"""
Tests for the /adventure dashboard buttons and the encounter choice.

dpytest fakes a gateway but can't dispatch component interactions, so both
views are driven by calling their handlers with a duck-typed interaction — the
SellConfirmView/BlackjackView pattern from tests/test_inventory_commands.py and
tests/test_casino_commands.py. Everything below the handler is the real cog:
the same claim, the same payout, the same DB.
"""

import time

import pytest
from discord.ext import test as dpytest

import utils.db as db
from cogs.activities import (
    ACTIVITY_MAX_CHARGES,
    ENCOUNTER_OUTCOMES,
    STREAK_BONUS_PER_DAY,
    roll_work_pay,
)
from cogs.activities.views import (
    BUTTON_ACTIVITIES,
    AdventureView,
    EncounterView,
    _ActivityButton,
)
from tests.conftest import config


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
    """Enough discord.Interaction for the two views in cogs/activities."""

    def __init__(self, member, guild):
        self.user = member
        self.guild = guild
        self.response = _FakeResponse()
        self.followup = _FakeFollowup()
        self.edited = None

    async def edit_original_response(self, **kwargs):
        self.edited = kwargs
        return _FakeMessage()


async def _dashboard(bot, member, guild):
    """A live AdventureView over the real dashboard state."""
    cog = bot.get_cog("Activities")
    _embed, state = await cog.adventure_dashboard(guild, member)
    return cog, AdventureView(cog, member.id, state)


def _button(view, activity):
    for child in view.children:
        if isinstance(child, _ActivityButton) and child.activity == activity:
            return child
    raise AssertionError(f"no button for {activity}")


# ══════════════════════════════════════════════════════════════════════════════
#  The dashboard's buttons
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.cogs("cogs.activities")
async def test_the_dashboard_ships_a_button_per_runnable_activity(bot):
    """The whole point of the change: four activities you can run without
    typing a second command."""
    guild, author = config().guilds[0], config().members[0]
    _cog, view = await _dashboard(bot, author, guild)

    assert {b.activity for b in view.children if isinstance(b, _ActivityButton)} == set(
        BUTTON_ACTIVITIES
    )
    # /rob takes a target, and a button can't ask for one.
    assert "rob" not in BUTTON_ACTIVITIES


