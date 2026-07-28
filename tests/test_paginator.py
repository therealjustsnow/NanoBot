"""
tests/test_paginator.py
The shared listing navigation in utils/paginator.py, and the boards wired to it.

Every leaderboard was page-numbered with no way to turn a page — `page:2` was
the only way forward, which on a phone is the command retyped. The view is
generic, so it is tested twice over: once against a fake builder (arrow state,
switch behaviour, who may press), and once per real board to prove the command
actually hands its rendering over rather than replying flat.

dpytest can't dispatch components, so the buttons are driven by hand and the
commands are called with a duck-typed Context (the SellConfirmView pattern in
tests/test_inventory_commands.py).
"""

import types

import pytest

import utils.db as db
from tests.conftest import config
from utils import helpers as h
from utils import paginator


# ── Stubs ─────────────────────────────────────────────────────────────────────
class _CapturingCtx:
    """A duck-typed Context that records what a command replied with."""

    def __init__(self, author, guild):
        self.author, self.guild = author, guild
        self.kwargs: dict = {}

    async def defer(self, *a, **kw):
        return None

    async def reply(self, *args, **kwargs):
        self.kwargs = kwargs
        return types.SimpleNamespace(id=1, edit=self._edit)

    async def _edit(self, **kwargs):
        self.kwargs.update(kwargs)


class _FakeResponse:
    def __init__(self):
        self.sent = None

    async def defer(self, *a, **kw):
        return None

    async def send_message(self, **kwargs):
        self.sent = kwargs


class _FakeInteraction:
    """Minimal duck-typed Interaction for the arrows and the switch."""

    def __init__(self, user_id, guild=None):
        self.user = types.SimpleNamespace(id=user_id)
        self.guild = guild
        self.response = _FakeResponse()
        self.edited: dict = {}

    async def edit_original_response(self, **kwargs):
        self.edited = kwargs
        return None


def _fake_builder(pages: int, calls: list):
    """A builder that records what it was asked for and clamps like a real one."""

    async def build(page, choice):
        page = max(1, min(page, pages))
        calls.append((page, choice))
        return paginator.Page(h.embed("Board", f"page {page} of {choice}"), page, pages)

    return build


def _arrows(view):
    return {b.delta: b for b in view.children if getattr(b, "delta", None)}


def _switch_buttons(view):
    return {b.value: b for b in view.children if hasattr(b, "value")}


# ── The view ──────────────────────────────────────────────────────────────────
async def test_a_single_page_listing_gets_no_buttons():
    """A row of dead arrows under a one-page board is noise, not navigation."""
    ctx = _CapturingCtx(types.SimpleNamespace(id=7), None)
    view = await paginator.send(ctx, _fake_builder(1, []))
    assert view is None
    assert "view" not in ctx.kwargs
    assert ctx.kwargs["embed"]


async def test_arrows_report_where_you_are_and_where_you_can_go():
    ctx = _CapturingCtx(types.SimpleNamespace(id=7), None)
    view = await paginator.send(ctx, _fake_builder(3, []))

    assert {d: b.disabled for d, b in _arrows(view).items()} == {-1: True, 1: False}
    assert any(b.label == "1/3" for b in view.children)

    await _arrows(view)[1].callback(_FakeInteraction(7))
    await _arrows(view)[1].callback(_FakeInteraction(7))
    assert view.page == 3
    assert {d: b.disabled for d, b in _arrows(view).items()} == {-1: False, 1: True}
    assert any(b.label == "3/3" for b in view.children)


async def test_a_page_turn_re_asks_the_builder_rather_than_caching_rows():
    """A board can move between presses — the rows are re-read, not stored."""
    calls: list = []
    ctx = _CapturingCtx(types.SimpleNamespace(id=7), None)
    view = await paginator.send(ctx, _fake_builder(4, calls))

    interaction = _FakeInteraction(7)
    await _arrows(view)[1].callback(interaction)
    assert calls == [(1, None), (2, None)]
    assert "page 2" in interaction.edited["embed"].description


