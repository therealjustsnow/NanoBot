"""
Tests for the ticket DB accessors in utils/db/ (in-memory SQLite).
"""

import aiosqlite
import pytest

import utils.db as db


@pytest.fixture(autouse=True)
async def database(monkeypatch):
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    monkeypatch.setattr(db, "_db", conn)
    await db._ensure_ticket_tables()
    yield conn
    await conn.close()
    monkeypatch.setattr(db, "_db", None)


# ── config ─────────────────────────────────────────────────────────────────────


async def test_config_defaults_when_unset():
    cfg = await db.get_ticket_config(1)
    assert cfg["enabled"] is False
    assert cfg["staff_role_id"] is None
    assert cfg["log_channel_id"] is None
    assert cfg["panel_channel_id"] is None
    assert cfg["max_open"] == 3


async def test_config_set_and_roundtrip():
    await db.set_ticket_config(
        1, enabled=True, staff_role_id="7", log_channel_id="8", max_open=5
    )
    cfg = await db.get_ticket_config(1)
    assert cfg["enabled"] is True
    assert cfg["staff_role_id"] == "7"
    assert cfg["log_channel_id"] == "8"
    assert cfg["max_open"] == 5


async def test_config_partial_update_preserves_other_fields():
    await db.set_ticket_config(1, staff_role_id="7", panel_title="Help Desk")
    await db.set_ticket_config(1, enabled=True)
    cfg = await db.get_ticket_config(1)
    assert cfg["enabled"] is True
    assert cfg["staff_role_id"] == "7"  # preserved
    assert cfg["panel_title"] == "Help Desk"  # preserved


# ── ticket lifecycle ───────────────────────────────────────────────────────────


async def test_create_ticket_numbers_are_per_guild_and_sequential():
    t1 = await db.create_ticket(1, 100, "first")
    t2 = await db.create_ticket(1, 101, "second")
    other = await db.create_ticket(2, 100, "other guild")
    assert (t1["number"], t2["number"]) == (1, 2)
    assert other["number"] == 1  # counter is per-guild
    assert t1["status"] == "open"
    assert t1["thread_id"] is None
    assert t1["subject"] == "first"


async def test_thread_attach_and_lookup():
    t = await db.create_ticket(1, 100, "s")
    await db.set_ticket_thread(t["id"], 555)
    found = await db.get_ticket_by_thread(555)
    assert found is not None and found["id"] == t["id"]
    assert await db.get_ticket_by_thread(999) is None


async def test_delete_ticket_rolls_back_reservation():
    t = await db.create_ticket(1, 100, "s")
    await db.delete_ticket(t["id"])
    assert await db.get_ticket(t["id"]) is None
    assert await db.count_open_tickets(1, 100) == 0
    # The freed number is reused — MAX(number)+1 doesn't leave gaps.
    t2 = await db.create_ticket(1, 100, "again")
    assert t2["number"] == 1


async def test_open_count_and_per_user_scoping():
    a = await db.create_ticket(1, 100, "a")
    await db.create_ticket(1, 100, "b")
    await db.create_ticket(1, 200, "other user")
    await db.create_ticket(2, 100, "other guild")
    assert await db.count_open_tickets(1, 100) == 2
    await db.close_ticket(a["id"], 100, None)
    assert await db.count_open_tickets(1, 100) == 1
    mine = await db.get_open_tickets_for_user(1, 100)
    assert [t["subject"] for t in mine] == ["b"]


async def test_close_ticket_records_and_wins_only_once():
    t = await db.create_ticket(1, 100, "s")
    assert await db.close_ticket(t["id"], 900, "resolved") is True
    # A racing second closer loses.
    assert await db.close_ticket(t["id"], 901, "me too") is False
    fresh = await db.get_ticket(t["id"])
    assert fresh["status"] == "closed"
    assert fresh["closed_by"] == "900"
    assert fresh["close_reason"] == "resolved"
    assert fresh["closed_at"] is not None


async def test_close_ticket_allows_null_closer():
    # Thread-deleted closes have no human closer.
    t = await db.create_ticket(1, 100, "s")
    assert await db.close_ticket(t["id"], None, "thread deleted") is True
    assert (await db.get_ticket(t["id"]))["closed_by"] is None


async def test_claim_wins_only_once_and_needs_open():
    t = await db.create_ticket(1, 100, "s")
    assert await db.claim_ticket(t["id"], 900) is True
    assert await db.claim_ticket(t["id"], 901) is False  # already claimed
    assert (await db.get_ticket(t["id"]))["claimed_by"] == "900"

    t2 = await db.create_ticket(1, 100, "s2")
    await db.close_ticket(t2["id"], 900, None)
    assert await db.claim_ticket(t2["id"], 900) is False  # closed


async def test_close_stale_tickets_sweeps_only_threadless_open_rows():
    stale = await db.create_ticket(1, 100, "never got a thread")
    live = await db.create_ticket(1, 101, "fine")
    await db.set_ticket_thread(live["id"], 555)
    done = await db.create_ticket(1, 102, "closed already")
    await db.close_ticket(done["id"], 900, None)

    assert await db.close_stale_tickets() == 1
    assert (await db.get_ticket(stale["id"]))["status"] == "closed"
    assert (await db.get_ticket(live["id"]))["status"] == "open"
    # Idempotent: nothing left to sweep.
    assert await db.close_stale_tickets() == 0
