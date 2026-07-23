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
        "wins": 0,
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
        "wins": 0,
    }


async def test_record_win_bumps_streak_and_biggest_win():
    stats = await db.record_casino_game(G, A, 100, 192)
    assert stats["games"] == 1
    assert stats["won"] == 192
    assert stats["biggest_win"] == 192
    assert stats["streak"] == 1
    assert stats["best_streak"] == 1
    assert stats["wins"] == 1


async def test_record_win_and_loss_wins_column_only_counts_true_wins():
    await db.record_casino_game(G, A, 100, 192)  # win
    await db.record_casino_game(G, A, 100, 100)  # push, not a win
    stats = await db.record_casino_game(G, A, 100, 0)  # loss
    assert stats["wins"] == 1
    assert stats["games"] == 3


async def test_ensure_casino_tables_is_idempotent_for_wins_column():
    # Calling table-setup twice (as init() does across restarts) must not
    # raise on the already-added `wins` column.
    await db._ensure_casino_tables()
    await db._ensure_casino_tables()
    assert await db.get_casino_stats(G, A) == {
        "games": 0,
        "wagered": 0,
        "won": 0,
        "biggest_win": 0,
        "streak": 0,
        "best_streak": 0,
        "wins": 0,
    }


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


# ── Blackjack hand persistence ──────────────────────────────────────────────────
SHOE = [("K", "♣"), ("2", "♦"), ("7", "♥"), ("9", "♠")]
PLAYER = [("9", "♠"), ("7", "♥")]
DEALER = [("2", "♦"), ("K", "♣")]


async def test_create_blackjack_hand_appears_in_open_hands():
    hand_id = await db.create_blackjack_hand(
        G, 555, A, 100, 2, SHOE, PLAYER, DEALER, 1000.0
    )
    open_hands = await db.get_open_blackjack_hands()
    assert len(open_hands) == 1
    row = open_hands[0]
    assert row["hand_id"] == hand_id
    assert row["guild_id"] == G
    assert row["channel_id"] == 555
    assert row["user_id"] == A
    assert row["bet"] == 100
    assert row["streak"] == 2
    assert row["message_id"] is None  # not yet set
    assert row["shoe"] == SHOE
    assert row["player"] == PLAYER
    assert row["dealer"] == DEALER
    assert row["created_at"] == 1000.0


async def test_set_blackjack_message_persists_id():
    hand_id = await db.create_blackjack_hand(
        G, 555, A, 100, 0, SHOE, PLAYER, DEALER, 1000.0
    )
    await db.set_blackjack_message(hand_id, 999)
    row = await db.get_blackjack_hand(hand_id)
    assert row["message_id"] == 999


async def test_update_blackjack_hand_persists_shoe_after_hit():
    hand_id = await db.create_blackjack_hand(
        G, 555, A, 100, 0, SHOE, PLAYER, DEALER, 1000.0
    )
    new_shoe_state = SHOE[:-1]  # simulate popping one card off for a hit
    new_player = PLAYER + [("3", "♣")]
    await db.update_blackjack_hand(hand_id, new_shoe_state, new_player, DEALER)
    row = await db.get_blackjack_hand(hand_id)
    assert row["shoe"] == new_shoe_state
    assert row["player"] == new_player
    assert row["dealer"] == DEALER


async def test_claim_blackjack_hand_deletes_row_and_returns_data():
    hand_id = await db.create_blackjack_hand(
        G, 555, A, 100, 1, SHOE, PLAYER, DEALER, 1000.0
    )
    claimed = await db.claim_blackjack_hand(hand_id)
    assert claimed is not None
    assert claimed["hand_id"] == hand_id
    assert claimed["bet"] == 100
    assert claimed["streak"] == 1
    assert await db.get_open_blackjack_hands() == []
    assert await db.get_blackjack_hand(hand_id) is None


async def test_claim_blackjack_hand_is_a_one_shot_race_guard():
    """Simulates a settle race (button press vs. expiry timer vs. a
    restart-restored expiry): calling the claim accessor twice for the same
    hand must pay out exactly once — the second caller gets None."""
    hand_id = await db.create_blackjack_hand(
        G, 555, A, 100, 0, SHOE, PLAYER, DEALER, 1000.0
    )
    first = await db.claim_blackjack_hand(hand_id)
    second = await db.claim_blackjack_hand(hand_id)
    assert first is not None
    assert second is None


