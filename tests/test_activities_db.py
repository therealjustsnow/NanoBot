"""
Tests for the activities accessors in utils/db/ — in-memory SQLite.
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
    await db._ensure_activities_tables()
    # /rob and /mine upgrade touch coins/items through the shared tables.
    await db._ensure_economy_tables()
    await db._ensure_items_tables()
    yield conn
    await conn.close()


G = 555
A, B = 1, 2


# ── Config ─────────────────────────────────────────────────────────────────────
async def test_config_defaults():
    cfg = await db.get_activities_config(G)
    assert cfg == {
        "work_enabled": True,
        "mine_enabled": True,
        "hunt_enabled": True,
        "explore_enabled": True,
        "rob_enabled": True,
        "work_cooldown": 3600,
        "mine_cooldown": 1800,
        "hunt_cooldown": 2700,
        "explore_cooldown": 10800,
        "rob_cooldown": 14400,
    }


async def test_config_roundtrip_partial_update():
    await db.set_activities_config(G, rob_enabled=False)
    cfg = await db.get_activities_config(G)
    assert cfg["rob_enabled"] is False
    assert cfg["work_enabled"] is True  # untouched key keeps its default

    await db.set_activities_config(G, work_cooldown=120)
    cfg = await db.get_activities_config(G)
    assert cfg["work_cooldown"] == 120
    assert cfg["rob_enabled"] is False  # earlier partial update stuck


# ── Stats defaults ───────────────────────────────────────────────────────────────
async def test_stats_defaults():
    stats = await db.get_activity_stats(A)
    assert stats == {
        "last_work": 0.0,
        "work_shifts": 0,
        "last_mine": 0.0,
        "mine_count": 0,
        "pickaxe_level": 0,
        "last_hunt": 0.0,
        "hunt_count": 0,
        "last_explore": 0.0,
        "explore_count": 0,
        "last_rob": 0.0,
        "rob_count": 0,
    }


# ── try_claim_activity: atomicity ────────────────────────────────────────────────
async def test_unknown_activity_rejected():
    with pytest.raises(ValueError):
        await db.try_claim_activity(A, "fishing", 1000.0, 60)


async def test_first_claim_succeeds_and_counts():
    assert await db.try_claim_activity(A, "work", 1000.0, 3600) == 0
    stats = await db.get_activity_stats(A)
    assert stats["work_shifts"] == 1
    assert stats["last_work"] == 1000.0


async def test_claim_blocked_inside_cooldown():
    await db.try_claim_activity(A, "mine", 1000.0, 1800)
    retry = await db.try_claim_activity(A, "mine", 1500.0, 1800)
    assert retry == 1300
    stats = await db.get_activity_stats(A)
    # The failed claim didn't touch the row.
    assert stats["mine_count"] == 1
    assert stats["last_mine"] == 1000.0


async def test_claim_allowed_after_cooldown():
    await db.try_claim_activity(A, "hunt", 1000.0, 2700)
    assert await db.try_claim_activity(A, "hunt", 3700.0, 2700) == 0
    stats = await db.get_activity_stats(A)
    assert stats["hunt_count"] == 2


async def test_claim_retry_is_at_least_one_second():
    await db.try_claim_activity(A, "explore", 1000.0, 10800)
    retry = await db.try_claim_activity(A, "explore", 10799.5, 10800)
    assert retry >= 1


async def test_different_activities_have_independent_cooldowns():
    assert await db.try_claim_activity(A, "work", 1000.0, 3600) == 0
    # Claiming /work doesn't block /mine for the same user.
    assert await db.try_claim_activity(A, "mine", 1000.0, 1800) == 0
    stats = await db.get_activity_stats(A)
    assert stats["work_shifts"] == 1
    assert stats["mine_count"] == 1


async def test_different_users_have_independent_cooldowns():
    await db.try_claim_activity(A, "rob", 1000.0, 14400)
    assert await db.try_claim_activity(B, "rob", 1000.0, 14400) == 0


async def test_cooldown_follows_the_user_into_every_server():
    """Cooldowns are per user, not per (guild, user): claiming in one server
    blocks the same activity everywhere, which is what stops /work being
    farmed once per guild."""
    assert await db.try_claim_activity(A, "rob", 1000.0, 14400) == 0
    retry = await db.try_claim_activity(A, "rob", 1000.0, 14400)
    assert retry > 0


# ── Pickaxe upgrades (race guard + refund contract) ──────────────────────────────
async def test_pickaxe_upgrade_from_fresh_row():
    assert await db.set_pickaxe_level(A, 1, expected=0) is True
    assert (await db.get_activity_stats(A))["pickaxe_level"] == 1


async def test_pickaxe_upgrade_race_second_buyer_loses():
    assert await db.set_pickaxe_level(A, 1, expected=0) is True
    # A racing second upgrade still expects level 0 — must fail so the caller
    # knows to refund the coins it already debited.
    assert await db.set_pickaxe_level(A, 1, expected=0) is False
    assert (await db.get_activity_stats(A))["pickaxe_level"] == 1
    assert await db.set_pickaxe_level(A, 2, expected=1) is True


async def test_pickaxe_upgrade_does_not_touch_other_users():
    await db.set_pickaxe_level(A, 1, expected=0)
    assert (await db.get_activity_stats(B))["pickaxe_level"] == 0
