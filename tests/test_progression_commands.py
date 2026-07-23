"""
Command-level tests for cogs/progression/ under dpytest (parse → check → DB →
reply).
"""

import pytest
from discord.ext import test as dpytest

import utils.db as db
from cogs.progression.definitions import WeeklyObjectiveDef
from tests.conftest import config

# A single fabricated weekly objective, easy to drive deterministically without
# needing to simulate real fishing/casino/activities play. Its stat
# ("contribution") is bumped directly via db.add_contribution.
_FAKE_OBJ = WeeklyObjectiveDef(
    "test_contrib",
    "Test Objective",
    "🧪",
    "Earn 100 contribution.",
    "contribution",
    100,
    150,
    10,
)


def _force_single_weekly_pick(monkeypatch):
    # Resolve the cog module at call time, not import time: unload_extension
    # (run by e.g. test_command_limits' all-cogs fixture) purges the package
    # from sys.modules, so a collection-time module reference goes stale and a
    # monkeypatch against it would silently patch a dead module.
    from cogs.progression import cog as progression_cog

    monkeypatch.setattr(
        progression_cog,
        "pick_weekly_objectives",
        lambda guild_id, user_id, period, pool, count: [_FAKE_OBJ],
    )


# ── Bare /progress profile ────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.progression")
async def test_bare_progress_shows_profile(bot):
    author = config().members[0]
    await dpytest.message("!progress", member=author)
    sent = dpytest.get_message()
    assert sent.embeds
    assert "Progress" in sent.embeds[0].title


