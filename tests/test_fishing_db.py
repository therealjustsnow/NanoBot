"""
Tests for the fishing accessors in utils/db/ — in-memory SQLite.
"""

import aiosqlite
import pytest

import utils.db as db


@pytest.fixture(autouse=True)
async def database(monkeypatch):
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    monkeypatch.setattr(db, "_db", conn)
    await db._ensure_fishing_tables()
    # sell/upgrade credit coins through the economy tables.
    await db._ensure_economy_tables()
    # bait charge consumption exercises the shared items/effects tables.
    await db._ensure_items_tables()
    yield conn
    await conn.close()


G = 777
A, B = 1, 2


# ── Config ─────────────────────────────────────────────────────────────────────
async def test_config_defaults():
    cfg = await db.get_fishing_config(G)
    assert cfg == {"enabled": True, "cooldown": 240}


async def test_config_roundtrip_partial_update():
    await db.set_fishing_config(G, cooldown=120)
    cfg = await db.get_fishing_config(G)
    assert cfg["cooldown"] == 120
    assert cfg["enabled"] is True  # untouched key keeps its default
    await db.set_fishing_config(G, enabled=False)
    cfg = await db.get_fishing_config(G)
    assert cfg["enabled"] is False
    assert cfg["cooldown"] == 120


# ── Cast cooldown claim ────────────────────────────────────────────────────────
async def test_first_cast_claims():
    assert await db.try_claim_cast(G, A, 1000.0, 60) == 0
    fisher = await db.get_fisher(G, A)
    assert fisher["casts"] == 1
    assert fisher["last_cast"] == 1000.0


async def test_cast_blocked_inside_cooldown():
    await db.try_claim_cast(G, A, 1000.0, 60)
    retry = await db.try_claim_cast(G, A, 1010.0, 60)
    assert retry == 50
    # The failed claim didn't touch the row.
    fisher = await db.get_fisher(G, A)
    assert fisher["casts"] == 1
    assert fisher["last_cast"] == 1000.0


async def test_cast_allowed_after_cooldown():
    await db.try_claim_cast(G, A, 1000.0, 60)
    assert await db.try_claim_cast(G, A, 1060.0, 60) == 0
    assert (await db.get_fisher(G, A))["casts"] == 2


async def test_cast_retry_is_at_least_one_second():
    await db.try_claim_cast(G, A, 1000.0, 60)
    assert await db.try_claim_cast(G, A, 1059.5, 60) >= 1


# ── Catches, bag, species ──────────────────────────────────────────────────────
async def test_record_catch_fills_bag_species_and_stats():
    await db.record_catch(G, A, "salmon", 5.0, 40)
    await db.record_catch(G, A, "salmon", 7.5, 55)
    await db.record_catch(G, A, "boot", 0.5, 1, track_best=False)

    bag = await db.get_bag(G, A)
    assert {r["fish_key"]: (r["qty"], r["total_value"]) for r in bag} == {
        "salmon": (2, 95),
        "boot": (1, 1),
    }
    assert await db.get_species_counts(G, A) == {"salmon": 2, "boot": 1}
    fisher = await db.get_fisher(G, A)
    assert fisher["caught"] == 3
    assert fisher["best_key"] == "salmon"
    assert fisher["best_weight"] == 7.5


async def test_best_only_improves_and_junk_never_counts():
    await db.record_catch(G, A, "salmon", 7.5, 55)
    await db.record_catch(G, A, "tuna", 3.0, 50)  # lighter — not a new best
    fisher = await db.get_fisher(G, A)
    assert fisher["best_key"] == "salmon"
    await db.record_catch(G, A, "driftwood", 100.0, 2, track_best=False)
    assert (await db.get_fisher(G, A))["best_key"] == "salmon"


async def test_sell_one_species():
    await db.record_catch(G, A, "salmon", 5.0, 40)
    await db.record_catch(G, A, "salmon", 7.5, 55)
    await db.record_catch(G, A, "boot", 0.5, 1)
    count, total = await db.sell_catches(G, A, "salmon")
    assert (count, total) == (2, 95)
    bag = await db.get_bag(G, A)
    assert [r["fish_key"] for r in bag] == ["boot"]
    # Species dex is lifetime — selling doesn't erase it.
    assert (await db.get_species_counts(G, A))["salmon"] == 2


async def test_sell_everything():
    await db.record_catch(G, A, "salmon", 5.0, 40)
    await db.record_catch(G, A, "boot", 0.5, 1)
    count, total = await db.sell_catches(G, A)
    assert (count, total) == (2, 41)
    assert await db.get_bag(G, A) == []


