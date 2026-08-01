"""
Tests for what a vote actually pays.

Voting used to be rewarded in reminder slots — a currency most voters had no
use for. It now pays coins, two timed buffs and a milestone item, all of which
land in the shared global tables, so these check both that the rewards arrive
and that they can't become a way to skip the game.

The webhook plumbing isn't exercised here (tests/test_db.py covers the vote
rows); `_grant_vote_rewards` is called directly, which is the whole payout.
"""

import time

import pytest

import utils.db as db
from cogs.activities.constants import ENCOUNTER_OUTCOMES
from cogs.votes import (
    VOTE_COINS,
    VOTE_COIN_BOOST,
    VOTE_LUCK,
    VOTE_MILESTONE_EVERY,
    VOTE_MILESTONE_ITEM,
    VOTE_STREAK_CAP,
    vote_coins,
)
from utils import helpers as h
from utils import items as item_catalogue


async def settle_todays_quest(user_id: int):
    """Claim today's fishing quest up front so a sale can't also pay it out.

    Selling bumps the daily quest, and the quest is seeded on `(user_id, day)`
    — so whether a sale happens to *complete* one depends on the member id the
    test framework generated and the date the suite ran. That is exactly the
    kind of test that passes locally and fails in CI, which is what it did.
    Claiming it first makes the sale's payout the only thing being measured.
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


# ── The pure half ────────────────────────────────────────────────────────────
def test_vote_coins_scale_with_the_streak_and_stop():
    assert vote_coins(1) == VOTE_COINS  # first vote, no bonus yet
    assert vote_coins(2) > vote_coins(1)
    assert vote_coins(999) == round(VOTE_COINS * (1 + VOTE_STREAK_CAP))
    # Monotonic up to the cap, flat after.
    run = [vote_coins(s) for s in range(1, 30)]
    assert run == sorted(run)
    assert run[-1] == vote_coins(9_999)


def test_a_vote_always_pays_something():
    assert vote_coins(0) >= 1
    assert vote_coins(-5) >= 1


def test_voting_is_a_thank_you_not_a_shortcut():
    """Two sites on 12h cooldowns cap a day at four votes. Against a ~8,000
    coin day that has to stay a bonus, not a replacement for playing."""
    from cogs.activities.helpers import adventure_coins_per_day

    best_day = 4 * vote_coins(9_999)
    assert best_day < 0.25 * adventure_coins_per_day()


def test_the_milestone_item_is_a_real_one():
    assert item_catalogue.get(VOTE_MILESTONE_ITEM) is not None
    assert VOTE_MILESTONE_EVERY > 1


# ── The payout ───────────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.votes")
async def test_a_vote_pays_coins_and_both_buffs(bot):
    cog = bot.get_cog("Votes")
    user = bot.guilds[0].members[0]

    reward = await cog._grant_vote_rewards(user.id, 1)

    assert reward["coins"] == VOTE_COINS
    assert await db.get_balance(user.id) == VOTE_COINS
    effects = await db.get_active_effects(user.id)
    assert effects["coin_boost"]["magnitude"] == VOTE_COIN_BOOST
    assert effects["luck"]["magnitude"] == VOTE_LUCK
    # Both are timed, not charge-based — a boost you have to spend carefully
    # isn't a thank-you.
    assert effects["coin_boost"]["expires_at"] > 0
    assert len(reward["lines"]) >= 4


@pytest.mark.cogs("cogs.votes")
async def test_the_milestone_only_lands_on_the_milestone(bot):
    cog = bot.get_cog("Votes")
    user = bot.guilds[0].members[0]

    await cog._grant_vote_rewards(user.id, VOTE_MILESTONE_EVERY - 1)
    assert await db.get_item_qty(user.id, VOTE_MILESTONE_ITEM) == 0

    reward = await cog._grant_vote_rewards(user.id, VOTE_MILESTONE_EVERY)
    assert await db.get_item_qty(user.id, VOTE_MILESTONE_ITEM) == 1
    assert any("milestone" in line for line in reward["lines"])


@pytest.mark.cogs("cogs.votes")
async def test_a_second_vote_refreshes_rather_than_stacks(bot):
    """Effects don't stack anywhere in the bot (docs/economy-design.md) — the
    freshest one wins. Voting twice extends the window, it doesn't double it."""
    cog = bot.get_cog("Votes")
    user = bot.guilds[0].members[0]

    await cog._grant_vote_rewards(user.id, 1)
    await cog._grant_vote_rewards(user.id, 2)

    effects = await db.get_active_effects(user.id)
    assert effects["coin_boost"]["magnitude"] == VOTE_COIN_BOOST
    # Coins, though, are paid every time.
    assert await db.get_balance(user.id) == vote_coins(1) + vote_coins(2)


# ── The boost is honoured where coins are earned ─────────────────────────────
def test_coin_boost_helper_never_shrinks_a_payout():
    assert h.apply_coin_boost(100, {}) == 100
    assert h.apply_coin_boost(100, None) == 100
    assert h.apply_coin_boost(100, {"coin_boost": {"magnitude": 1.25}}) == 125
    # A magnitude below 1 would be a *nerf* smuggled in as a buff.
    assert h.apply_coin_boost(100, {"coin_boost": {"magnitude": 0.5}}) == 100
    assert h.coin_boost_multiplier({}) == 1.0


@pytest.mark.cogs("cogs.activities", "cogs.votes")
async def test_the_boost_reaches_activity_payouts(bot, monkeypatch):
    from cogs.activities import cog as activities

    guild = bot.guilds[0]
    author = guild.members[0]
    monkeypatch.setattr(activities.random, "random", lambda: 0.5)
    cog = bot.get_cog("Activities")

    plain = await cog.run_activity(guild, author, "work")
    assert plain.claimed
    unboosted = await db.get_balance(author.id)

    await db.grant_effect(author.id, "coin_boost", 2.0, duration=3600)
    boosted_run = await cog.run_activity(guild, author, "work")
    assert boosted_run.claimed
    boosted = await db.get_balance(author.id) - unboosted

    assert boosted == unboosted * 2


@pytest.mark.cogs("cogs.fishing", "cogs.votes")
async def test_the_boost_reaches_a_fishing_sale(bot):
    guild = bot.guilds[0]
    author = guild.members[0]
    cog = bot.get_cog("Fishing")
    await settle_todays_quest(author.id)
    await db.record_catch(author.id, "salmon", 5.0, 100, track_best=True)
    await db.grant_effect(author.id, "coin_boost", 2.0, duration=3600)

    screen = await cog.sell_screen(guild, author)

    assert screen.ok
    assert await db.get_balance(author.id) == 200


@pytest.mark.cogs("cogs.activities", "cogs.votes")
async def test_the_boost_never_makes_a_cost_bigger(bot):
    """An encounter option that charges for itself, a hunting fine, a rob fine —
    a multiplier on coins *earned* must not touch coins spent."""
    guild = bot.guilds[0]
    author = guild.members[0]
    cog = bot.get_cog("Activities")
    await db.add_coins(author.id, 5_000)
    await db.grant_effect(author.id, "coin_boost", 2.0, duration=3600)
    before = await db.get_balance(author.id)

    # trader_sand charges the price of the box and returns nothing.
    step = await cog.resolve_encounter(
        guild, author, "explore_trader", "buy", 0.99, 0.5
    )

    price = -ENCOUNTER_OUTCOMES["trader_sand"]["coins"][0]
    spent = before - await db.get_balance(author.id)
    assert spent == price, step.embed.description
