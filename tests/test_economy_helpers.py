"""
Tests for the pure economy helpers in cogs/economy.py (no Discord deps).
"""

from cogs.economy import (
    DAILY_COOLDOWN,
    STREAK_WINDOW,
    Economy,
    _rank_title,
    compute_daily,
    fmt_coins,
    resolve_gamble,
)


def test_daily_lock_is_stable_per_user_and_distinct_across_users():
    cog = Economy(bot=None)
    a = cog._daily_lock(1, 2)
    # Same (guild, user) returns the same lock so the claim is serialized.
    assert cog._daily_lock(1, 2) is a
    # Different user (or guild) gets its own lock — claims don't block each other.
    assert cog._daily_lock(1, 3) is not a
    assert cog._daily_lock(9, 2) is not a


def test_fmt_coins_singular_plural_and_commas():
    assert fmt_coins(1, "NanoCoin", "🪙") == "🪙 **1** NanoCoin"
    assert fmt_coins(2, "NanoCoin", "🪙") == "🪙 **2** NanoCoins"
    assert fmt_coins(1234, "NanoCoin", "🪙") == "🪙 **1,234** NanoCoins"
    assert fmt_coins(0, "NanoCoin", "🪙") == "🪙 **0** NanoCoins"


def test_daily_first_claim():
    res = compute_daily(now=1000.0, last_daily=0, streak=0, base=100, bonus=10)
    assert res == {"ok": True, "total": 100, "streak": 1}


def test_daily_on_cooldown():
    now = 1_000_000.0
    res = compute_daily(now=now, last_daily=now - 100, streak=1, base=100, bonus=10)
    assert res["ok"] is False
    assert res["retry_after"] == DAILY_COOLDOWN - 100


def test_daily_keeps_streak_inside_window():
    now = 1_000_000.0
    # Claimed 25h ago: past cooldown, still within the 48h streak window.
    last = now - (DAILY_COOLDOWN + 3600)
    res = compute_daily(now=now, last_daily=last, streak=3, base=100, bonus=10)
    assert res["ok"] is True
    assert res["streak"] == 4
    assert res["total"] == 100 + 10 * 3


def test_daily_resets_streak_after_window():
    now = 1_000_000.0
    last = now - (STREAK_WINDOW + 1)  # missed the window
    res = compute_daily(now=now, last_daily=last, streak=5, base=100, bonus=10)
    assert res["ok"] is True
    assert res["streak"] == 1
    assert res["total"] == 100


def test_gamble_win_doubles_bet():
    res = resolve_gamble(100, roll=0.0, win_chance=0.45, multiplier=2.0)
    assert res == {"won": True, "delta": 100}


def test_gamble_loss_takes_bet():
    res = resolve_gamble(100, roll=0.9, win_chance=0.45, multiplier=2.0)
    assert res == {"won": False, "delta": -100}


def test_gamble_boundary_is_a_loss():
    # roll == win_chance is a loss (strict <).
    res = resolve_gamble(50, roll=0.45, win_chance=0.45)
    assert res["won"] is False


def test_gamble_multiplier_scales_winnings():
    res = resolve_gamble(100, roll=0.0, win_chance=1.0, multiplier=3.0)
    assert res == {"won": True, "delta": 200}


def test_rank_title_thresholds():
    assert _rank_title(1) == "🏆 Guild Legend"
    assert _rank_title(2) == "💎 Veteran"
    assert _rank_title(3) == "💎 Veteran"
    assert _rank_title(4) == "⭐ Trusted"
    assert _rank_title(10) == "⭐ Trusted"
    assert _rank_title(25) == "🤝 Contributor"
    assert _rank_title(26) == "🌱 Member"
    assert _rank_title(9999) == "🌱 Member"
