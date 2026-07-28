"""
tests/test_cosmetic_shop.py
The cosmetic shop: the catalogue rules that make it a real sink, and the
/shop unlock purchase path end to end.

The load-bearing rule is that a `purchase` cosmetic is *never* unlocked by
playing. If one ever became auto-unlockable it would stop costing anything and
the sink would quietly become a giveaway, so that is asserted from both ends —
the rule evaluator and the lazy-unlock helper the profile card uses.
"""

import os
import types

import pytest
from discord.ext import test as dpytest
from PIL import ImageFont

import utils.db as db
from cogs.identity.helpers import interleave_by_slot, newly_unlocked, unlock_context
from tests.conftest import config
from utils import cosmetics, profile_card


# ── Catalogue ─────────────────────────────────────────────────────────────────
def test_every_purchasable_cosmetic_has_a_price_and_a_real_slot():
    stock = cosmetics.purchasable()
    assert stock, "the shop should not be empty"
    for d in stock:
        assert d.price > 0, f"{d.key} is for sale for nothing"
        assert d.slot in cosmetics.SLOTS
        assert d.category in cosmetics.CATEGORIES
        assert d.description, f"{d.key} has no shop description"


def test_purchasable_is_sorted_cheapest_first():
    prices = [d.price for d in cosmetics.purchasable()]
    assert prices == sorted(prices)


def test_both_aisles_have_stock():
    for category in cosmetics.CATEGORIES:
        assert cosmetics.purchasable(category), f"{category} aisle is empty"


def test_a_shop_cosmetic_is_never_unlocked_by_playing():
    """The whole sink rests on this: no amount of levels, prestige, stats or
    achievements may hand over something the shop sells."""
    maxed = unlock_context(
        global_level=999,
        prestige=10,
        achievements={d.key for d in cosmetics.COSMETICS.values()},
        stats={key: 10**12 for key in ("balance", "fish_caught", "casino_games")},
    )
    for d in cosmetics.purchasable():
        assert not cosmetics.is_unlocked(d, maxed), f"{d.key} unlocked itself"
    assert not [d for d in newly_unlocked(set(), maxed) if d.price]
    assert not [d for d in cosmetics.auto_unlockable() if d.price]


def test_coin_glyphs_render_in_the_bundled_font():
    """A missing glyph draws as a tofu box, and the coin is the middle of the
    wallet card — catch it here rather than on someone's balance."""
    path = next((p for p in profile_card._FONT_CANDIDATES if os.path.exists(p)), None)
    if path is None:  # pragma: no cover - bare container without fonts
        pytest.skip("no TrueType font available")
    font = ImageFont.truetype(path, 40)
    missing = bytes(font.getmask("￿"))
    bad = [
        d.key
        for d in cosmetics.in_slot("coin")
        if bytes(font.getmask(d.glyph)) == missing
    ]
    assert not bad, f"coin glyphs not covered by the font: {bad}"


def test_every_declared_texture_pattern_and_style_is_implemented():
    """A typo in a def's `texture`/`pattern` would silently fall back to the
    default look rather than failing, so registry membership is checked."""
    for d in cosmetics.COSMETICS.values():
        assert d.texture in profile_card._TEXTURES, (d.key, d.texture)
        assert d.pattern in profile_card._BANNER_PATTERNS, (d.key, d.pattern)
        assert d.style in profile_card._BORDER_STYLES, (d.key, d.style)


def test_interleave_by_slot_gives_every_slot_a_turn():
    pairs = [("badge", f"b{i}") for i in range(5)] + [("banner", "n1"), ("coin", "c1")]
    out = interleave_by_slot(pairs)
    assert out[:3] == ["b0", "n1", "c1"]
    assert len(out) == len(pairs)
    assert interleave_by_slot([]) == []


# ── /shop unlock ──────────────────────────────────────────────────────────────
_ITEM = "banner_sunset"  # 12,000 coins


def _clear_cooldown(bot, qualified_name: str):
    """dpytest replays messages faster than a human can; the per-user command
    cooldown is not what any of these are testing."""
    bot.get_command(qualified_name)._buckets._cache.clear()


