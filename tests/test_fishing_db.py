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
    yield conn
    await conn.close()


G = 777
A, B = 1, 2


# ── Config ─────────────────────────────────────────────────────────────────────
async def test_config_defaults():
    cfg = await db.get_fishing_config(G)
    assert cfg == {"enabled": True, "cooldown": 60}


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