async def test_sell_empty_bag():
    assert await db.sell_catches(G, A) == (0, 0)
    assert await db.sell_catches(G, A, "salmon") == (0, 0)


async def test_sell_does_not_touch_other_users():
    await db.record_catch(G, A, "salmon", 5.0, 40)
    await db.record_catch(G, B, "salmon", 5.0, 40)
    await db.sell_catches(G, A)
    assert len(await db.get_bag(G, B)) == 1


# ── Earnings + leaderboard ─────────────────────────────────────────────────────
async def test_earned_accumulates_and_ranks():
    await db.add_fishing_earned(G, A, 100)
    await db.add_fishing_earned(G, A, 50)
    await db.add_fishing_earned(G, B, 500)
    assert (await db.get_fisher(G, A))["earned"] == 150
    assert await db.get_fishing_rank(G, A) == (2, 150)
    assert await db.get_fishing_rank(G, B) == (1, 500)
    assert await db.count_fishers(G) == 2
    board = await db.get_fishing_leaderboard(G)
    assert [r["user_id"] for r in board] == [B, A]


async def test_rank_none_without_earnings():
    assert await db.get_fishing_rank(G, A) is None


# ── Rod upgrades ───────────────────────────────────────────────────────────────
async def test_rod_upgrade_from_fresh_row():
    assert await db.set_rod_level(G, A, 1, expected=0) is True
    assert (await db.get_fisher(G, A))["rod_level"] == 1


async def test_rod_upgrade_race_second_buyer_loses():
    assert await db.set_rod_level(G, A, 1, expected=0) is True
    # A racing second upgrade still expects level 0 — must fail.
    assert await db.set_rod_level(G, A, 1, expected=0) is False
    assert (await db.get_fisher(G, A))["rod_level"] == 1
    assert await db.set_rod_level(G, A, 2, expected=1) is True


# ── XP ─────────────────────────────────────────────────────────────────────────
async def test_add_fishing_xp_accumulates():
    assert await db.add_fishing_xp(G, A, 10) == 10
    assert await db.add_fishing_xp(G, A, 15) == 25
    assert (await db.get_fisher(G, A))["xp"] == 25


async def test_add_fishing_xp_clamps_at_zero():
    await db.add_fishing_xp(G, A, 10)
    assert await db.add_fishing_xp(G, A, -100) == 0


async def test_fresh_fisher_has_zero_xp_and_streak_columns():
    fisher = await db.get_fisher(G, B)
    assert fisher["xp"] == 0
    assert fisher["streak_days"] == 0
    assert fisher["last_day"] == 0


# ── Daily streak stamp ─────────────────────────────────────────────────────────
async def test_try_claim_daily_streak_first_time():
    await db.try_claim_cast(G, A, 1000.0, 60)  # creates the fishing_stats row
    assert await db.try_claim_daily_streak(G, A, 100, 1) is True
    fisher = await db.get_fisher(G, A)
    assert fisher["last_day"] == 100
    assert fisher["streak_days"] == 1


async def test_try_claim_daily_streak_same_day_is_a_noop():
    await db.try_claim_cast(G, A, 1000.0, 60)
    assert await db.try_claim_daily_streak(G, A, 100, 1) is True
    # A second claim attempt for the same day must not re-advance.
    assert await db.try_claim_daily_streak(G, A, 100, 2) is False
    assert (await db.get_fisher(G, A))["streak_days"] == 1


async def test_try_claim_daily_streak_advances_next_day():
    await db.try_claim_cast(G, A, 1000.0, 60)
    await db.try_claim_daily_streak(G, A, 100, 1)
    assert await db.try_claim_daily_streak(G, A, 101, 2) is True
    assert (await db.get_fisher(G, A))["streak_days"] == 2


# ── Daily quest ──────────────────────────────────────────────────────────────────
async def test_get_or_create_quest_is_idempotent():
    quest = await db.get_or_create_quest(G, A, 100, "catch_any", 10)
    assert quest == {
        "quest_key": "catch_any",
        "target": 10,
        "progress": 0,
        "claimed": False,
    }
    # Re-reading the same day with different generated params doesn't overwrite.
    again = await db.get_or_create_quest(G, A, 100, "earn_coins", 999)
    assert again == quest


async def test_bump_quest_progress_clamps_at_target():
    await db.get_or_create_quest(G, A, 100, "catch_any", 5)
    assert await db.bump_quest_progress(G, A, 100, 3) == 3
    assert await db.bump_quest_progress(G, A, 100, 10) == 5  # clamped