@pytest.mark.cogs("cogs.economy")
async def test_unlock_charges_once_and_grants_the_cosmetic(bot):
    author = config().members[0]
    price = cosmetics.get(_ITEM).price
    await db.add_coins(author.id, price + 500)

    await dpytest.message(f"!shop unlock {_ITEM}", member=author)
    assert dpytest.get_message().embeds
    assert _ITEM in await db.get_unlocked_cosmetics(author.id)
    assert await db.get_balance(author.id) == 500

    # Buying it again is refused before any coins move.
    _clear_cooldown(bot, "shop unlock")
    await dpytest.message(f"!shop unlock {_ITEM}", member=author)
    dpytest.get_message()
    assert await db.get_balance(author.id) == 500


@pytest.mark.cogs("cogs.economy")
async def test_unlock_refuses_when_you_cannot_afford_it(bot):
    author = config().members[1]
    await db.add_coins(author.id, 10)

    await dpytest.message(f"!shop unlock {_ITEM}", member=author)
    embed = dpytest.get_message().embeds[0]
    assert "short" in embed.description
    assert await db.get_balance(author.id) == 10  # nothing was taken
    assert _ITEM not in await db.get_unlocked_cosmetics(author.id)


@pytest.mark.cogs("cogs.economy")
async def test_unlock_refuses_something_that_is_not_for_sale(bot):
    """An earned cosmetic can't be bought — that would undo the achievement."""
    author = config().members[0]
    await db.add_coins(author.id, 10_000_000)

    await dpytest.message("!shop unlock banner_ember", member=author)
    embed = dpytest.get_message().embeds[0]
    assert "isn't for sale" in embed.description
    assert "banner_ember" not in await db.get_unlocked_cosmetics(author.id)
    assert await db.get_balance(author.id) == 10_000_000


@pytest.mark.cogs("cogs.economy")
async def test_unlock_rejects_an_unknown_name(bot):
    author = config().members[0]
    await dpytest.message("!shop unlock definitely not a cosmetic", member=author)
    assert dpytest.get_message().embeds


# ── The aisles ────────────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.economy")
async def test_the_hub_lists_every_aisle(bot):
    author = config().members[0]
    await dpytest.message("!shop", member=author)
    embed = dpytest.get_message().embeds[0]
    names = " ".join(f.name for f in embed.fields)
    for expected in ("Profile", "Wallet", "Server"):
        assert expected in names, names


class _CapturingCtx:
    """A duck-typed Context that records what a command replied with.

    dpytest reads a send's JSON payload, which is empty for a *multipart*
    request — so once a reply carries a file, its embed and content are
    invisible to `dpytest.get_message()`. The aisle now attaches a preview
    image, so its embed has to be inspected here instead. (Same reason
    tests/test_inventory_commands.py drives SellConfirmView by hand.)
    """

    def __init__(self, author, guild):
        self.author, self.guild = author, guild
        self.kwargs: dict = {}

    async def defer(self, *a, **kw):
        return None

    async def reply(self, *args, **kwargs):
        self.kwargs = kwargs
        return None


@pytest.mark.cogs("cogs.economy")
async def test_each_cosmetic_aisle_marks_what_you_can_afford(bot):
    author = config().members[0]
    guild = config().guilds[0]
    await db.add_coins(author.id, 6_000)  # enough for the cheapest, not the rest
    cog = bot.get_cog("Economy")

    for aisle in ("profile", "wallet"):
        ctx = _CapturingCtx(author, guild)
        await cog._show_cosmetics(ctx, aisle, 1)
        embed = ctx.kwargs["embed"]
        marks = " ".join(f.name for f in embed.fields)
        assert "🟢" in marks or "🔒" in marks, marks
        assert "/profile equip" in embed.footer.text