async def test_the_builders_own_clamp_wins():
    """The command owns what page 99 means; the view just displays the answer."""
    ctx = _CapturingCtx(types.SimpleNamespace(id=7), None)
    view = await paginator.send(ctx, _fake_builder(3, []), page=99)
    assert (view.page, view.pages) == (3, 3)
    assert _arrows(view)[1].disabled


async def test_a_two_option_switch_is_buttons_and_re_cuts_from_page_one():
    calls: list = []
    ctx = _CapturingCtx(types.SimpleNamespace(id=7), None)
    view = await paginator.send(
        ctx, _fake_builder(5, calls), page=3, switch=paginator.scope_switch("server")
    )
    buttons = _switch_buttons(view)
    assert buttons["server"].disabled and not buttons["global"].disabled

    await buttons["global"].callback(_FakeInteraction(7))
    # A different cut of the list starts at the top of it, not on page 3.
    assert calls[-1] == (1, "global")
    assert view.choice == "global"
    buttons = _switch_buttons(view)
    assert buttons["global"].disabled and not buttons["server"].disabled


async def test_a_switch_with_too_many_options_becomes_a_dropdown():
    calls: list = []
    switch = paginator.Switch(
        options=[(f"s{i}", f"Stat {i}") for i in range(6)], current="s0"
    )
    ctx = _CapturingCtx(types.SimpleNamespace(id=7), None)
    view = await paginator.send(ctx, _fake_builder(2, calls), switch=switch)

    select = [c for c in view.children if hasattr(c, "options")][0]
    assert [o.value for o in select.options] == [f"s{i}" for i in range(6)]
    assert [o.value for o in select.options if o.default] == ["s0"]

    select._refresh_state = lambda *a, **kw: None
    select._values = ["s4"]
    await select.callback(_FakeInteraction(7))
    assert calls[-1] == (1, "s4")
    assert view.choice == "s4"


async def test_an_arrow_keeps_the_cut_you_are_looking_at():
    """Paging a Global board must not silently drop you back to This server."""
    calls: list = []
    ctx = _CapturingCtx(types.SimpleNamespace(id=7), None)
    view = await paginator.send(
        ctx, _fake_builder(3, calls), switch=paginator.scope_switch("global")
    )
    await _arrows(view)[1].callback(_FakeInteraction(7))
    assert calls[-1] == (2, "global")


async def test_only_the_member_who_asked_can_press():
    ctx = _CapturingCtx(types.SimpleNamespace(id=7), None)
    view = await paginator.send(ctx, _fake_builder(3, []))

    stranger = _FakeInteraction(8)
    assert await view.interaction_check(stranger) is False
    assert stranger.response.sent is not None
    assert await view.interaction_check(_FakeInteraction(7)) is True


async def test_timing_out_leaves_the_list_readable_but_dead():
    ctx = _CapturingCtx(types.SimpleNamespace(id=7), None)
    view = await paginator.send(ctx, _fake_builder(3, []))
    await view.on_timeout()
    assert all(c.disabled for c in view.children)
    assert ctx.kwargs["view"] is view  # the embed is still on screen


# ── The boards ────────────────────────────────────────────────────────────────
# One entry per paginated listing: the command, its args, and whether it offers
# a second cut of the list. A board added later that replies flat fails here.
_BOARDS = [
    ("coin top", {"scope": "global"}, "scope"),
    ("coin contrib", {"scope": "global"}, "scope"),
    ("fish top", {"scope": "global"}, "scope"),
    ("fish global", {"stat": "earned"}, "stat"),
    ("casino top", {"scope": "global"}, "scope"),
    ("level top", {}, None),
]


async def _seed_boards(guild_id: int, how_many: int = 25):
    """Enough rows on every board that each one spans more than one page."""
    for i in range(1, how_many + 1):
        uid = 900_000 + i
        await db.add_coins(uid, 100 * i)
        await db.add_contribution(uid, 10 * i)
        await db.add_fishing_earned(uid, 50 * i)
        await db.record_casino_game(uid, wagered=10, payout=20 * i)
        await db.add_xp(guild_id, uid, 100 * i)


