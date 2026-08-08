"""
Tests for the /adventure dashboard buttons and the encounter choice.

dpytest fakes a gateway but can't dispatch component interactions, so both
views are driven by calling their handlers with a duck-typed interaction — the
SellConfirmView/BlackjackView pattern from tests/test_inventory_commands.py and
tests/test_casino_commands.py. Everything below the handler is the real cog:
the same claim, the same payout, the same DB.
"""

import time

import discord
import pytest
from discord.ext import test as dpytest

import utils.db as db
from cogs.activities import (
    ACTIVITY_DEFAULT_COOLDOWNS,
    ACTIVITY_MAX_CHARGES,
    ENCOUNTER_OUTCOMES,
    STREAK_BONUS_PER_DAY,
    roll_work_pay,
)
from cogs.activities.views import (
    BUTTON_ACTIVITIES,
    AdventureView,
    EncounterView,
    RunResultView,
    _ActivityButton,
    _EncounterButton,
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
    """A webhook followup, including the one way it is fussier than a channel.

    `view` here is MISSING-or-a-View: discord.py's Webhook.send type-checks it
    and raises on None, unlike Messageable.send which documents None as the
    default. A fake that quietly accepted None let a press ship that claimed the
    charge and then raised instead of reporting the run, so the check is part of
    the fake rather than a comment on it.
    """

    def __init__(self):
        self.sent = []

    async def send(self, **kwargs):
        if "view" in kwargs and kwargs["view"] is None:
            raise TypeError(
                "expected view parameter to be of type View or LayoutView, "
                "not NoneType"
            )
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
@pytest.mark.parametrize("activity,marker", [("work", "💼"), ("mine", "⛏️")])
async def test_a_run_without_an_encounter_still_reports_itself(
    bot, monkeypatch, activity, marker
):
    """The ordinary case: most runs open no encounter, so most presses have no
    view to attach — and that is exactly the shape that used to raise instead of
    reporting, leaving the member paid but told nothing."""
    from cogs.activities import cog as activities

    guild, author = config().guilds[0], config().members[0]
    # Never rolls an encounter (0.9 is far above ENCOUNTER_CHANCE).
    monkeypatch.setattr(activities.random, "random", lambda: 0.9)
    _cog, view = await _dashboard(bot, author, guild)
    interaction = FakeInteraction(author, guild)

    await view.press(interaction, activity)

    assert len(interaction.followup.sent) == 1
    sent = interaction.followup.sent[0]
    assert marker in sent["embed"].title
    assert "view" not in sent  # not `view=None` — a followup rejects that


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

    await view.choose(interaction, "track")  # the reliable option

    _key, pelts = ENCOUNTER_OUTCOMES["stag_tracked"]["item"]
    assert await db.get_item_qty(author.id, "pelt") == pelts
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

    _key, pelts = ENCOUNTER_OUTCOMES["stag_tracked"]["item"]
    assert await db.get_item_qty(author.id, "pelt") == pelts
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
    await db.try_claim_activity(
        author.id,
        "work",
        time.time(),
        ACTIVITY_DEFAULT_COOLDOWNS["work"],
        ACTIVITY_MAX_CHARGES["work"],
    )
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


# ══════════════════════════════════════════════════════════════════════════════
#  Collect all
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.cogs("cogs.activities")
async def test_collect_all_empties_every_bucket_in_one_press(bot, monkeypatch):
    """The point of a half-day cap is that turning up twice loses nothing — but
    that arrived as thirteen taps, which is its own kind of chore."""
    from cogs.activities import cog as activities

    guild, author = config().guilds[0], config().members[0]
    monkeypatch.setattr(activities.random, "random", lambda: 0.5)
    cog, view = await _dashboard(bot, author, guild)
    interaction = FakeInteraction(author, guild)

    await view.collect(interaction)

    stats = await db.get_activity_stats(author.id)
    for activity in BUTTON_ACTIVITIES:
        column = "work_shifts" if activity == "work" else f"{activity}_count"
        assert stats[column] == ACTIVITY_MAX_CHARGES[activity], activity

    embed = interaction.followup.sent[0]["embed"]
    banked = sum(ACTIVITY_MAX_CHARGES[a] for a in BUTTON_ACTIVITIES)
    assert f"**{banked}**" in embed.description
    assert await db.get_balance(author.id) > 0


@pytest.mark.cogs("cogs.activities")
async def test_collecting_twice_claims_nothing_the_second_time(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild, author = config().guilds[0], config().members[0]
    monkeypatch.setattr(activities.random, "random", lambda: 0.5)
    cog = bot.get_cog("Activities")

    await cog.collect_all(guild, author)
    balance = await db.get_balance(author.id)
    run = await cog.collect_all(guild, author)

    assert run.claimed is False
    assert "Nothing To Collect" in run.embed.title
    assert await db.get_balance(author.id) == balance


@pytest.mark.cogs("cogs.activities")
async def test_collect_all_reports_what_actually_landed(bot, monkeypatch):
    """Totals are balance/inventory deltas, so the summary can't drift from the
    payouts — it includes the streak, a fine, and a cave-in that paid nothing."""
    from cogs.activities import cog as activities

    guild, author = config().guilds[0], config().members[0]
    monkeypatch.setattr(activities.random, "random", lambda: 0.5)
    cog = bot.get_cog("Activities")

    before = await db.get_balance(author.id)
    run = await cog.collect_all(guild, author)
    after = await db.get_balance(author.id)

    assert f"{after - before:,}" in run.embed.description
    assert "🎒" in run.embed.description  # mining and hunting paid in items


@pytest.mark.cogs("cogs.activities")
async def test_collect_all_skips_a_disabled_activity(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild, author = config().guilds[0], config().members[0]
    monkeypatch.setattr(activities.random, "random", lambda: 0.5)
    await db.set_activities_config(guild.id, work_enabled=False)
    cog = bot.get_cog("Activities")

    await cog.collect_all(guild, author)

    stats = await db.get_activity_stats(author.id)
    assert stats["work_shifts"] == 0
    assert stats["mine_count"] == ACTIVITY_MAX_CHARGES["mine"]


@pytest.mark.cogs("cogs.activities")
async def test_every_encounter_a_collect_rolls_is_queued(bot, monkeypatch):
    """Encounters fire per run, so a full bucket rolls several — they are all
    asked, one at a time on the one message: the first rides the view and the
    rest queue behind it rather than being dropped unrolled."""
    from cogs.activities import cog as activities

    guild, author = config().guilds[0], config().members[0]
    # Every run fires an encounter.
    monkeypatch.setattr(activities.random, "random", lambda: 0.0)
    cog = bot.get_cog("Activities")

    run = await cog.collect_all(guild, author)

    assert isinstance(run.view, EncounterView)
    # One per banked run: the first is armed, the rest wait their turn.
    banked = sum(ACTIVITY_MAX_CHARGES[a] for a in BUTTON_ACTIVITIES)
    assert run.encounter_key is not None
    assert len(run.encounter_queue) == banked - 1
    assert run.view._pending_encounters == run.encounter_queue


@pytest.mark.cogs("cogs.activities")
async def test_a_surviving_encounter_still_asks_its_question(bot, monkeypatch):
    """A collect throws away every run's embed — including the one carrying the
    encounter's scene and question — so keeping the view without re-prompting
    left the member a row of unlabelled buttons under a total."""
    from cogs.activities import cog as activities
    from cogs.activities.constants import ENCOUNTERS

    guild, author = config().guilds[0], config().members[0]
    monkeypatch.setattr(activities.random, "random", lambda: 0.0)
    cog = bot.get_cog("Activities")

    run = await cog.collect_all(guild, author)

    encounter = ENCOUNTERS[run.encounter_key]
    field = next(f for f in run.embed.fields if encounter["title"] in f.name)
    assert field.value == encounter["prompt"]
    # The buttons on screen are the options the question just described.
    labels = {o["label"] for o in encounter["options"]}
    assert labels <= {c.label for c in run.view.children}


@pytest.mark.cogs("cogs.activities")
async def test_the_collect_button_counts_what_is_banked(bot, monkeypatch):
    from cogs.activities import cog as activities
    from cogs.activities.views import _CollectButton

    guild, author = config().guilds[0], config().members[0]
    monkeypatch.setattr(activities.random, "random", lambda: 0.5)
    cog, view = await _dashboard(bot, author, guild)

    button = next(c for c in view.children if isinstance(c, _CollectButton))
    banked = sum(ACTIVITY_MAX_CHARGES[a] for a in BUTTON_ACTIVITIES)
    assert f"({banked})" in button.label
    assert button.disabled is False

    await cog.collect_all(guild, author)
    _embed, state = await cog.adventure_dashboard(guild, author)
    drained = next(
        c
        for c in AdventureView(cog, author.id, state).children
        if isinstance(c, _CollectButton)
    )
    assert drained.disabled is True


@pytest.mark.cogs("cogs.activities")
async def test_collect_is_reachable_as_a_command_too(bot, monkeypatch):
    """Buttons expire; the command doesn't."""
    from cogs.activities import cog as activities

    author = config().members[0]
    monkeypatch.setattr(activities.random, "random", lambda: 0.5)

    await dpytest.message("!adventure collect", member=author)
    assert "Collected" in dpytest.get_message().embeds[0].title


# ══════════════════════════════════════════════════════════════════════════════
#  The dashboard under an individual command
# ══════════════════════════════════════════════════════════════════════════════
async def _run_card(bot, member, guild, embed=None, encounter_key=None):
    """A live RunResultView over the real run-card state."""
    cog = bot.get_cog("Activities")
    result = embed if embed is not None else discord.Embed(title="💼 Result")
    card, state = await cog.run_card(
        guild, member, result, pending=encounter_key is not None
    )
    return (
        cog,
        RunResultView(cog, member.id, state, result, encounter_key=encounter_key),
        card,
    )


def _adventure_field(embed):
    """The loop's status field on a run card, or None when it isn't there."""
    return next((f for f in embed.fields if f.name == "🧭 Adventure"), None)


@pytest.mark.cogs("cogs.activities")
async def test_an_individual_command_replies_with_the_dashboard(bot, monkeypatch):
    """The whole discoverability fix: almost nobody ran /adventure, so /work
    now shows them the loop they never found — buttons included."""
    from cogs.activities import cog as activities

    author = config().members[0]
    monkeypatch.setattr(activities.random, "random", lambda: 0.9)

    await dpytest.message("!work", member=author)
    message = dpytest.get_message()

    # One embed, not two: the result, carrying the loop as a field.
    assert len(message.embeds) == 1
    assert "💼" in message.embeds[0].title
    field = _adventure_field(message.embeds[0])
    assert field is not None
    assert "Work" in field.value and "Dig" in field.value


@pytest.mark.cogs("cogs.activities")
async def test_the_run_card_is_a_field_not_a_second_card(bot, monkeypatch):
    """Eight fields under every paycheck, twenty-six times a day, is how a
    helpful addition becomes something people want switched off — and two
    headlines on one reply is what made a dig confusing to read."""
    from cogs.activities import cog as activities

    guild, author = config().guilds[0], config().members[0]
    monkeypatch.setattr(activities.random, "random", lambda: 0.9)
    cog = bot.get_cog("Activities")

    result = discord.Embed(title="💼 Result", description="You earned coins.")
    card, _state = await cog.run_card(guild, author, result)
    full, _state = await cog.adventure_dashboard(guild, author)

    # The result keeps its own title and body; the loop is one field under it.
    assert card.title == "💼 Result"
    assert card.description == "You earned coins."
    assert len(card.fields) == 1
    assert len(full.fields) > 5
    # …and the result it was built from is never mutated, so repainting the
    # card on every press can't stack the field up.
    assert result.fields == []


@pytest.mark.cogs("cogs.activities")
async def test_a_refused_run_gets_the_card_too(bot, monkeypatch):
    """This is where it earns its place most: "work is on cooldown" is a dead
    end, and "…but two digs are ready" is an answer."""
    from cogs.activities import cog as activities

    author = config().members[0]
    monkeypatch.setattr(activities.random, "random", lambda: 0.9)

    for _ in range(ACTIVITY_MAX_CHARGES["work"] + 1):
        await dpytest.message("!work", member=author)
        message = dpytest.get_message()

    assert "Not Yet" in message.embeds[0].title
    assert _adventure_field(message.embeds[0]) is not None


@pytest.mark.cogs("cogs.activities")
async def test_a_press_on_a_run_card_replaces_the_result_in_place(bot, monkeypatch):
    """The board posts results underneath itself because it has no slot for
    one. This card does, so three hours of /work aren't three new messages."""
    from cogs.activities import cog as activities

    guild, author = config().guilds[0], config().members[0]
    monkeypatch.setattr(activities.random, "random", lambda: 0.9)
    _cog, view, _card = await _run_card(bot, author, guild)
    interaction = FakeInteraction(author, guild)

    await view.press(interaction, "mine")

    assert interaction.followup.sent == []
    assert "⛏️" in interaction.edited["embed"].title
    assert _adventure_field(interaction.edited["embed"]) is not None


@pytest.mark.cogs("cogs.activities")
async def test_a_refusal_on_a_run_card_never_overwrites_the_result(bot, monkeypatch):
    """Losing a good roll to a "still on cooldown" notice is worse than the
    notice is useful — the fishing rule, applied here."""
    from cogs.activities import cog as activities

    guild, author = config().guilds[0], config().members[0]
    monkeypatch.setattr(activities.random, "random", lambda: 0.9)
    cog, view, _card = await _run_card(bot, author, guild)
    for _ in range(ACTIVITY_MAX_CHARGES["work"]):
        await cog.run_activity(guild, author, "work")

    interaction = FakeInteraction(author, guild)
    await view.press(interaction, "work")

    assert "Not Yet" in interaction.followup.sent[0]["embed"].title
    assert interaction.followup.sent[0]["ephemeral"] is True
    assert interaction.edited["embed"].title == "💼 Result"


@pytest.mark.cogs("cogs.activities")
async def test_a_live_decision_owns_the_run_card(bot, monkeypatch):
    """The question and the buttons that answer it were the two things
    furthest apart on the old card. Now a live encounter is the *only* thing
    pressable, on the first row, and the loop's status stands down."""
    guild, author = config().guilds[0], config().members[0]
    _cog, view, card = await _run_card(
        bot, author, guild, encounter_key="work_overtime"
    )

    options = [c for c in view.children if isinstance(c, _EncounterButton)]
    assert {c.option_key for c in options} == {"stay", "deal", "leave"}
    assert all(c.row == 0 for c in options)
    # Nothing else to press, and nothing else to read.
    assert len(view.children) == len(options)
    assert _adventure_field(card) is None


@pytest.mark.cogs("cogs.activities")
async def test_answering_brings_the_loop_back(bot, monkeypatch):
    """What happened → what you decide → what's next. The last step arrives
    when the decision is settled, not on a timer."""
    from cogs.activities import views as activity_views

    guild, author = config().guilds[0], config().members[0]
    monkeypatch.setattr(activity_views.random, "random", lambda: 0.5)
    _cog, view, _card = await _run_card(
        bot, author, guild, encounter_key="work_overtime"
    )
    interaction = FakeInteraction(author, guild)

    await view.choose(interaction, "leave")

    assert "Late Shift" in interaction.edited["embed"].title
    assert await db.get_balance(author.id) > 0
    # Answered, so the options come off the card and the loop comes back.
    assert not [c for c in view.children if isinstance(c, _EncounterButton)]
    assert [c for c in view.children if isinstance(c, _ActivityButton)]
    assert _adventure_field(interaction.edited["embed"]) is not None


@pytest.mark.cogs("cogs.activities")
async def test_a_chained_stage_keeps_the_card_to_itself(bot, monkeypatch):
    """A follow-up question is the same situation again: still one decision on
    screen, still nothing else to press."""
    from cogs.activities import views as activity_views

    guild, author = config().guilds[0], config().members[0]
    monkeypatch.setattr(activity_views.random, "random", lambda: 0.5)
    _cog, view, _card = await _run_card(bot, author, guild, encounter_key="work_till")
    interaction = FakeInteraction(author, guild)

    await view.choose(interaction, "pocket")

    assert view.encounter_key == "work_till_tape"
    options = [c for c in view.children if isinstance(c, _EncounterButton)]
    assert options and len(view.children) == len(options)
    assert _adventure_field(interaction.edited["embed"]) is None


# ══════════════════════════════════════════════════════════════════════════════
#  Chained encounters
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.cogs("cogs.activities")
async def test_an_answer_can_open_another_question(bot, monkeypatch):
    """The direct answer to "you only ever make one choice": pocketing the till
    is not the end of the story, it's the start of the one about the tape."""
    from cogs.activities import views as activity_views

    guild, author = config().guilds[0], config().members[0]
    monkeypatch.setattr(activity_views.random, "random", lambda: 0.5)
    cog = bot.get_cog("Activities")
    view = EncounterView(cog, author.id, guild.id, "work_till")
    interaction = FakeInteraction(author, guild)

    await view.choose(interaction, "pocket")

    assert view.encounter_key == "work_till_tape"
    # Re-armed, not settled: the member is being asked something new.
    assert {c.option_key for c in view.children} == {"own_up", "sit_tight"}
    assert not any(c.disabled for c in view.children)
    assert any("Tape" in f.name for f in interaction.edited["embed"].fields)
    lo, hi = ENCOUNTER_OUTCOMES["till_pocketed"]["coins"]
    assert lo <= await db.get_balance(author.id) <= hi


@pytest.mark.cogs("cogs.activities")
async def test_each_stage_of_a_chain_pays_exactly_once(bot, monkeypatch):
    from cogs.activities import views as activity_views

    guild, author = config().guilds[0], config().members[0]
    monkeypatch.setattr(activity_views.random, "random", lambda: 0.5)
    cog = bot.get_cog("Activities")
    view = EncounterView(cog, author.id, guild.id, "work_till")

    await view.choose(FakeInteraction(author, guild), "pocket")
    pocketed = await db.get_balance(author.id)
    # 0.5 lands in tape_missed (0.55), which pays nothing and ends the chain.
    await view.choose(FakeInteraction(author, guild), "sit_tight")

    assert await db.get_balance(author.id) == pocketed
    assert view.encounter_key is None
    assert all(c.disabled for c in view.children)

    stale = FakeInteraction(author, guild)
    await view.choose(stale, "own_up")
    assert stale.response.sent["ephemeral"] is True
    assert await db.get_balance(author.id) == pocketed


@pytest.mark.cogs("cogs.activities")
async def test_a_chain_that_lapses_keeps_what_it_already_paid(bot, monkeypatch):
    """Each stage was its own decision, and the answered ones were answered."""
    from cogs.activities import views as activity_views

    guild, author = config().guilds[0], config().members[0]
    monkeypatch.setattr(activity_views.random, "random", lambda: 0.5)
    cog = bot.get_cog("Activities")
    view = EncounterView(cog, author.id, guild.id, "work_till")
    view.message = _FakeMessage()

    await view.choose(FakeInteraction(author, guild), "pocket")
    pocketed = await db.get_balance(author.id)
    await view.on_timeout()

    assert pocketed > 0
    assert await db.get_balance(author.id) == pocketed
    assert all(c.disabled for c in view.children)