@pytest.mark.cogs("cogs.economy")
async def test_an_aisle_shows_the_art_it_is_selling(bot):
    """The answer to "what am I actually buying?" — a name and a price don't
    tell anyone what a banner looks like."""
    author = config().members[0]
    guild = config().guilds[0]
    cog = bot.get_cog("Economy")

    ctx = _CapturingCtx(author, guild)
    await cog._show_cosmetics(ctx, "profile", 1)

    file = ctx.kwargs["file"]
    assert file.filename.endswith(f".{profile_card.IMAGE_EXT}")
    # The embed must point *at that attachment*, or Discord renders the preview
    # as a loose image below the listing instead of inside it.
    assert ctx.kwargs["embed"].image.url == f"attachment://{file.filename}"
    assert len(file.fp.getvalue()) > 1000, "the preview should be a real render"


@pytest.mark.cogs("cogs.economy")
async def test_an_aisle_still_replies_end_to_end(bot):
    """The stub-context tests above bypass dispatch, so one real invocation
    keeps the command wiring itself covered."""
    author = config().members[0]
    await dpytest.message("!shop profile", member=author)
    assert dpytest.get_message().attachments


def test_preview_captions_never_contain_tofu():
    """The bundled font has no colour emoji, so anything drawn from a name or a
    caption is filtered — a 🟢 in a caption renders as an empty box."""
    assert profile_card._font_safe("🟢 Neon Glow ✅") == "Neon Glow"
    assert profile_card._font_safe("The Great Wave off Kanagawa")
    for d in cosmetics.COSMETICS.values():
        assert profile_card._font_safe(d.name), f"{d.key}'s name is undrawable"


def test_preview_sheet_marks_affordability_by_colour(bot=None):
    """Affordability has to survive the no-emoji rule, so it is a colour."""
    from PIL import Image

    d = cosmetics.purchasable()[0]
    cheap = Image.open(
        __import__("io").BytesIO(
            profile_card.preview_sheet([(d, d.name, "1 coin", True)], columns=1)
        )
    ).convert("RGB")
    dear = Image.open(
        __import__("io").BytesIO(
            profile_card.preview_sheet([(d, d.name, "1 coin", False)], columns=1)
        )
    ).convert("RGB")
    assert cheap.tobytes() != dear.tobytes()


def test_preview_sheet_renders_every_slot_type():
    """Each slot has to preview as something recognisable: a banner is its own
    art, but a border would otherwise be an empty rectangle and a nameplate an
    invisible one."""
    from PIL import Image

    picks = [d for slot in cosmetics.SLOTS for d in cosmetics.in_slot(slot)[:1]]
    raw = profile_card.preview_sheet([(d, d.name, "1 coin") for d in picks])
    sheet = Image.open(__import__("io").BytesIO(raw))
    assert sheet.format == profile_card.IMAGE_FORMAT
    # Two columns, so the sheet grows in rows rather than off the side.
    assert sheet.width < sheet.height
    assert profile_card.preview_sheet([])  # never raises on an empty page


@pytest.mark.cogs("cogs.economy")
async def test_the_unlock_picker_leads_with_what_you_can_afford(bot):
    """Nobody types `banner_sakura` on a phone; the picker also has to say
    which rows are actually buyable right now."""
    guild = bot.guilds[0]
    user = guild.members[0]
    cog = bot.get_cog("Economy")
    interaction = types.SimpleNamespace(guild_id=guild.id, guild=guild, user=user)

    broke = await cog._shop_unlock_ac(interaction, "")
    assert broke, "the picker must never come back empty"
    assert all(c.name.startswith("🔒") for c in broke)
    assert all(len(c.name) <= 100 for c in broke)

    cheapest = cosmetics.purchasable()[0]
    await db.add_coins(user.id, cheapest.price)
    choices = await cog._shop_unlock_ac(interaction, "")
    assert choices[0].value == cheapest.key
    assert choices[0].name.startswith("🟢")

    # Owned ones sink below everything buyable (past the 25-row cut, on a big
    # catalogue — so this looks it up by name rather than in the top slice).
    await db.unlock_cosmetic(user.id, cheapest.key, at=0)
    rows = await cog._shop_unlock_ac(interaction, cheapest.name.lower())
    owned_row = next(c for c in rows if c.value == cheapest.key)
    assert owned_row.name.startswith("✅")
    assert rows.index(owned_row) == len(rows) - 1

    # And typing filters by name, key, slot or aisle.
    assert all(
        cosmetics.get(c.value).category == "wallet"
        for c in await cog._shop_unlock_ac(interaction, "wallet")
    )


