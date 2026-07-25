"""
tests/test_globalize_migration.py
Migration 1 (utils/db/globalize.py): per-guild economy → one global account.

These build a database with the *old* per-(guild, user) schema, populate it the
way a live bot would have, run the migration, and assert the merge rules
documented in globalize.py: nothing earned is lost, permanent unlocks keep
their best value, cooldowns can't be reset by the merge, and an already-claimed
reward stays claimed (so nobody can be paid twice).
"""

import aiosqlite
import pytest

import utils.db as db
from utils.db import globalize

pytestmark = pytest.mark.asyncio

A, B = 1001, 1002  # users
G1, G2 = 501, 502  # the two servers they played in

_OLD_SCHEMA = """
CREATE TABLE economy (
    guild_id TEXT NOT NULL, user_id TEXT NOT NULL,
    coins INTEGER NOT NULL DEFAULT 0, last_daily REAL NOT NULL DEFAULT 0,
    streak INTEGER NOT NULL DEFAULT 0, contribution INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE user_items (
    guild_id TEXT NOT NULL, user_id TEXT NOT NULL, item_key TEXT NOT NULL,
    qty INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (guild_id, user_id, item_key)
);
CREATE TABLE user_effects (
    guild_id TEXT NOT NULL, user_id TEXT NOT NULL, effect_key TEXT NOT NULL,
    magnitude REAL NOT NULL DEFAULT 0, expires_at REAL NOT NULL DEFAULT 0,
    uses_left INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id, effect_key)
);
CREATE TABLE fishing_stats (
    guild_id TEXT NOT NULL, user_id TEXT NOT NULL,
    rod_level INTEGER NOT NULL DEFAULT 0, last_cast REAL NOT NULL DEFAULT 0,
    casts INTEGER NOT NULL DEFAULT 0, caught INTEGER NOT NULL DEFAULT 0,
    earned INTEGER NOT NULL DEFAULT 0, best_key TEXT NOT NULL DEFAULT '',
    best_weight REAL NOT NULL DEFAULT 0, xp INTEGER NOT NULL DEFAULT 0,
    streak_days INTEGER NOT NULL DEFAULT 0, last_day INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE fishing_catches (
    id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL, fish_key TEXT NOT NULL, weight REAL NOT NULL DEFAULT 0,
    value INTEGER NOT NULL DEFAULT 0, caught_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE fishing_species (
    guild_id TEXT NOT NULL, user_id TEXT NOT NULL, fish_key TEXT NOT NULL,
    caught INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (guild_id, user_id, fish_key)
);
CREATE TABLE fishing_quests (
    guild_id TEXT NOT NULL, user_id TEXT NOT NULL, day INTEGER NOT NULL,
    quest_key TEXT NOT NULL, target INTEGER NOT NULL DEFAULT 0,
    progress INTEGER NOT NULL DEFAULT 0, claimed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id, day)
);
CREATE TABLE casino_stats (
    guild_id TEXT NOT NULL, user_id TEXT NOT NULL, games INTEGER NOT NULL DEFAULT 0,
    wagered INTEGER NOT NULL DEFAULT 0, won INTEGER NOT NULL DEFAULT 0,
    biggest_win INTEGER NOT NULL DEFAULT 0, streak INTEGER NOT NULL DEFAULT 0,
    best_streak INTEGER NOT NULL DEFAULT 0, wins INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE casino_challenges (
    guild_id TEXT NOT NULL, user_id TEXT NOT NULL, day INTEGER NOT NULL,
    chal_key TEXT NOT NULL, target INTEGER NOT NULL DEFAULT 0,
    progress INTEGER NOT NULL DEFAULT 0, claimed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id, day, chal_key)
);
CREATE TABLE activities_stats (
    guild_id TEXT NOT NULL, user_id TEXT NOT NULL, last_work REAL NOT NULL DEFAULT 0,
    work_shifts INTEGER NOT NULL DEFAULT 0, last_mine REAL NOT NULL DEFAULT 0,
    mine_count INTEGER NOT NULL DEFAULT 0, pickaxe_level INTEGER NOT NULL DEFAULT 0,
    last_hunt REAL NOT NULL DEFAULT 0, hunt_count INTEGER NOT NULL DEFAULT 0,
    last_explore REAL NOT NULL DEFAULT 0, explore_count INTEGER NOT NULL DEFAULT 0,
    last_rob REAL NOT NULL DEFAULT 0, rob_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE achievements_earned (
    guild_id TEXT NOT NULL, user_id TEXT NOT NULL, key TEXT NOT NULL,
    earned_at REAL NOT NULL DEFAULT 0, PRIMARY KEY (guild_id, user_id, key)
);
CREATE TABLE weekly_objectives (
    guild_id TEXT NOT NULL, user_id TEXT NOT NULL, week TEXT NOT NULL,
    obj_key TEXT NOT NULL, target REAL NOT NULL DEFAULT 0,
    baseline REAL NOT NULL DEFAULT 0, claimed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id, week, obj_key)
);
CREATE TABLE progression (
    guild_id TEXT NOT NULL, user_id TEXT NOT NULL,
    prestige INTEGER NOT NULL DEFAULT 0, selected_title TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (guild_id, user_id)
);
"""