@pytest.mark.cogs("cogs.progression")
async def test_bare_progress_lazily_awards_achievements(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(guild.id, author.id, 1_000)  # crosses the balance_1k threshold

    await dpytest.message("!progress", member=author)
    dpytest.get_message()

    earned = await db.get_earned_achievements(guild.id, author.id)
    assert "balance_1k" in earned
    # Reward (50 coins) landed on top of the 1,000 seeded.
    assert await db.get_balance(guild.id, author.id) == 1_050


# ── /progress achievements ────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.progression")
async def test_achievements_awards_reward_exactly_once(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(guild.id, author.id, 1_000)

    await dpytest.message("!progress achievements", member=author)
    sent = dpytest.get_message()
    assert sent.embeds
    assert await db.get_balance(guild.id, author.id) == 1_050

    # Re-evaluating (a second view) must NOT grant the reward again.
    await dpytest.message("!progress achievements", member=author)
    dpytest.get_message()
    assert await db.get_balance(guild.id, author.id) == 1_050
    earned = await db.get_earned_achievements(guild.id, author.id)
    assert list(earned).count("balance_1k") == 1


@pytest.mark.cogs("cogs.progression")
async def test_achievements_shows_category_breakdown(bot):
    author = config().members[0]
    await dpytest.message("!progress achievements", member=author)
    sent = dpytest.get_message()
    embed = sent.embeds[0]
    field_names = " ".join(f.name for f in embed.fields)
    assert "Fishing" in field_names
    assert "Wealth" in field_names
    assert "Casino" in field_names
    assert "Activities" in field_names


@pytest.mark.cogs("cogs.progression")
async def test_achievements_for_another_member_does_not_evaluate_them(bot):
    guild = config().guilds[0]
    viewer = config().members[0]
    other = config().members[1]
    await db.add_coins(guild.id, other.id, 1_000)

    await dpytest.message(f"!progress achievements {other.mention}", member=viewer)
    dpytest.get_message()

    # Looking at someone else's profile must not silently award/pay them.
    assert await db.get_earned_achievements(guild.id, other.id) == {}
    assert await db.get_balance(guild.id, other.id) == 1_000


# ── /progress badges ─────────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.progression")
async def test_badges_empty_state(bot):
    author = config().members[0]
    await dpytest.message("!progress badges", member=author)
    sent = dpytest.get_message()
    assert "hasn't earned any badges" in sent.embeds[0].description


@pytest.mark.cogs("cogs.progression")
async def test_badges_shows_earned_emoji(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.try_award_achievement(guild.id, author.id, "balance_1k")

    await dpytest.message("!progress badges", member=author)
    sent = dpytest.get_message()
    from cogs.progression.definitions import ACHIEVEMENTS_BY_KEY

    assert ACHIEVEMENTS_BY_KEY["balance_1k"].emoji in sent.embeds[0].description


# ── /progress weekly ──────────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.progression")
async def test_weekly_shows_objective_and_progress(bot, monkeypatch):
    _force_single_weekly_pick(monkeypatch)
    author = config().members[0]

    await dpytest.message("!progress weekly", member=author)
    sent = dpytest.get_message()
    embed = sent.embeds[0]
    assert any(_FAKE_OBJ.name in f.name for f in embed.fields)


@pytest.mark.cogs("cogs.progression")
async def test_weekly_autoclaims_completed_objective_exactly_once(bot, monkeypatch):
    _force_single_weekly_pick(monkeypatch)
    guild = config().guilds[0]
    author = config().members[0]

    # First view creates the row with baseline = current contribution (0).
    await dpytest.message("!progress weekly", member=author)
    dpytest.get_message()
    before = await db.get_balance(guild.id, author.id)

    # Cross the target (100).
    await db.add_contribution(guild.id, author.id, 100)

    await dpytest.message("!progress weekly", member=author)
    sent = dpytest.get_message()
    assert "coins" in sent.embeds[0].fields[0].value.lower()
    after_claim = await db.get_balance(guild.id, author.id)
    assert after_claim == before + 150  # coins reward, no prestige bonus at rank 0

    # A third view must not pay out again.
    await dpytest.message("!progress weekly", member=author)
    dpytest.get_message()
    assert await db.get_balance(guild.id, author.id) == after_claim


# ── /progress title ────────────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.progression")
async def test_title_rejects_when_nothing_earned(bot):
    author = config().members[0]
    await dpytest.message("!progress title Tycoon", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title


@pytest.mark.cogs("cogs.progression")
async def test_title_selection_roundtrip(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.try_award_achievement(
        guild.id, author.id, "balance_100k"
    )  # title "Tycoon"

    await dpytest.message("!progress title Tycoon", member=author)
    sent = dpytest.get_message()
    assert "Tycoon" in sent.embeds[0].description

    progression = await db.get_progression(guild.id, author.id)
    assert progression["selected_title"] == "Tycoon"

    await dpytest.message("!progress title none", member=author)
    dpytest.get_message()
    progression = await db.get_progression(guild.id, author.id)
    assert progression["selected_title"] == ""


# ── /progress prestige ────────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.progression")
async def test_prestige_status_shows_requirements(bot):
    author = config().members[0]
    await dpytest.message("!progress prestige", member=author)
    sent = dpytest.get_message()
    embed = sent.embeds[0]
    assert any("Requirements" in f.name for f in embed.fields)


@pytest.mark.cogs("cogs.progression")
async def test_prestige_confirm_blocked_on_insufficient_points(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(guild.id, author.id, 25_000)

    await dpytest.message("!progress prestige confirm", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert (await db.get_progression(guild.id, author.id))["prestige"] == 0
    assert await db.get_balance(guild.id, author.id) == 25_000


@pytest.mark.cogs("cogs.progression")
async def test_prestige_confirm_blocked_on_insufficient_coins(bot):
    guild = config().guilds[0]
    author = config().members[0]
    # 80 + 15 + 10 = 105 >= 100 required points, but no coins seeded.
    for key in ("balance_100k", "contrib_500", "casino_games_10"):
        await db.try_award_achievement(guild.id, author.id, key)

    await dpytest.message("!progress prestige confirm", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert (await db.get_progression(guild.id, author.id))["prestige"] == 0


@pytest.mark.cogs("cogs.progression")
async def test_prestige_confirm_success_advances_rank_and_charges_coins(bot):
    guild = config().guilds[0]
    author = config().members[0]
    for key in ("balance_100k", "contrib_500", "casino_games_10"):
        await db.try_award_achievement(guild.id, author.id, key)
    await db.add_coins(guild.id, author.id, 25_000)

    await dpytest.message("!progress prestige confirm", member=author)
    sent = dpytest.get_message()
    assert "Prestige" in sent.embeds[0].title
    assert (await db.get_progression(guild.id, author.id))["prestige"] == 1
    assert await db.get_balance(guild.id, author.id) == 0

    # Confirming again immediately fails — rank 1 needs 200 points / 50,000 coins.
    await dpytest.message("!progress prestige confirm", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert (await db.get_progression(guild.id, author.id))["prestige"] == 1
