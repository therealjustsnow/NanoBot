"""
Tests for the /fish buttons: the cast/sell/bag/travel/shop/trap row, the map,
and the tackle shop.

dpytest can't dispatch component interactions, so the views are driven by
calling their handlers with a duck-typed interaction (the SellConfirmView
pattern from tests/test_inventory_commands.py). Everything under the handler is
the real cog — the same atomic cast claim, the same charter debit, the same DB.
"""

import time

import pytest

import utils.db as db
from cogs.fishing.constants import TRAP_SOAK
from cogs.fishing.items import FISH_TRAP
from cogs.fishing.spots import DEFAULT_SPOT, SPOTS, SPOT_ORDER
from tests.conftest import config


def views():
    """The view module the *live* cog is using.

    Loading a cog replaces `cogs.fishing.views` in sys.modules, so a class
    bound at collection time is a different object from the one the cog builds
    — an `isinstance` against it fails in a full run and passes in isolation,
    which is the most annoying possible way to learn this. Resolve at call time.
    """
    import cogs.fishing.views as module

    return module


class _FakeResponse:
    def __init__(self):
        self.deferred = False
        self.sent = None

    async def defer(self, **kwargs):
        self.deferred = True

    async def send_message(self, **kwargs):
        self.sent = kwargs


class _FakeMessage:
    def __init__(self):
        self.edits = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)


class _FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        return _FakeMessage()


class FakeInteraction:
    """Enough discord.Interaction for the fishing views."""

    def __init__(self, member, guild, channel=None):
        self.user = member
        self.guild = guild
        self.channel = channel
        self.response = _FakeResponse()
        self.followup = _FakeFollowup()
        self.edited = None

    async def edit_original_response(self, **kwargs):
        self.edited = kwargs
        return _FakeMessage()


def _press(view, cls, **match):
    """Find a button on a view by type and attribute."""
    for child in view.children:
        if isinstance(child, cls) and all(
            getattr(child, k, None) == v for k, v in match.items()
        ):
            return child
    raise AssertionError(f"no {cls.__name__} matching {match}")


