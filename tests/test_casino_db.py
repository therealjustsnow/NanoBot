"""
Tests for the casino accessors in utils/db/ — in-memory SQLite.
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
    await db._ensure_casino_tables()
    # Payouts credit through the economy tables.
    await db._ensure_economy_tables()
    yield conn
    await conn.close()


G = 777
A, B = 1, 2


# ── Config ─────────────────────────────────────────────────────────────────────
async def test_config_defaults():
    cfg = await db.get_casino_config(G)
    assert cfg == {"enabled": True, "min_bet": 10, "max_bet": 1000, "jackpot_pool": 0}


async def test_config_roundtrip_partial_update():
    await db.set_casino_config(G, min_bet=25)
    cfg = await db.get_casino_config(G)
    assert cfg["min_bet"] == 25
    assert cfg["max_bet"] == 1000  # untouched key keeps its default
    assert cfg["enabled"] is True

    await db.set_casino_config(G, enabled=False, max_bet=5000)
    cfg = await db.get_casino_config(G)
    assert cfg["enabled"] is False
    assert cfg["max_bet"] == 5000
    assert cfg["min_bet"] == 25


async def test_set_config_never_touches_jackpot_pool():
    await db.add_to_jackpot(G, 500)
    await db.set_casino_config(G, min_bet=50)
    assert (await db.get_casino_config(G))["jackpot_pool"] == 500


# ── Jackpot ────────────────────────────────────────────────────────────────────
async def test_add_to_jackpot_accumulates():
    assert await db.add_to_jackpot(G, 100) == 100
    assert await db.add_to_jackpot(G, 50) == 150
    assert (await db.get_casino_config(G))["jackpot_pool"] == 150


async def test_add_to_jackpot_zero_is_noop():
    assert await db.add_to_jackpot(G, 0) == 0
    assert (await db.get_casino_config(G))["jackpot_pool"] == 0


async def test_claim_jackpot_zeroes_and_returns_pot():
    await db.add_to_jackpot(G, 777)
    assert await db.try_claim_jackpot(G) == 777
    assert (await db.get_casino_config(G))["jackpot_pool"] == 0


async def test_claim_empty_jackpot_returns_zero():
    assert await db.try_claim_jackpot(G) == 0


async def test_claim_jackpot_is_a_one_shot_race_guard():
    await db.add_to_jackpot(G, 200)
    # Simulate a race: both readers see pot=200 before either claims.
    pot_seen = (await db.get_casino_config(G))["jackpot_pool"]
    assert pot_seen == 200
    first = await db.try_claim_jackpot(G)
    second = await db.try_claim_jackpot(G)
    assert first == 200
    assert second == 0  # loser gets nothing — pot already zeroed


async def test_jackpot_survives_across_guilds():
    await db.add_to_jackpot(G, 100)
    await db.add_to_jackpot(999, 50)
    assert (await db.get_casino_config(G))["jackpot_pool"] == 100
    assert (await db.get_casino_config(999))["jackpot_pool"] == 50


# ── Player stats ───────────────────────────────────────────────────────────────
async def test_stats_defaults():
    assert await db.get_casino_stats(G, A) == {
        "games": 0,
        "wagered": 0,
        "won": 0,
        "biggest_win": 0,
        "streak": 0,
        "best_streak": 0,
    }


async def test_record_loss_resets_streak_and_tracks_wagered():
    stats = await db.record_casino_game(G, A, 100, 0)
    assert stats == {
        "games": 1,
        "wagered": 100,
        "won": 0,
        "biggest_win": 0,
        "streak": 0,
        "best_streak": 0,
    }


async def test_record_win_bumps_streak_and_biggest_win():
    stats = await db.record_casino_game(G, A, 100, 192)
    assert stats["games"] == 1
    assert stats["won"] == 192
    assert stats["biggest_win"] == 192
    assert stats["streak"] == 1
    assert stats["best_streak"] == 1


async def test_record_push_neither_extends_nor_resets_streak():
    await db.record_casino_game(G, A, 100, 192)  # win -> streak 1
    stats = await db.record_casino_game(G, A, 100, 100)  # push
    assert stats["streak"] == 1  # unchanged
    assert stats["biggest_win"] == 192  # push isn't a bigger "win"
    assert stats["games"] == 2
    assert stats["wagered"] == 200
    assert stats["won"] == 292


async def test_win_streak_then_loss_resets():
    await db.record_casino_game(G, A, 100, 192)
    await db.record_casino_game(G, A, 100, 192)
    stats = await db.record_casino_game(G, A, 100, 192)
    assert stats["streak"] == 3
    assert stats["best_streak"] == 3
    stats = await db.record_casino_game(G, A, 100, 0)  # loss
    assert stats["streak"] == 0
    assert stats["best_streak"] == 3  # best is retained


async def test_biggest_win_only_grows():
    await db.record_casino_game(G, A, 100, 500)
    await db.record_casino_game(G, A, 100, 150)
    assert (await db.get_casino_stats(G, A))["biggest_win"] == 500


# ── Leaderboard (net winnings) ──────────────────────────────────────────────────
async def test_leaderboard_orders_by_net():
    await db.record_casino_game(G, A, 100, 50)  # net -50
    await db.record_casino_game(G, B, 100, 300)  # net +200
    board = await db.get_casino_leaderboard(G)
    assert [r["user_id"] for r in board] == [B, A]
    assert board[0]["net"] == 200
    assert board[1]["net"] == -50


async def test_rank_none_without_games():
    assert await db.get_casino_rank(G, A) is None


async def test_rank_and_count():
    await db.record_casino_game(G, A, 100, 50)
    await db.record_casino_game(G, B, 100, 300)
    assert await db.count_casino_players(G) == 2
    assert await db.get_casino_rank(G, B) == (1, 200)
    assert await db.get_casino_rank(G, A) == (2, -50)


async def test_stats_and_leaderboard_scoped_per_guild():
    await db.record_casino_game(G, A, 100, 50)
    await db.record_casino_game(999, A, 100, 300)
    assert (await db.get_casino_stats(G, A))["wagered"] == 100
    assert (await db.get_casino_stats(999, A))["wagered"] == 100
    assert await db.count_casino_players(G) == 1
