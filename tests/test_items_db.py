"""
Tests for the generic inventory/effects/events accessors in utils/db/ —
in-memory SQLite.
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
    await db._ensure_items_tables()
    yield conn
    await conn.close()


G = 777
A, B = 1, 2


# ── Inventory ──────────────────────────────────────────────────────────────────
async def test_inventory_starts_empty():
    assert await db.get_inventory(A) == []
    assert await db.get_item_qty(A, "worm") == 0


async def test_add_and_stack_items():
    assert await db.add_item(A, "worm", 3) == 3
    assert await db.add_item(A, "worm", 2) == 5
    await db.add_item(A, "iron_ore", 1)
    inv = await db.get_inventory(A)
    assert {s["item_key"]: s["qty"] for s in inv} == {"worm": 5, "iron_ore": 1}


async def test_consume_within_stock():
    await db.add_item(A, "worm", 3)
    assert await db.try_consume_item(A, "worm", 2) is True
    assert await db.get_item_qty(A, "worm") == 1


async def test_consume_over_stock_fails_and_keeps_qty():
    await db.add_item(A, "worm", 1)
    assert await db.try_consume_item(A, "worm", 2) is False
    assert await db.try_consume_item(A, "worm", 0) is False
    assert await db.try_consume_item(B, "worm", 1) is False
    assert await db.get_item_qty(A, "worm") == 1


async def test_zero_qty_stacks_hidden():
    await db.add_item(A, "worm", 1)
    await db.try_consume_item(A, "worm", 1)
    assert await db.get_inventory(A) == []


async def test_transfer_item():
    await db.add_item(A, "worm", 2)
    assert await db.transfer_item(A, B, "worm", 2) is True
    assert await db.get_item_qty(A, "worm") == 0
    assert await db.get_item_qty(B, "worm") == 2
    assert await db.transfer_item(A, B, "worm", 1) is False


# ── Effects ────────────────────────────────────────────────────────────────────
async def test_timed_effect_lifecycle():
    await db.grant_effect(A, "luck", 0.5, duration=60, now=1000.0)
    effs = await db.get_active_effects(A, now=1030.0)
    assert effs["luck"]["magnitude"] == 0.5
    assert effs["luck"]["expires_at"] == 1060.0
    # Expired → pruned on read.
    assert await db.get_active_effects(A, now=1060.0) == {}


async def test_regrant_replaces_effect():
    await db.grant_effect(A, "luck", 0.5, duration=60, now=1000.0)
    await db.grant_effect(A, "luck", 0.2, duration=600, now=1010.0)
    effs = await db.get_active_effects(A, now=1030.0)
    assert effs["luck"]["magnitude"] == 0.2
    assert effs["luck"]["expires_at"] == 1610.0


async def test_charge_effect_consumes_to_deletion():
    await db.grant_effect(A, "bait", 1.0, uses=2)
    assert await db.consume_effect_use(A, "bait") is True
    effs = await db.get_active_effects(A)
    assert effs["bait"]["uses_left"] == 1
    assert await db.consume_effect_use(A, "bait") is True
    assert await db.get_active_effects(A) == {}
    assert await db.consume_effect_use(A, "bait") is False


async def test_clear_effect():
    await db.grant_effect(A, "luck", 0.5, duration=60, now=1000.0)
    await db.clear_effect(A, "luck")
    assert await db.get_active_effects(A, now=1000.0) == {}


# ── Economy events ─────────────────────────────────────────────────────────────
async def test_guild_event_visible_only_in_guild():
    await db.start_event(G, "frenzy", 2.0, 600, now=1000.0)
    active = await db.get_active_events(G, now=1100.0)
    assert [e["event_key"] for e in active] == ["frenzy"]
    assert active[0]["magnitude"] == 2.0
    assert await db.get_active_events(G + 1, now=1100.0) == []


async def test_global_event_visible_everywhere():
    await db.start_event(None, "double_xp", 2.0, 600, now=1000.0)
    assert [e["event_key"] for e in await db.get_active_events(G, now=1100.0)] == [
        "double_xp"
    ]
    assert [e["event_key"] for e in await db.get_active_events(G + 1, now=1100.0)] == [
        "double_xp"
    ]


async def test_events_expire_and_prune():
    await db.start_event(G, "frenzy", 2.0, 600, now=1000.0)
    assert await db.get_active_events(G, now=1600.0) == []
    # Pruned, not just filtered.
    async with db._conn().execute("SELECT COUNT(*) FROM economy_events") as cur:
        assert (await cur.fetchone())[0] == 0


async def test_end_event():
    eid = await db.start_event(G, "frenzy", 2.0, 600, now=1000.0)
    await db.end_event(eid)
    assert await db.get_active_events(G, now=1001.0) == []


async def test_event_data_roundtrip():
    await db.start_event(G, "migration", 1.0, 600, data={"species": "koi"}, now=1000.0)
    active = await db.get_active_events(G, now=1001.0)
    assert active[0]["data"] == {"species": "koi"}