@pytest.mark.cogs("cogs.economy")
async def test_the_server_aisle_still_answers_to_its_old_name(bot):
    """`/shop list` was the guild shop for a long time; the prefix alias keeps
    working after the rename to `/shop server`."""
    guild, author = config().guilds[0], config().members[0]
    await db.add_shop_item(guild.id, "VIP", 5_000, "custom", payload="a perk")

    for invocation in ("!shop server", "!shop list"):
        _clear_cooldown(bot, "shop server")
        await dpytest.message(invocation, member=author)
        embed = dpytest.get_message().embeds[0]
        assert any("VIP" in f.name for f in embed.fields)


# ── The browser ───────────────────────────────────────────────────────────────
# Every aisle was page-numbered with no way to turn a page: `/shop profile
# page:5` to move one screen is the whole command retyped on a phone. dpytest
# can't dispatch components, so the buttons are driven by hand (the
# SellConfirmView pattern in tests/test_inventory_commands.py).
class _FakeResponse:
    def __init__(self):
        self.sent = None

    async def defer(self, *a, **kw):
        return None

    async def send_message(self, **kwargs):
        self.sent = kwargs


class _FakeInteraction:
    """Minimal duck-typed Interaction for the /shop page + aisle buttons."""

    def __init__(self, user_id, guild):
        self.user = types.SimpleNamespace(id=user_id)
        self.guild = guild
        self.response = _FakeResponse()
        self.edited: dict = {}

    async def edit_original_response(self, **kwargs):
        self.edited = kwargs
        return None


def _browser(ctx):
    return ctx.kwargs["view"]


def _labels(view):
    return {b.label for b in view.children if b.label}


@pytest.mark.cogs("cogs.economy")
async def test_every_aisle_ships_a_switcher_and_page_arrows(bot):
    author, guild = config().members[0], config().guilds[0]
    cog = bot.get_cog("Economy")
    ctx = _CapturingCtx(author, guild)
    await cog._show_cosmetics(ctx, "profile", 1)

    view = _browser(ctx)
    # Every aisle is one tap away, and the one being viewed isn't pressable.
    assert {"Shop", "Profile", "Wallet", "Server"} <= _labels(view)
    current = [b for b in view.children if getattr(b, "aisle", None) == "profile"][0]
    assert current.disabled
    # Page 1 of many: back is dead, forward is live, and the count is on screen.
    arrows = [b for b in view.children if getattr(b, "delta", None)]
    assert {b.delta: b.disabled for b in arrows} == {-1: True, 1: False}
    assert any(b.label == f"1/{view.pages}" for b in view.children)


@pytest.mark.cogs("cogs.economy")
async def test_the_forward_arrow_turns_the_page_and_swaps_the_art(bot):
    author, guild = config().members[0], config().guilds[0]
    cog = bot.get_cog("Economy")
    ctx = _CapturingCtx(author, guild)
    await cog._show_cosmetics(ctx, "profile", 1)
    view = _browser(ctx)
    assert view.pages > 1, "the profile aisle should span more than one page"
    first = ctx.kwargs["file"].filename

    interaction = _FakeInteraction(author.id, guild)
    await [b for b in view.children if getattr(b, "delta", None) == 1][0].callback(
        interaction
    )

    assert view.page == 2
    assert "Page 2/" in interaction.edited["embed"].footer.text
    # A fresh attachment, under a page-specific name: a same-named one on an
    # edited message can be served from the client's cache, leaving page 2
    # showing page 1's art.
    turned = interaction.edited["attachments"][0]
    assert turned.filename != first
    assert interaction.edited["embed"].image.url == f"attachment://{turned.filename}"
    assert len(turned.fp.getvalue()) > 1000
    # And back is pressable now that there's something behind us.
    assert not [b for b in view.children if getattr(b, "delta", None) == -1][0].disabled


