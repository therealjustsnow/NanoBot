"""
Tests for the economy accessors in utils/db.py — in-memory SQLite.
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
    await db._ensure_economy_tables()
    yield conn
    await conn.close()


G = 555
A, B, C = 1, 2, 3


async def test_add_and_clamp():
    assert await db.add_coins(G, A, 100) == 100
    assert await db.add_coins(G, A, 50) == 150
    assert await db.add_coins(G, A, -1000) == 0


async def test_set_is_absolute():
    await db.add_coins(G, A, 500)
    await db.set_coins(G, A, 20)
    assert await db.get_balance(G, A) == 20


async def test_transfer_success():
    await db.add_coins(G, A, 100)
    assert await db.transfer_coins(G, A, B, 40) is True
    assert await db.get_balance(G, A) == 60
    assert await db.get_balance(G, B) == 40


async def test_transfer_insufficient_funds():
    await db.add_coins(G, A, 30)
    assert await db.transfer_coins(G, A, B, 40) is False
    assert await db.get_balance(G, A) == 30
    assert await db.get_balance(G, B) == 0


async def test_transfer_rejects_nonpositive():
    await db.add_coins(G, A, 30)
    assert await db.transfer_coins(G, A, B, 0) is False
    assert await db.transfer_coins(G, A, B, -5) is False


async def test_rank_and_leaderboard():
    await db.add_coins(G, A, 100)
    await db.add_coins(G, B, 300)
    await db.add_coins(G, C, 200)
    assert await db.get_econ_rank(G, B) == (1, 300)
    assert await db.get_econ_rank(G, A) == (3, 100)
    top = await db.get_econ_leaderboard(G, limit=10)
    assert [r["user_id"] for r in top] == [B, C, A]


async def test_rank_none_without_account():
    assert await db.get_econ_rank(G, 99) is None


async def test_leaderboard_excludes_zero():
    await db.add_coins(G, A, 100)
    await db.set_coins(G, B, 0)
    assert await db.count_econ(G) == 1


async def test_reset_one_and_all():
    await db.add_coins(G, A, 100)
    await db.add_coins(G, B, 100)
    assert await db.reset_economy(G, A) == 1
    assert await db.get_balance(G, A) == 0
    assert await db.reset_economy(G) == 1  # only B left
    assert await db.count_econ(G) == 0


async def test_daily_state_round_trip():
    assert await db.get_daily_state(G, A) == (0.0, 0)
    await db.set_daily_state(G, A, 12345.0, 3)
    assert await db.get_daily_state(G, A) == (12345.0, 3)
    # set_daily_state must not clobber an existing balance
    await db.set_coins(G, B, 500)
    await db.set_daily_state(G, B, 999.0, 1)
    assert await db.get_balance(G, B) == 500


async def test_config_defaults_and_partial_update():
    cfg = await db.get_econ_config(G)
    assert cfg["daily_amount"] == 100
    assert cfg["currency_name"] == "NanoCoin"
    assert cfg["currency_emoji"] == "🪙"
    assert cfg["streak_bonus"] == 0

    await db.set_econ_config(G, currency_name="Gold", streak_bonus=25)
    cfg = await db.get_econ_config(G)
    assert cfg["currency_name"] == "Gold"
    assert cfg["streak_bonus"] == 25
    assert cfg["daily_amount"] == 100  # untouched