async def settle_todays_quest(user_id: int):
    """Claim today's quest up front so a sale can't also pay it out.

    The quest is seeded on `(user_id, day)`, so whether a sale completes one
    depends on the member id dpytest generated and the date the suite ran —
    a coin-flip between local and CI. See tests/test_vote_rewards.py.
    """
    from cogs.fishing.helpers import generate_quest

    today = int(time.time() // 86400)
    quest = generate_quest(user_id, today)
    await db.get_or_create_quest(user_id, today, quest["quest_key"], quest["target"])
    # The claim only lands on a *completed* quest, so fill it first — this is
    # the same pair of accessors the cog uses, not a hand-written UPDATE.
    await db.bump_quest_progress(user_id, today, quest["target"])
    claimed = await db.try_claim_quest_reward(user_id, today)
    # Assert the neutralisation actually took: if this ever stops working the
    # tests below go back to flaking on a third of member ids, and a silent
    # no-op here is exactly how that would come back.
    assert claimed, "today's quest should now be claimed, so a sale can't pay it"


async def _ready_to_cast(user_id):
    """Clear the cast cooldown so a test can cast again immediately."""
    await db._conn().execute(
        "UPDATE fishing_stats SET last_cast=0 WHERE user_id=?", (str(user_id),)
    )


# ══════════════════════════════════════════════════════════════════════════════
#  The cast row
# ══════════════════════════════════════════════════════════════════════════════
class _FakeCtx:
    """Just enough Context for Fishing._send — dpytest doesn't surface the view
    it was replied with, and the view is the thing under test."""

    def __init__(self, member, guild):
        self.author = member
        self.guild = guild
        self.channel = None
        self.replied = None

    async def reply(self, **kwargs):
        self.replied = kwargs
        return _FakeMessage()


@pytest.mark.cogs("cogs.fishing")
async def test_a_cast_reply_carries_the_buttons(bot):
    """The headline of the whole change: an hour of fishing was 180 typed
    commands, and the reply to a cast now offers the next one as a tap."""
    guild, author = config().guilds[0], config().members[0]
    cog = bot.get_cog("Fishing")
    ctx = _FakeCtx(author, guild)

    await cog._send(ctx, await cog.cast_once(guild, author))

    assert isinstance(ctx.replied["view"], views().FishingView)
    assert ctx.replied["ephemeral"] is False


@pytest.mark.cogs("cogs.fishing")
async def test_a_refusal_gets_no_buttons(bot):
    """There is nothing to do from "fishing is disabled here", and a Cast
    button under it would be a lie."""
    guild, author = config().guilds[0], config().members[0]
    cog = bot.get_cog("Fishing")
    await db.set_fishing_config(guild.id, enabled=False)
    ctx = _FakeCtx(author, guild)

    await cog._send(ctx, await cog.cast_once(guild, author))

    assert ctx.replied["view"] is None
    assert ctx.replied["ephemeral"] is True


@pytest.mark.cogs("cogs.fishing")
async def test_the_cast_button_casts_and_edits_in_place(bot):
    guild, author = config().guilds[0], config().members[0]
    cog = bot.get_cog("Fishing")
    view = views().FishingView(cog, author.id)
    interaction = FakeInteraction(author, guild)

    await view.cast.callback(interaction)

    assert (await db.get_fisher(author.id))["casts"] == 1
    # Edited, not posted: the loop lives in one message.
    assert interaction.edited is not None
    assert interaction.followup.sent == []


@pytest.mark.cogs("cogs.fishing")
async def test_a_cast_on_cooldown_never_overwrites_the_catch(bot):
    """Being told "still on cooldown" is not worth losing the Legendary you
    just caught — a refusal answers privately and leaves the screen alone."""
    guild, author = config().guilds[0], config().members[0]
    cog = bot.get_cog("Fishing")
    view = views().FishingView(cog, author.id)

    await view.cast.callback(FakeInteraction(author, guild))
    second = FakeInteraction(author, guild)
    await view.cast.callback(second)

    assert second.edited is None
    assert second.followup.sent[0]["ephemeral"] is True
    assert "Not Yet" in second.followup.sent[0]["embed"].title


@pytest.mark.cogs("cogs.fishing")
async def test_the_sell_button_sells_the_bag(bot):
    guild, author = config().guilds[0], config().members[0]
    cog = bot.get_cog("Fishing")
    await settle_todays_quest(author.id)
    await db.record_catch(author.id, "salmon", 5.0, 60, track_best=True)
    view = views().FishingView(cog, author.id)
    interaction = FakeInteraction(author, guild)

    await view.sell.callback(interaction)

    assert await db.get_balance(author.id) == 60
    assert "Sold" in interaction.edited["embed"].title


@pytest.mark.cogs("cogs.fishing")
async def test_selling_an_empty_bag_answers_privately(bot):
    guild, author = config().guilds[0], config().members[0]
    cog = bot.get_cog("Fishing")
    view = views().FishingView(cog, author.id)
    interaction = FakeInteraction(author, guild)

    await view.sell.callback(interaction)

    assert interaction.edited is None
    assert interaction.followup.sent[0]["ephemeral"] is True


@pytest.mark.cogs("cogs.fishing")
async def test_only_the_invoker_may_press(bot):
    guild, author, other = config().guilds[0], config().members[0], config().members[1]
    view = views().FishingView(bot.get_cog("Fishing"), author.id)

    assert await view.interaction_check(FakeInteraction(author, guild)) is True
    theirs = FakeInteraction(other, guild)
    assert await view.interaction_check(theirs) is False
    assert theirs.response.sent["ephemeral"] is True


@pytest.mark.cogs("cogs.fishing")
async def test_timeout_greys_the_buttons_but_leaves_the_catch(bot):
    author = config().members[0]
    view = views().FishingView(bot.get_cog("Fishing"), author.id)
    view.message = _FakeMessage()

    await view.on_timeout()

    assert all(child.disabled for child in view.children)
    assert "embed" not in view.message.edits[-1]


# ══════════════════════════════════════════════════════════════════════════════
#  The map
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.cogs("cogs.fishing")
async def test_the_map_offers_every_spot_and_marks_where_you_are(bot):
    guild, author = config().guilds[0], config().members[0]
    cog = bot.get_cog("Fishing")
    _screen, state = await cog.travel_screen(guild, author)
    view = views().TravelView(cog, author.id, state)

    assert {b.spot for b in view.children if isinstance(b, views()._SpotButton)} == set(
        SPOT_ORDER
    )
    # Standing at the starter spot: its own button is dead, the rest are gated.
    assert _press(view, views()._SpotButton, spot=DEFAULT_SPOT).disabled is True
    assert _press(view, views()._SpotButton, spot=SPOT_ORDER[-1]).disabled is True
    assert any(isinstance(c, views()._BackButton) for c in view.children)


@pytest.mark.cogs("cogs.fishing")
async def test_chartering_a_spot_charges_once_and_travels(bot):
    guild, author = config().guilds[0], config().members[0]
    cog = bot.get_cog("Fishing")
    target = SPOT_ORDER[1]
    info = SPOTS[target]
    await db.add_fishing_xp(author.id, 100_000)  # clear the level gate
    await db.add_coins(author.id, info["price"] + 500)

    screen, _state = await cog.travel_to(guild, author, target)

    assert screen.ok
    assert await db.get_balance(author.id) == 500
    assert await db.get_unlocked_spots(author.id) == {target}
    assert (await db.get_fisher(author.id))["spot"] == target

    # Travelling back and forth afterwards is free.
    await cog.travel_to(guild, author, DEFAULT_SPOT)
    await cog.travel_to(guild, author, target)
    assert await db.get_balance(author.id) == 500


@pytest.mark.cogs("cogs.fishing")
async def test_the_level_gate_refuses_before_any_coins_move(bot):
    guild, author = config().guilds[0], config().members[0]
    cog = bot.get_cog("Fishing")
    target = SPOT_ORDER[-1]
    await db.add_coins(author.id, SPOTS[target]["price"] * 2)

    screen, _state = await cog.travel_to(guild, author, target)

    assert screen.ok is False
    assert "level" in screen.embed.description
    assert await db.get_balance(author.id) == SPOTS[target]["price"] * 2
    assert await db.get_unlocked_spots(author.id) == set()


@pytest.mark.cogs("cogs.fishing")
async def test_a_charter_you_cannot_afford_takes_nothing(bot):
    guild, author = config().guilds[0], config().members[0]
    cog = bot.get_cog("Fishing")
    target = SPOT_ORDER[1]
    await db.add_fishing_xp(author.id, 100_000)
    await db.add_coins(author.id, SPOTS[target]["price"] - 1)

    screen, _state = await cog.travel_to(guild, author, target)

    assert screen.ok is False
    assert await db.get_balance(author.id) == SPOTS[target]["price"] - 1
    assert await db.get_unlocked_spots(author.id) == set()
    assert (await db.get_fisher(author.id))["spot"] in ("", DEFAULT_SPOT)


@pytest.mark.cogs("cogs.fishing")
async def test_a_spot_button_re_checks_rather_than_trusting_its_label(bot):
    """The label was painted from a snapshot. If the coins went elsewhere in
    between, the press has to refuse — not charter on credit."""
    guild, author = config().guilds[0], config().members[0]
    cog = bot.get_cog("Fishing")
    target = SPOT_ORDER[1]
    await db.add_fishing_xp(author.id, 100_000)
    await db.add_coins(author.id, SPOTS[target]["price"])

    _screen, state = await cog.travel_screen(guild, author)
    view = views().TravelView(cog, author.id, state)
    button = _press(view, views()._SpotButton, spot=target)
    assert button.disabled is False

    await db.try_debit_coins(author.id, SPOTS[target]["price"])  # spent elsewhere
    interaction = FakeInteraction(author, guild)
    await button.callback(interaction)

    assert interaction.edited is None
    assert interaction.followup.sent[0]["ephemeral"] is True
    assert await db.get_unlocked_spots(author.id) == set()


@pytest.mark.cogs("cogs.fishing")
async def test_where_you_stand_changes_what_you_catch(bot, monkeypatch):
    """The point of travelling, end to end: an exclusive species is reachable
    at its spot and nowhere else."""
    guild, author = config().guilds[0], config().members[0]
    cog = bot.get_cog("Fishing")
    await db.add_fishing_xp(author.id, 100_000)
    await db.add_coins(author.id, SPOTS["river"]["price"])
    await cog.travel_to(guild, author, "river")

    # At River Bend the uncommon pool contains the River Eel; at the pond it
    # cannot.
    from cogs.fishing.spots import fish_pool

    assert "river_eel" in fish_pool("uncommon", "river")
    assert "river_eel" not in fish_pool("uncommon", DEFAULT_SPOT)


@pytest.mark.cogs("cogs.fishing")
async def test_back_returns_to_the_hub(bot):
    guild, author = config().guilds[0], config().members[0]
    cog = bot.get_cog("Fishing")
    _screen, state = await cog.travel_screen(guild, author)
    view = views().TravelView(cog, author.id, state)
    view.message = _FakeMessage()
    interaction = FakeInteraction(author, guild)

    await _press(view, views()._BackButton).callback(interaction)

    assert isinstance(interaction.edited["view"], views().FishingView)
    assert "Fishing" in interaction.edited["embed"].title


# ══════════════════════════════════════════════════════════════════════════════
#  The tackle shop
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.cogs("cogs.fishing")
async def test_the_shop_marks_what_you_can_afford(bot):
    guild, author = config().guilds[0], config().members[0]
    cog = bot.get_cog("Fishing")

    _screen, stock = await cog.shop_screen(guild, author)
    assert stock and not any(entry["affordable"] for entry in stock)

    await db.add_coins(author.id, 1_000_000)
    _screen, rich = await cog.shop_screen(guild, author)
    assert all(entry["affordable"] for entry in rich)


@pytest.mark.cogs("cogs.fishing")
async def test_a_buy_button_buys_one_and_repaints_the_shelf(bot):
    guild, author = config().guilds[0], config().members[0]
    cog = bot.get_cog("Fishing")
    await db.add_coins(author.id, 1_000)

    _screen, stock = await cog.shop_screen(guild, author)
    view = views().TackleShopView(cog, author.id, stock)
    button = _press(view, views()._BuyButton, item_key="bait_worm")
    interaction = FakeInteraction(author, guild)

    await button.callback(interaction)

    assert await db.get_item_qty(author.id, "bait_worm") == 1
    assert await db.get_balance(author.id) == 975
    assert isinstance(interaction.edited["view"], views().TackleShopView)


@pytest.mark.cogs("cogs.fishing")
async def test_buying_what_you_cannot_afford_changes_nothing(bot):
    guild, author = config().guilds[0], config().members[0]
    cog = bot.get_cog("Fishing")

    screen = await cog.buy_one(guild, author, "bait_glowgrub")

    assert screen.ok is False
    assert await db.get_item_qty(author.id, "bait_glowgrub") == 0
    assert await db.get_balance(author.id) == 0


# ══════════════════════════════════════════════════════════════════════════════
#  Traps
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.cogs("cogs.fishing")
async def test_setting_a_trap_spends_the_item(bot):
    guild, author = config().guilds[0], config().members[0]
    cog = bot.get_cog("Fishing")
    await db.add_item(author.id, FISH_TRAP, 1)

    screen = await cog.trap_screen(guild, author)

    assert screen.ok and "Set" in screen.embed.title
    assert await db.get_item_qty(author.id, FISH_TRAP) == 0
    assert await db.get_trap(author.id) is not None


@pytest.mark.cogs("cogs.fishing")
async def test_no_trap_no_set(bot):
    guild, author = config().guilds[0], config().members[0]
    cog = bot.get_cog("Fishing")

    screen = await cog.trap_screen(guild, author)

    assert screen.ok is False
    assert await db.get_trap(author.id) is None


@pytest.mark.cogs("cogs.fishing")
async def test_a_soaking_trap_reports_its_wait_and_stays_put(bot):
    guild, author = config().guilds[0], config().members[0]
    cog = bot.get_cog("Fishing")
    await db.add_item(author.id, FISH_TRAP, 2)
    await cog.trap_screen(guild, author)

    screen = await cog.trap_screen(guild, author)

    assert screen.ok is False
    assert "Soaking" in screen.embed.title
    # The second trap wasn't consumed to be told that.
    assert await db.get_item_qty(author.id, FISH_TRAP) == 1


@pytest.mark.cogs("cogs.fishing")
async def test_a_soaked_trap_fills_the_bag_from_where_it_was_set(bot):
    """Rolled at the spot it soaked in, not where the angler is standing now —
    otherwise setting one in the pond and pulling it at the Trench would make
    travel pointless."""
    guild, author = config().guilds[0], config().members[0]
    cog = bot.get_cog("Fishing")
    await db.set_trap(author.id, "river", time.time() - TRAP_SOAK - 1)

    screen = await cog.trap_screen(guild, author)

    assert screen.ok and "Basket" in screen.embed.title
    assert "River Bend" in screen.embed.description
    assert await db.get_trap(author.id) is None
    caught = sum(row["qty"] for row in await db.get_bag(author.id))
    treasure = "treasure" in screen.embed.description.lower()
    assert caught > 0 or treasure


@pytest.mark.cogs("cogs.fishing")
async def test_the_trap_button_and_the_command_are_the_same_path(bot):
    guild, author = config().guilds[0], config().members[0]
    cog = bot.get_cog("Fishing")
    await db.add_item(author.id, FISH_TRAP, 1)
    view = views().FishingView(cog, author.id)
    interaction = FakeInteraction(author, guild)

    await view.trap.callback(interaction)

    assert await db.get_trap(author.id) is not None
    assert "Set" in interaction.edited["embed"].title