@pytest.mark.cogs("cogs.economy")
async def test_a_scrolled_page_is_not_re_rendered(bot):
    """Turning back to a page already seen shouldn't pay for the render again."""
    author, guild = config().members[0], config().guilds[0]
    cog = bot.get_cog("Economy")
    ctx = _CapturingCtx(author, guild)
    await cog._show_cosmetics(ctx, "profile", 1)
    view = _browser(ctx)
    forward = [b for b in view.children if getattr(b, "delta", None) == 1][0]

    await forward.callback(_FakeInteraction(author.id, guild))
    art = dict(view._art)
    assert ("profile", 2) in art

    calls = []
    real = profile_card.preview_sheet
    profile_card.preview_sheet = lambda *a, **kw: (calls.append(1), real(*a, **kw))[1]
    try:
        back = [b for b in view.children if getattr(b, "delta", None) == -1][0]
        await back.callback(_FakeInteraction(author.id, guild))
        assert view.page == 1
        assert calls == [], "page 1's sheet was rendered a second time"
    finally:
        profile_card.preview_sheet = real


@pytest.mark.cogs("cogs.economy")
async def test_the_switcher_opens_another_aisle_at_its_first_page(bot):
    author, guild = config().members[0], config().guilds[0]
    await db.add_shop_item(guild.id, "VIP", 5_000, "custom", payload="a perk")
    cog = bot.get_cog("Economy")
    ctx = _CapturingCtx(author, guild)
    await cog._show_cosmetics(ctx, "profile", 2)
    view = _browser(ctx)

    interaction = _FakeInteraction(author.id, guild)
    server_btn = [b for b in view.children if getattr(b, "aisle", None) == "server"][0]
    await server_btn.callback(interaction)

    assert (view.aisle, view.page) == ("server", 1)
    assert any("VIP" in f.name for f in interaction.edited["embed"].fields)
    # The cosmetic sheet has to go with the listing it described.
    assert interaction.edited["attachments"] == []


@pytest.mark.cogs("cogs.economy")
async def test_the_hub_is_an_aisle_of_the_browser(bot):
    """The hub is reachable from any aisle, and has nothing to page through."""
    author, guild = config().members[0], config().guilds[0]
    cog = bot.get_cog("Economy")
    ctx = _CapturingCtx(author, guild)
    await cog._show_hub(ctx)
    view = _browser(ctx)

    assert view.pages == 1
    assert not [b for b in view.children if getattr(b, "delta", None)]
    interaction = _FakeInteraction(author.id, guild)
    profile_btn = [b for b in view.children if getattr(b, "aisle", None) == "profile"][
        0
    ]
    await profile_btn.callback(interaction)
    assert view.aisle == "profile"
    assert interaction.edited["attachments"], "the aisle previews what it sells"


@pytest.mark.cogs("cogs.economy")
async def test_only_the_member_who_asked_can_browse(bot):
    """The listing is personalised — it counts your coins and marks what you
    own — so someone else's press would repaint it with the wrong balance."""
    author, other, guild = config().members[0], config().members[1], config().guilds[0]
    cog = bot.get_cog("Economy")
    ctx = _CapturingCtx(author, guild)
    await cog._show_cosmetics(ctx, "profile", 1)
    view = _browser(ctx)

    interaction = _FakeInteraction(other.id, guild)
    assert await view.interaction_check(interaction) is False
    assert interaction.response.sent is not None
    assert await view.interaction_check(_FakeInteraction(author.id, guild)) is True


@pytest.mark.cogs("cogs.economy")
async def test_a_page_past_the_end_lands_on_the_last_one(bot):
    author, guild = config().members[0], config().guilds[0]
    cog = bot.get_cog("Economy")
    ctx = _CapturingCtx(author, guild)
    await cog._show_cosmetics(ctx, "profile", 999)
    view = _browser(ctx)

    assert view.page == view.pages
    assert [b for b in view.children if getattr(b, "delta", None) == 1][0].disabled