async def _run(bot, name: str, kwargs: dict):
    """Invoke a board command's callback with a recording Context."""
    author, guild = config().members[0], config().guilds[0]
    command = bot.get_command(name)
    assert command is not None, name
    ctx = _CapturingCtx(author, guild)
    await command.callback(command.cog, ctx, **kwargs)
    return ctx


@pytest.mark.cogs("cogs.economy", "cogs.fishing", "cogs.casino", "cogs.leveling")
async def test_every_board_hands_its_pages_to_the_browser(bot):
    """The point of the change: no board still answers with a flat embed and a
    `page:` option as the only way forward."""
    guild = config().guilds[0]
    await _seed_boards(guild.id)

    for name, kwargs, dimension in _BOARDS:
        ctx = await _run(bot, name, kwargs)
        view = ctx.kwargs.get("view")
        assert isinstance(view, paginator.PagedView), f"{name} replied flat"
        assert view.pages > 1, f"{name} should have spanned several pages"
        assert _arrows(view), f"{name} has no page arrows"
        if dimension is None:
            assert view.switch is None, f"{name} shouldn't offer a second view"
        else:
            assert view.switch is not None, f"{name} lost its {dimension} switch"


@pytest.mark.cogs("cogs.economy", "cogs.fishing", "cogs.casino", "cogs.leveling")
async def test_every_board_turns_its_own_pages(bot):
    """Pressing ▶️ has to reach that board's real rows, not a generic renderer."""
    guild, author = config().guilds[0], config().members[0]
    await _seed_boards(guild.id)

    for name, kwargs, _ in _BOARDS:
        ctx = await _run(bot, name, kwargs)
        view = ctx.kwargs["view"]
        interaction = _FakeInteraction(author.id, guild)
        await _arrows(view)[1].callback(interaction)
        assert view.page == 2, name
        assert "Page 2/" in interaction.edited["embed"].footer.text, name


@pytest.mark.cogs("cogs.economy")
async def test_a_scope_switch_swaps_which_wallets_are_ranked(bot):
    """The two scopes are a real choice (the rows differ), which is exactly why
    it earns a button instead of another re-run."""
    guild, author = config().guilds[0], config().members[0]
    await _seed_boards(guild.id)
    # A member of this server, poorer than the seeded global crowd.
    await db.add_coins(author.id, 5)

    ctx = await _run(bot, "coin top", {"scope": "server"})
    view = ctx.kwargs["view"]
    assert view.switch.current == "server"
    assert "members here" in ctx.kwargs["embed"].footer.text

    interaction = _FakeInteraction(author.id, guild)
    await _switch_buttons(view)["global"].callback(interaction)
    assert view.choice == "global"
    assert "every server" in interaction.edited["embed"].footer.text
    assert view.pages > 1


@pytest.mark.cogs("cogs.fishing")
async def test_the_global_fishing_board_switches_stat_from_its_dropdown(bot):
    guild, author = config().guilds[0], config().members[0]
    await _seed_boards(guild.id)
    for i in range(1, 26):
        await db.add_fishing_xp(900_000 + i, 30 * i)

    ctx = await _run(bot, "fish global", {"stat": "earned"})
    view = ctx.kwargs["view"]
    assert "Earnings" in ctx.kwargs["embed"].title
    select = [c for c in view.children if hasattr(c, "options")][0]

    interaction = _FakeInteraction(author.id, guild)
    select._refresh_state = lambda *a, **kw: None
    select._values = ["xp"]
    await select.callback(interaction)
    assert view.choice == "xp"
    # A different stat is a different ranking, not just a different label.
    assert interaction.edited["embed"].title != ctx.kwargs["embed"].title


@pytest.mark.cogs("cogs.economy")
async def test_an_empty_board_still_offers_the_other_scope(bot):
    """A fresh server's board is empty, and the global one usually isn't — so
    the switch has to survive the "nothing here yet" screen."""
    ctx = await _run(bot, "coin top", {"scope": "server"})
    view = ctx.kwargs["view"]
    assert isinstance(view, paginator.PagedView)
    assert view.pages == 1 and not _arrows(view)
    assert _switch_buttons(view)["global"].disabled is False
