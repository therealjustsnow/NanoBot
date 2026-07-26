"""
Tests for the retention / WAL / VACUUM janitor in utils/db/maintenance.py.

The calendar-keyed tables (a daily fishing quest, two daily casino challenges,
three weekly objectives per member) grow forever otherwise, and a connection
that never closes never checkpoints its WAL or reclaims freed pages.

These tests care about two things: that the sweep removes exactly the rows
nobody reads any more, and that it never touches the ones that are still load
bearing — `shop_purchases` in particular, which *is* the per-user-limit ledger.
"""

import time

import aiosqlite
import pytest

import utils.db as db
from utils.db import maintenance

DAY = 86400
NOW = 1_800_000_000.0  # fixed clock so day/week maths is deterministic
TODAY = int(NOW // DAY)


@pytest.fixture(autouse=True)
async def database(monkeypatch):
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    monkeypatch.setattr(db, "_db", conn)
    await db._ensure_fishing_tables()
    await db._ensure_casino_tables()
    await db._ensure_progression_tables()
    await db._ensure_economy_tables()
    await db._ensure_items_tables()
    yield conn
    await conn.close()


A, B = 1, 2
G = 99


async def _seed_quests(days):
    for day in days:
        await db.get_or_create_quest(A, day, "catch_rare", 5)


# ── Retention ────────────────────────────────────────────────────────────────
async def test_old_daily_rows_are_pruned_and_recent_ones_kept():
    await _seed_quests([TODAY, TODAY - 1, TODAY - 13, TODAY - 14, TODAY - 400])
    removed = await maintenance.prune_history(NOW)

    assert removed["fishing_quests"] == 2  # the 14-day-old one and the ancient one
    async with db._conn().execute("SELECT day FROM fishing_quests") as cur:
        kept = {r["day"] for r in await cur.fetchall()}
    assert kept == {TODAY, TODAY - 1, TODAY - 13}


async def test_casino_challenges_follow_the_same_window():
    for day in (TODAY, TODAY - 30):
        await db.bump_challenge_progress(A, day, "win_games", 3, 1)
    removed = await maintenance.prune_history(NOW)
    assert removed["casino_challenges"] == 1
    assert (await db.get_challenge_progress(A, TODAY, "win_games"))["progress"] == 1
    assert (await db.get_challenge_progress(A, TODAY - 30, "win_games"))[
        "progress"
    ] == 0


async def test_weekly_objectives_prune_by_iso_period():
    """`week` is a lexically-sortable YYYY-Www key, so the cutoff is a string."""
    await db.get_or_create_objective(A, "2027-W05", "obj", 10, 0.0)  # future
    await db.get_or_create_objective(A, "2020-W01", "obj", 10, 0.0)  # ancient
    removed = await maintenance.prune_history(NOW)

    assert removed["weekly_objectives"] == 1
    rows = await db.get_period_objectives(A, "2027-W05")
    assert len(rows) == 1
    assert await db.get_period_objectives(A, "2020-W01") == []


async def test_prune_is_a_no_op_on_fresh_data():
    await _seed_quests([TODAY])
    assert await maintenance.prune_history(NOW) == {}
    assert await maintenance.prune_history(NOW) == {}


async def test_prune_never_touches_the_purchase_ledger():
    """shop_purchases enforces per-user limits — losing a row would let someone
    re-buy a limit-1 item, so it is deliberately outside every policy."""
    item_id = await db.add_shop_item(G, "Role", 10, kind="custom", per_user_limit=1)
    await db.add_coins(A, 100)
    assert (await db.purchase_item(G, item_id, A))["ok"] is True

    await maintenance.prune_history(NOW + 400 * DAY)

    assert await db.count_user_purchases(G, item_id, A) == 1
    assert {p.table for p in maintenance.RETENTION}.isdisjoint(
        {"shop_purchases", "fishing_catches", "achievements_earned"}
    )


def test_every_policy_declares_a_known_cutoff_kind():
    for policy in maintenance.RETENTION:
        assert maintenance._cutoff(policy, NOW) is not None


def test_unknown_cutoff_kind_is_rejected():
    with pytest.raises(ValueError):
        maintenance._cutoff(maintenance.Policy("t", "c", "fortnights", 1), NOW)


# ── WAL / VACUUM ─────────────────────────────────────────────────────────────
async def test_checkpoint_and_free_space_report():
    await _seed_quests([TODAY - 400])
    await maintenance.prune_history(NOW)
    assert await maintenance.checkpoint_wal() >= 0
    free, total = await maintenance.free_space()
    assert total > 0 and free >= 0


async def test_sweep_skips_vacuum_on_a_tidy_database():
    await _seed_quests([TODAY, TODAY - 400])
    summary = await maintenance.sweep(NOW)
    assert summary["pruned"] == {"fishing_quests": 1}
    assert summary["vacuumed"] is False  # nowhere near the free-page threshold


async def test_sweep_survives_a_failing_step(monkeypatch):
    """Housekeeping must never take the bot down."""

    async def _boom(*_a, **_kw):
        raise RuntimeError("nope")

    monkeypatch.setattr(maintenance, "prune_history", _boom)
    summary = await maintenance.sweep(NOW)
    assert summary["pruned"] == {}
    assert "checkpointed" in summary