async def test_bump_quest_progress_zero_or_negative_is_a_noop():
    await db.get_or_create_quest(G, A, 100, "catch_any", 5)
    assert await db.bump_quest_progress(G, A, 100, 0) == 0
    assert await db.bump_quest_progress(G, A, 100, -3) == 0


async def test_try_claim_quest_reward_requires_target_and_guards_double_claim():
    await db.get_or_create_quest(G, A, 100, "catch_any", 5)
    # Not at target yet.
    assert await db.try_claim_quest_reward(G, A, 100) is False
    await db.bump_quest_progress(G, A, 100, 5)
    assert await db.try_claim_quest_reward(G, A, 100) is True
    # A second claim on the same completed quest must fail.
    assert await db.try_claim_quest_reward(G, A, 100) is False


async def test_quests_are_isolated_per_day_and_user():
    await db.get_or_create_quest(G, A, 100, "catch_any", 5)
    await db.bump_quest_progress(G, A, 100, 5)
    await db.try_claim_quest_reward(G, A, 100)
    # A new day for the same user is a fresh, unclaimed quest.
    fresh = await db.get_or_create_quest(G, A, 101, "catch_any", 5)
    assert fresh["progress"] == 0
    assert fresh["claimed"] is False
    # A different user on the same day is independent too.
    other = await db.get_or_create_quest(G, B, 100, "catch_any", 5)
    assert other["progress"] == 0
    assert other["claimed"] is False


# ── Bait charge consumption (shared items/effects layer) ─────────────────────────
async def test_fish_bait_effect_charges_deplete_on_consume():
    await db.grant_effect(G, A, "fish_bait", 0.12, uses=2)
    effects = await db.get_active_effects(G, A)
    assert effects["fish_bait"]["magnitude"] == 0.12
    assert effects["fish_bait"]["uses_left"] == 2

    assert await db.consume_effect_use(G, A, "fish_bait") is True
    assert (await db.get_active_effects(G, A))["fish_bait"]["uses_left"] == 1
    assert await db.consume_effect_use(G, A, "fish_bait") is True
    # Charges exhausted: the effect row is gone, and a further consume fails.
    assert "fish_bait" not in await db.get_active_effects(G, A)
    assert await db.consume_effect_use(G, A, "fish_bait") is False


async def test_consume_effect_use_missing_effect_fails():
    assert await db.consume_effect_use(G, A, "fish_bait") is False


# ── Global (cross-guild) leaderboard ───────────────────────────────────────────
G2 = 888  # a second guild, to prove global stats span every guild


async def test_global_stats_registry_has_expected_keys():
    assert set(db.GLOBAL_STATS) == {
        "earned",
        "caught",
        "casts",
        "best_weight",
        "xp",
        "streak",
    }


async def test_global_leaderboard_aggregates_across_guilds():
    await db.add_fishing_earned(G, A, 100)
    await db.add_fishing_earned(G2, A, 50)  # same user, different guild
    await db.add_fishing_earned(G, B, 80)

    board = await db.get_global_leaderboard("earned")
    values = {row["user_id"]: row["value"] for row in board}
    assert values[A] == 150  # 100 + 50 summed across both guilds
    assert values[B] == 80
    assert board[0]["user_id"] == A  # highest first


async def test_global_leaderboard_caught_and_best_weight():
    await db.record_catch(G, A, "salmon", 5.0, 40)
    await db.record_catch(G2, A, "tuna", 20.0, 70)
    board = await db.get_global_leaderboard("caught")
    assert {row["user_id"]: row["value"] for row in board}[A] == 2

    board = await db.get_global_leaderboard("best_weight")
    assert {row["user_id"]: row["value"] for row in board}[A] == 20.0


async def test_global_rank_and_count():
    await db.add_fishing_earned(G, A, 200)
    await db.add_fishing_earned(G2, B, 500)
    assert await db.get_global_rank("earned", B) == (1, 500)
    assert await db.get_global_rank("earned", A) == (2, 200)
    assert await db.count_global_leaderboard("earned") == 2


async def test_global_rank_none_without_stat():
    assert await db.get_global_rank("earned", A) is None


async def test_global_stat_key_is_whitelisted():
    with pytest.raises(ValueError):
        await db.get_global_leaderboard("coins; DROP TABLE fishing_stats;--")
    with pytest.raises(ValueError):
        await db.get_global_rank("nonsense", A)
    with pytest.raises(ValueError):
        await db.count_global_leaderboard("nonsense")