@pytest.mark.cogs("cogs.activities")
async def test_a_button_press_runs_the_activity_and_repaints_the_card(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild, author = config().guilds[0], config().members[0]
    monkeypatch.setattr(activities.random, "random", lambda: 0.5)
    _cog, view = await _dashboard(bot, author, guild)
    interaction = FakeInteraction(author, guild)

    await view.press(interaction, "work")

    assert await db.get_balance(author.id) == roll_work_pay(0.5, 0)
    # The card is edited in place, and the result posts underneath it.
    assert interaction.edited is not None and interaction.edited["view"] is view
    assert len(interaction.followup.sent) == 1
    assert "💼" in interaction.followup.sent[0]["embed"].title


@pytest.mark.cogs("cogs.activities")
async def test_a_press_spends_exactly_one_charge(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild, author = config().guilds[0], config().members[0]
    monkeypatch.setattr(activities.random, "random", lambda: 0.5)
    cog, view = await _dashboard(bot, author, guild)

    await view.press(FakeInteraction(author, guild), "work")

    _embed, state = await cog.adventure_dashboard(guild, author)
    assert state["charges"]["work"]["ready"] == ACTIVITY_MAX_CHARGES["work"] - 1
    assert (await db.get_activity_stats(author.id))["work_shifts"] == 1


@pytest.mark.cogs("cogs.activities")
async def test_the_labels_carry_the_charge_count_then_the_wait(bot, monkeypatch):
    """A member opens the dashboard to find out what they can do right now, so
    the buttons say it rather than the embed alone."""
    from cogs.activities import cog as activities

    guild, author = config().guilds[0], config().members[0]
    monkeypatch.setattr(activities.random, "random", lambda: 0.5)
    _cog, view = await _dashboard(bot, author, guild)

    full = _button(view, "work")
    assert f"×{ACTIVITY_MAX_CHARGES['work']}" in full.label
    assert full.disabled is False

    for _ in range(ACTIVITY_MAX_CHARGES["work"]):
        await view.press(FakeInteraction(author, guild), "work")

    drained = _button(view, "work")
    assert drained.disabled is True
    assert "·" in drained.label  # "Work · 20m"


@pytest.mark.cogs("cogs.activities")
async def test_a_disabled_activity_gets_a_dead_button(bot):
    guild, author = config().guilds[0], config().members[0]
    await db.set_activities_config(guild.id, hunt_enabled=False)
    _cog, view = await _dashboard(bot, author, guild)

    assert _button(view, "hunt").disabled is True
    assert _button(view, "explore").disabled is False


@pytest.mark.cogs("cogs.activities")
async def test_a_stale_press_is_refused_not_paid(bot, monkeypatch):
    """The buttons are painted from a snapshot, so a card left open can offer a
    charge that's since been spent. The claim is still the only arbiter."""
    from cogs.activities import cog as activities

    guild, author = config().guilds[0], config().members[0]
    monkeypatch.setattr(activities.random, "random", lambda: 0.5)
    _cog, view = await _dashboard(bot, author, guild)

    # Drain the bucket behind the view's back.
    now = time.time()
    for _ in range(ACTIVITY_MAX_CHARGES["work"]):
        await db.try_claim_activity(
            author.id, "work", now, 1200, ACTIVITY_MAX_CHARGES["work"]
        )
    balance = await db.get_balance(author.id)

    interaction = FakeInteraction(author, guild)
    await view.press(interaction, "work")

    assert "Not Yet" in interaction.followup.sent[0]["embed"].title
    assert await db.get_balance(author.id) == balance


@pytest.mark.cogs("cogs.activities")
async def test_only_the_invoker_may_press(bot):
    """The card counts *your* charges, so someone else pressing it would be
    acting on a screen that isn't about them."""
    guild, author, other = config().guilds[0], config().members[0], config().members[1]
    _cog, view = await _dashboard(bot, author, guild)

    mine = FakeInteraction(author, guild)
    assert await view.interaction_check(mine) is True

    theirs = FakeInteraction(other, guild)
    assert await view.interaction_check(theirs) is False
    assert theirs.response.sent["ephemeral"] is True


@pytest.mark.cogs("cogs.activities")
async def test_refresh_repaints_without_running_anything(bot):
    guild, author = config().guilds[0], config().members[0]
    _cog, view = await _dashboard(bot, author, guild)
    interaction = FakeInteraction(author, guild)

    await view.refresh(interaction)

    assert interaction.edited is not None
    assert interaction.followup.sent == []
    assert (await db.get_activity_stats(author.id))["work_shifts"] == 0


@pytest.mark.cogs("cogs.activities")
async def test_timeout_greys_the_buttons_but_leaves_the_card(bot):
    guild, author = config().guilds[0], config().members[0]
    _cog, view = await _dashboard(bot, author, guild)
    view.message = _FakeMessage()

    await view.on_timeout()

    assert all(child.disabled for child in view.children)
    assert view.message.edits[-1]["view"] is view
    assert "embed" not in view.message.edits[-1]


# ══════════════════════════════════════════════════════════════════════════════
#  Encounters
# ══════════════════════════════════════════════════════════════════════════════
def _force_encounter(monkeypatch, module, *rolls):
    """Make the next run fire the first encounter registered for its activity."""
    monkeypatch.setattr(module.random, "random", _sequence(*rolls))


def _sequence(*values, then=0.5):
    seq = iter(values)
    return lambda: next(seq, then)


@pytest.mark.cogs("cogs.activities")
async def test_an_encounter_rides_the_result_it_belongs_to(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild, author = config().guilds[0], config().members[0]
    # pay, scene, encounter fires (< ENCOUNTER_CHANCE), pick the first one
    _force_encounter(monkeypatch, activities, 0.5, 0.5, 0.0, 0.0)
    cog = bot.get_cog("Activities")

    run = await cog.run_activity(guild, author, "work")

    assert isinstance(run.view, EncounterView)
    assert run.view.encounter_key == "work_overtime"
    assert any("Late Shift" in f.name for f in run.embed.fields)


@pytest.mark.cogs("cogs.activities")
async def test_choosing_an_option_pays_out_once(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild, author = config().guilds[0], config().members[0]
    monkeypatch.setattr(activities.random, "random", lambda: 0.5)
    cog = bot.get_cog("Activities")
    view = EncounterView(cog, author.id, guild.id, "hunt_stag")
    interaction = FakeInteraction(author, guild)

    await view.choose(interaction, "track")  # 0.5 < 0.75 → stag_tracked

    assert await db.get_item_qty(author.id, "pelt") == 2
    assert interaction.edited is not None
    assert all(child.disabled for child in view.children)


@pytest.mark.cogs("cogs.activities")
async def test_a_second_press_cannot_claim_the_same_encounter(bot, monkeypatch):
    """The single-use guard is the only thing standing between a double-tap and
    a double payout — there is no row to check against."""
    from cogs.activities import cog as activities

    guild, author = config().guilds[0], config().members[0]
    monkeypatch.setattr(activities.random, "random", lambda: 0.5)
    cog = bot.get_cog("Activities")
    view = EncounterView(cog, author.id, guild.id, "hunt_stag")

    await view.choose(FakeInteraction(author, guild), "track")
    second = FakeInteraction(author, guild)
    await view.choose(second, "shoot")

    assert await db.get_item_qty(author.id, "pelt") == 2
    assert await db.get_item_qty(author.id, "golden_antler") == 0
    assert second.response.sent["ephemeral"] is True


@pytest.mark.cogs("cogs.activities")
async def test_an_option_that_costs_coins_charges_for_itself(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild, author = config().guilds[0], config().members[0]
    await db.add_coins(author.id, 1000)
    monkeypatch.setattr(activities.random, "random", lambda: 0.5)
    cog = bot.get_cog("Activities")
    view = EncounterView(cog, author.id, guild.id, "explore_trader")

    # 0.5 lands in trader_key (0.45 chest, then 0.30 key).
    await view.choose(FakeInteraction(author, guild), "buy")

    cost = -ENCOUNTER_OUTCOMES["trader_key"]["coins"][0]
    assert await db.get_balance(author.id) == 1000 - cost
    assert await db.get_item_qty(author.id, "treasure_key") == 1


@pytest.mark.cogs("cogs.activities")
async def test_only_the_invoker_may_answer_an_encounter(bot):
    guild, author, other = config().guilds[0], config().members[0], config().members[1]
    cog = bot.get_cog("Activities")
    view = EncounterView(cog, author.id, guild.id, "hunt_stag")

    assert await view.interaction_check(FakeInteraction(author, guild)) is True
    theirs = FakeInteraction(other, guild)
    assert await view.interaction_check(theirs) is False


@pytest.mark.cogs("cogs.activities")
async def test_a_lapsed_encounter_pays_nothing(bot):
    """Timing out has to cost the member the bonus, not hand them the safe
    option — otherwise walking away is a strategy on every encounter whose safe
    option is the better expected value."""
    guild, author = config().guilds[0], config().members[0]
    cog = bot.get_cog("Activities")
    view = EncounterView(cog, author.id, guild.id, "hunt_stag")
    view.message = _FakeMessage()

    await view.on_timeout()

    assert await db.get_inventory(author.id) == []
    assert await db.get_balance(author.id) == 0
    assert all(child.disabled for child in view.children)


# ══════════════════════════════════════════════════════════════════════════════
#  The daily streak
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.cogs("cogs.activities")
async def test_the_streak_starts_on_the_first_run_of_the_day(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild, author = config().guilds[0], config().members[0]
    monkeypatch.setattr(activities.random, "random", lambda: 0.5)

    await dpytest.message("!work", member=author)
    embed = dpytest.get_message().embeds[0]

    assert "Streak started" in embed.description
    assert (await db.get_activity_stats(author.id))["streak_days"] == 1

    # A second run the same day doesn't re-announce or re-count it.
    await dpytest.message("!work", member=author)
    assert "Streak started" not in dpytest.get_message().embeds[0].description
    assert (await db.get_activity_stats(author.id))["streak_days"] == 1


@pytest.mark.cogs("cogs.activities")
async def test_a_continued_streak_multiplies_coin_rewards(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild, author = config().guilds[0], config().members[0]
    monkeypatch.setattr(activities.random, "random", lambda: 0.5)
    # Yesterday's run, so today's continues the streak to day 2.
    await db.try_claim_activity(author.id, "work", time.time(), 1200, 3)
    await db.try_claim_adventure_streak(author.id, int(time.time() // 86_400) - 1, 1)

    await dpytest.message("!work", member=author)
    embed = dpytest.get_message().embeds[0]

    assert "2-day streak" in embed.description
    expected = round(roll_work_pay(0.5, 0) * (1 + STREAK_BONUS_PER_DAY))
    assert await db.get_balance(author.id) == expected


@pytest.mark.cogs("cogs.activities")
async def test_robbing_counts_toward_the_streak(bot, monkeypatch):
    """The dashboard says "run anything today", and /rob is one of the five —
    even though it resolves outside the shared run path."""
    from cogs.activities import cog as activities

    author, target = config().members[0], config().members[1]
    await db.add_coins(author.id, 500)
    await db.add_coins(target.id, 2000)
    monkeypatch.setattr(activities.random, "random", lambda: 0.9)  # a failed attempt

    await dpytest.message(f"!rob {target.mention}", member=author)
    dpytest.get_message()

    assert (await db.get_activity_stats(author.id))["streak_days"] == 1


@pytest.mark.cogs("cogs.activities")
async def test_the_dashboard_shows_what_the_streak_is_worth(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild, author = config().guilds[0], config().members[0]
    monkeypatch.setattr(activities.random, "random", lambda: 0.5)
    await dpytest.message("!work", member=author)
    dpytest.get_message()

    await dpytest.message("!adventure", member=author)
    fields = {f.name: f.value for f in dpytest.get_message().embeds[0].fields}

    assert "1-day streak" in fields["Daily streak"]