@pytest.fixture
async def legacy(monkeypatch):
    """An in-memory DB carrying the pre-refactor per-guild economy schema."""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    monkeypatch.setattr(db, "_db", conn)
    await conn.executescript(_OLD_SCHEMA)
    await conn.commit()
    yield conn
    await conn.close()


async def _migrate():
    """Run just migration 1, the way init() would on an existing database."""
    await db._run_migrations([(1, globalize.globalize_economy)])


# ── Wallet ────────────────────────────────────────────────────────────────────
async def test_wallets_merge_without_losing_or_inventing_coins(legacy):
    await legacy.executemany(
        "INSERT INTO economy (guild_id, user_id, coins, last_daily, streak, "
        "contribution) VALUES (?,?,?,?,?,?)",
        [
            (str(G1), str(A), 1000, 500.0, 3, 40),
            (str(G2), str(A), 250, 900.0, 7, 60),
            (str(G1), str(B), 75, 0.0, 0, 0),
        ],
    )
    await legacy.commit()
    await _migrate()

    # Every coin the member earned survives, counted exactly once.
    assert await db.get_balance(A) == 1250
    assert await db.get_balance(B) == 75
    assert await db.get_contribution(A) == 100
    # The daily claim keeps the most recent stamp (no free extra claim) and the
    # best streak reached.
    assert await db.get_daily_state(A) == (900.0, 7)


async def test_total_coins_are_conserved(legacy):
    rows = [(str(g), str(u), 100, 0.0, 0, 0) for g in (G1, G2) for u in (A, B)]
    await legacy.executemany(
        "INSERT INTO economy (guild_id, user_id, coins, last_daily, streak, "
        "contribution) VALUES (?,?,?,?,?,?)",
        rows,
    )
    await legacy.commit()
    before = sum(r[2] for r in rows)
    await _migrate()
    after = await db.get_balance(A) + await db.get_balance(B)
    assert after == before  # nothing minted, nothing burned


# ── Items and effects ─────────────────────────────────────────────────────────
async def test_item_stacks_add_up_and_effects_keep_their_value(legacy):
    await legacy.executemany(
        "INSERT INTO user_items (guild_id, user_id, item_key, qty) VALUES (?,?,?,?)",
        [
            (str(G1), str(A), "iron_ore", 4),
            (str(G2), str(A), "iron_ore", 6),
            (str(G2), str(A), "lucky_charm", 1),
        ],
    )
    await legacy.executemany(
        "INSERT INTO user_effects (guild_id, user_id, effect_key, magnitude, "
        "expires_at, uses_left) VALUES (?,?,?,?,?,?)",
        [
            (str(G1), str(A), "fish_bait", 0.05, 0, 3),
            (str(G2), str(A), "fish_bait", 0.25, 0, 2),
        ],
    )
    await legacy.commit()
    await _migrate()

    assert await db.get_item_qty(A, "iron_ore") == 10
    assert await db.get_item_qty(A, "lucky_charm") == 1
    effects = await db.get_active_effects(A)
    assert effects["fish_bait"]["magnitude"] == 0.25  # strongest kept
    assert effects["fish_bait"]["uses_left"] == 5  # every paid-for charge kept


