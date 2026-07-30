"""
Tests for the price-refund ledger in utils/db/refunds.py — in-memory SQLite.

Nothing in this codebase records what a member paid for a rod or a pickaxe, so a
refund is reconstructed from what they own. That makes the ledger the only thing
standing between a re-run and minted coins, and these are the tests that say so:
the once-only insert, the batch marker that bounds who is eligible at all, and
the deliberate split between crediting and announcing.
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
    await db._ensure_refund_tables()
    yield conn
    await conn.close()


A, B = 1, 2
BATCH = "2026-07-ladder-reprice"


# ── The once-only insert ─────────────────────────────────────────────────────
async def test_a_refund_records_once_and_only_once():
    """The whole safety story. The caller credits coins on True, so a second
    True for the same pair would be a second payout of the same money."""
    assert await db.try_record_refund(A, BATCH, 93_500) is True
    assert await db.try_record_refund(A, BATCH, 93_500) is False
    assert await db.try_record_refund(A, BATCH, 1) is False  # amount is no key


async def test_refunds_do_not_leak_between_members_or_batches():
    assert await db.try_record_refund(A, BATCH, 100) is True
    assert await db.try_record_refund(B, BATCH, 100) is True
    assert await db.try_record_refund(A, "some-later-reprice", 100) is True


async def test_a_recorded_refund_keeps_its_breakdown():
    await db.try_record_refund(A, BATCH, 93_500, "⛏️ Obsidian Pickaxe — 76,000")
    (row,) = await db.get_refunds(A)
    assert row["amount"] == 93_500
    assert "Obsidian" in row["detail"]


# ── The batch marker: who is eligible at all ─────────────────────────────────
async def test_a_batch_is_not_swept_until_it_is_closed():
    assert await db.batch_swept(BATCH) is False
    await db.close_batch(BATCH, users=3, coins=250_000)
    assert await db.batch_swept(BATCH) is True


async def test_closing_a_batch_records_what_it_cost():
    await db.close_batch(BATCH, users=3, coins=250_000)
    totals = await db.refund_batch_totals(BATCH)
    assert (totals["users"], totals["coins"]) == (3, 250_000)
    assert totals["swept_at"] > 0


async def test_an_unswept_batch_reports_zeroes_rather_than_raising():
    """The owner's report asks about every batch, including ones that haven't
    run yet."""
    assert await db.refund_batch_totals("never-ran") == {
        "users": 0,
        "coins": 0,
        "swept_at": 0.0,
    }


async def test_closing_a_batch_twice_updates_rather_than_duplicates():
    """A crash mid-sweep re-runs the batch; the second close must not leave two
    rows claiming different totals."""
    await db.close_batch(BATCH, users=1, coins=100)
    await db.close_batch(BATCH, users=3, coins=250_000)
    assert (await db.refund_batch_totals(BATCH))["users"] == 3


# ── Delivery is separate from payment ────────────────────────────────────────
async def test_a_credited_refund_starts_out_pending():
    await db.try_record_refund(A, BATCH, 500)
    pending = await db.pending_refund_notices()
    assert [(p["user_id"], p["amount"]) for p in pending] == [(A, 500)]


async def test_marking_notified_clears_it_from_the_queue():
    await db.try_record_refund(A, BATCH, 500)
    await db.mark_refund_notified(A, BATCH)
    assert await db.pending_refund_notices() == []


async def test_a_failed_dm_leaves_the_refund_pending_and_paid():
    """The reason the two are separate columns: a member with closed DMs keeps
    the coins and stays in the queue for a later attempt."""
    await db.try_record_refund(A, BATCH, 500)
    # No mark_refund_notified — the send raised.
    assert len(await db.pending_refund_notices()) == 1
    # And the ledger still refuses to pay them a second time.
    assert await db.try_record_refund(A, BATCH, 500) is False


async def test_notifying_one_member_does_not_clear_another():
    await db.try_record_refund(A, BATCH, 500)
    await db.try_record_refund(B, BATCH, 500)
    await db.mark_refund_notified(A, BATCH)
    assert [p["user_id"] for p in await db.pending_refund_notices()] == [B]


async def test_the_queue_is_bounded():
    for user_id in range(1, 11):
        await db.try_record_refund(user_id, BATCH, 100)
    assert len(await db.pending_refund_notices(limit=4)) == 4