async def test_claim_missing_hand_returns_none():
    assert await db.claim_blackjack_hand(999999) is None


async def test_delete_blackjack_hand_removes_row():
    hand_id = await db.create_blackjack_hand(
        G, 555, A, 100, 0, SHOE, PLAYER, DEALER, 1000.0
    )
    await db.delete_blackjack_hand(hand_id)
    assert await db.get_blackjack_hand(hand_id) is None


async def test_open_hands_scoped_across_guilds_and_users():
    await db.create_blackjack_hand(G, 1, A, 50, 0, SHOE, PLAYER, DEALER, 1.0)
    await db.create_blackjack_hand(999, 2, B, 75, 0, SHOE, PLAYER, DEALER, 2.0)
    open_hands = await db.get_open_blackjack_hands()
    assert len(open_hands) == 2
    guild_ids = {row["guild_id"] for row in open_hands}
    assert guild_ids == {G, 999}


# ── Daily challenges ─────────────────────────────────────────────────────────────
DAY = 19000


async def test_get_challenge_progress_defaults_to_zero():
    assert await db.get_challenge_progress(G, A, DAY, "win_games") == {
        "progress": 0,
        "claimed": False,
    }


async def test_bump_challenge_progress_add_mode_accumulates_and_clamps():
    await db.bump_challenge_progress(G, A, DAY, "play_games", 5, 2, mode="add")
    row = await db.bump_challenge_progress(G, A, DAY, "play_games", 5, 2, mode="add")
    assert row == {"progress": 4, "claimed": False}
    # A third bump would overshoot — progress clamps at target.
    row = await db.bump_challenge_progress(G, A, DAY, "play_games", 5, 2, mode="add")
    assert row["progress"] == 5


async def test_bump_challenge_progress_max_mode_keeps_running_maximum():
    await db.bump_challenge_progress(G, A, DAY, "big_payout", 1000, 300, mode="max")
    row = await db.bump_challenge_progress(
        G, A, DAY, "big_payout", 1000, 150, mode="max"
    )
    assert row["progress"] == 300  # smaller amount doesn't lower the max
    row = await db.bump_challenge_progress(
        G, A, DAY, "big_payout", 1000, 700, mode="max"
    )
    assert row["progress"] == 700


async def test_bump_challenge_progress_add_zero_or_negative_is_noop():
    row = await db.bump_challenge_progress(G, A, DAY, "play_games", 5, 0, mode="add")
    assert row == {"progress": 0, "claimed": False}


async def test_try_claim_challenge_requires_target_reached():
    await db.bump_challenge_progress(G, A, DAY, "win_games", 3, 2, mode="add")
    assert await db.try_claim_challenge(G, A, DAY, "win_games") is False
    await db.bump_challenge_progress(G, A, DAY, "win_games", 3, 1, mode="add")
    assert await db.try_claim_challenge(G, A, DAY, "win_games") is True


async def test_try_claim_challenge_is_a_one_shot_race_guard():
    await db.bump_challenge_progress(G, A, DAY, "win_games", 3, 3, mode="add")
    first = await db.try_claim_challenge(G, A, DAY, "win_games")
    second = await db.try_claim_challenge(G, A, DAY, "win_games")
    assert first is True
    assert second is False  # already claimed — no double payout


async def test_challenges_scoped_per_guild_user_and_day():
    await db.bump_challenge_progress(G, A, DAY, "win_games", 3, 1, mode="add")
    await db.bump_challenge_progress(G, A, DAY + 1, "win_games", 3, 2, mode="add")
    await db.bump_challenge_progress(G, B, DAY, "win_games", 3, 2, mode="add")
    await db.bump_challenge_progress(999, A, DAY, "win_games", 3, 2, mode="add")
    assert (await db.get_challenge_progress(G, A, DAY, "win_games"))["progress"] == 1
    assert (await db.get_challenge_progress(G, A, DAY + 1, "win_games"))[
        "progress"
    ] == 2
    assert (await db.get_challenge_progress(G, B, DAY, "win_games"))["progress"] == 2
    assert (await db.get_challenge_progress(999, A, DAY, "win_games"))["progress"] == 2