# ── Fishing ───────────────────────────────────────────────────────────────────
async def test_fishing_progress_merges_bag_dex_and_records(legacy):
    await legacy.executemany(
        "INSERT INTO fishing_stats (guild_id, user_id, rod_level, last_cast, casts, "
        "caught, earned, best_key, best_weight, xp, streak_days, last_day) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (str(G1), str(A), 3, 100.0, 40, 30, 900, "tuna", 22.5, 400, 5, 20100),
            (str(G2), str(A), 1, 900.0, 10, 8, 100, "cod", 3.0, 90, 2, 20101),
        ],
    )
    await legacy.executemany(
        "INSERT INTO fishing_catches (guild_id, user_id, fish_key, weight, value, "
        "caught_at) VALUES (?,?,?,?,?,?)",
        [
            (str(G1), str(A), "cod", 2.0, 20, 1.0),
            (str(G2), str(A), "cod", 2.5, 25, 2.0),
        ],
    )
    await legacy.executemany(
        "INSERT INTO fishing_species (guild_id, user_id, fish_key, caught) "
        "VALUES (?,?,?,?)",
        [(str(G1), str(A), "cod", 12), (str(G2), str(A), "cod", 5)],
    )
    await legacy.commit()
    await _migrate()

    fisher = await db.get_fisher(A)
    assert fisher["rod_level"] == 3  # best rod kept
    assert fisher["casts"] == 50 and fisher["caught"] == 38
    assert fisher["earned"] == 1000 and fisher["xp"] == 490
    assert (fisher["best_key"], fisher["best_weight"]) == ("tuna", 22.5)
    assert fisher["last_cast"] == 900.0  # most recent cast — no free cast
    assert fisher["streak_days"] == 5 and fisher["last_day"] == 20101

    bag = await db.get_bag(A)
    assert bag == [{"fish_key": "cod", "qty": 2, "total_value": 45}]
    assert await db.get_species_counts(A) == {"cod": 17}


async def test_claimed_quest_stays_claimed_after_the_merge(legacy):
    """The reward was already paid in one server — the merged row must not
    re-open it, or the member would be paid twice for the same day."""
    await legacy.executemany(
        "INSERT INTO fishing_quests (guild_id, user_id, day, quest_key, target, "
        "progress, claimed) VALUES (?,?,?,?,?,?,?)",
        [
            (str(G1), str(A), 20100, "catch_any", 5, 5, 1),
            (str(G2), str(A), 20100, "catch_any", 5, 2, 0),
        ],
    )
    await legacy.commit()
    await _migrate()

    quest = await db.get_or_create_quest(A, 20100, "catch_any", 5)
    assert quest["claimed"] is True
    assert await db.try_claim_quest_reward(A, 20100) is False


# ── Casino ────────────────────────────────────────────────────────────────────
async def test_casino_stats_merge_and_claimed_challenges_stay_claimed(legacy):
    await legacy.executemany(
        "INSERT INTO casino_stats (guild_id, user_id, games, wagered, won, "
        "biggest_win, streak, best_streak, wins) VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (str(G1), str(A), 20, 2000, 1800, 500, 2, 6, 9),
            (str(G2), str(A), 5, 500, 900, 800, 1, 3, 3),
        ],
    )
    await legacy.executemany(
        "INSERT INTO casino_challenges (guild_id, user_id, day, chal_key, target, "
        "progress, claimed) VALUES (?,?,?,?,?,?,?)",
        [
            (str(G1), str(A), 20100, "win_games", 3, 3, 1),
            (str(G2), str(A), 20100, "win_games", 3, 1, 0),
        ],
    )
    await legacy.commit()
    await _migrate()

    stats = await db.get_casino_stats(A)
    assert stats["games"] == 25 and stats["wagered"] == 2500 and stats["won"] == 2700
    assert stats["biggest_win"] == 800 and stats["best_streak"] == 6
    assert (await db.get_challenge_progress(A, 20100, "win_games"))["claimed"] is True
    assert await db.try_claim_challenge(A, 20100, "win_games") is False


# ── Activities ────────────────────────────────────────────────────────────────
async def test_activity_counts_add_and_cooldowns_keep_the_latest_claim(legacy):
    await legacy.executemany(
        "INSERT INTO activities_stats (guild_id, user_id, last_work, work_shifts, "
        "last_mine, mine_count, pickaxe_level, last_hunt, hunt_count, last_explore, "
        "explore_count, last_rob, rob_count) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (str(G1), str(A), 100.0, 30, 0.0, 5, 2, 0.0, 1, 0.0, 0, 0.0, 0),
            (str(G2), str(A), 950.0, 12, 0.0, 3, 1, 0.0, 0, 0.0, 2, 0.0, 1),
        ],
    )
    await legacy.commit()
    await _migrate()

    stats = await db.get_activity_stats(A)
    assert stats["work_shifts"] == 42 and stats["mine_count"] == 8
    assert stats["pickaxe_level"] == 2  # best pickaxe kept
    assert stats["last_work"] == 950.0  # latest claim wins
    # …and that stamp really does still gate the cooldown.
    assert await db.try_claim_activity(A, "work", 1000.0, 3600) > 0


# ── Progression ───────────────────────────────────────────────────────────────
async def test_achievements_prestige_and_objectives_merge(legacy):
    await legacy.executemany(
        "INSERT INTO achievements_earned (guild_id, user_id, key, earned_at) "
        "VALUES (?,?,?,?)",
        [
            (str(G1), str(A), "fish_caught_10", 500.0),
            (str(G2), str(A), "fish_caught_10", 900.0),
            (str(G2), str(A), "balance_1k", 800.0),
        ],
    )
    await legacy.executemany(
        "INSERT INTO progression (guild_id, user_id, prestige, selected_title) "
        "VALUES (?,?,?,?)",
        [(str(G1), str(A), 1, "Angler"), (str(G2), str(A), 3, "Tycoon")],
    )
    await legacy.executemany(
        "INSERT INTO weekly_objectives (guild_id, user_id, week, obj_key, target, "
        "baseline, claimed) VALUES (?,?,?,?,?,?,?)",
        [
            (str(G1), str(A), "2026-W30", "weekly_fish", 20, 100.0, 1),
            (str(G2), str(A), "2026-W30", "weekly_fish", 20, 50.0, 0),
        ],
    )
    await legacy.commit()
    await _migrate()

    earned = await db.get_earned_achievements(A)
    assert set(earned) == {"fish_caught_10", "balance_1k"}
    assert earned["fish_caught_10"] == 500.0  # earliest award time kept
    assert await db.try_award_achievement(A, "fish_caught_10") is False

    prog = await db.get_progression(A)
    assert prog == {"prestige": 3, "selected_title": "Tycoon"}

    rows = await db.get_period_objectives(A, "2026-W30")
    assert len(rows) == 1
    assert rows[0]["baseline"] == 50.0  # most progress kept
    assert rows[0]["claimed"] is True  # already paid — can't be claimed again


# ── Migration 2: the per-guild cast cooldown is gone ──────────────────────────
async def test_fishing_cooldown_setting_is_dropped(legacy):
    """The cast cooldown became one fixed bot-wide constant; migration 2 drops
    the column while keeping each guild's on/off switch."""
    await legacy.execute(
        "CREATE TABLE fishing_config (guild_id TEXT PRIMARY KEY, "
        "enabled INTEGER NOT NULL DEFAULT 1, cooldown INTEGER NOT NULL DEFAULT 240)"
    )
    await legacy.executemany(
        "INSERT INTO fishing_config (guild_id, enabled, cooldown) VALUES (?,?,?)",
        [(str(G1), 0, 240), (str(G2), 1, 30)],
    )
    await legacy.commit()
    await db._run_migrations([(2, globalize.drop_fishing_cooldown_setting)])

    assert await db.get_fishing_config(G1) == {"enabled": False}
    assert await db.get_fishing_config(G2) == {"enabled": True}
    # Safe to re-run once the column is already gone.
    await db._run_migrations([(2, globalize.drop_fishing_cooldown_setting)])
    assert await db.get_fishing_config(G2) == {"enabled": True}


# ── Re-runnability ────────────────────────────────────────────────────────────
async def test_migration_is_safe_to_run_twice(legacy):
    await legacy.execute(
        "INSERT INTO economy (guild_id, user_id, coins) VALUES (?,?,?)",
        (str(G1), str(A), 500),
    )
    await legacy.commit()
    await _migrate()
    await _migrate()  # a retry after a crash, or a fresh DB that never had guild_id
    assert await db.get_balance(A) == 500


async def test_migration_on_a_fresh_database_is_a_noop(legacy):
    """A database created straight from the new schema has no guild_id columns;
    every step must skip rather than blow up."""
    await legacy.executescript("DROP TABLE economy")
    await legacy.execute(
        "CREATE TABLE economy (user_id TEXT PRIMARY KEY, coins INTEGER NOT NULL "
        "DEFAULT 0, last_daily REAL NOT NULL DEFAULT 0, streak INTEGER NOT NULL "
        "DEFAULT 0, contribution INTEGER NOT NULL DEFAULT 0)"
    )
    await legacy.execute("INSERT INTO economy (user_id, coins) VALUES (?,?)", ("7", 42))
    await legacy.commit()
    await _migrate()
    assert await db.get_balance(7) == 42
