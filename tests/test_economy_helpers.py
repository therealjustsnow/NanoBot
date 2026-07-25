"""
Tests for the pure economy helpers in cogs/economy/ (no Discord deps).
"""

from cogs.economy import (
    COIN_MAX,
    DAILY_COOLDOWN,
    STREAK_WINDOW,
    Economy,
    _DEFAULT_SHOP_ITEMS,
    _rank_title,
    _scaled_price,
    compute_daily,
    fmt_coins,
    resolve_gamble,
)


def test_scaled_price_matches_base_at_default_daily():
    # Defaults are tuned to the 100-coin daily, so scaling is a no-op there.
    for spec in _DEFAULT_SHOP_ITEMS:
        assert _scaled_price(spec["price"], 100) == spec["price"]


def test_scaled_price_tracks_daily_and_clamps():
    assert _scaled_price(500, 200) == 1000  # 2x daily → 2x price
    assert _scaled_price(500, 50) == 250  # half daily → half price
    assert _scaled_price(500, 0) == 500  # disabled daily → base
    assert _scaled_price(500, 1) == 10  # rounds/clamps up to the 10-coin floor
    assert _scaled_price(COIN_MAX, 1_000_000) == COIN_MAX  # clamped to ceiling


def test_default_shop_items_are_well_formed():
    names = [spec["name"] for spec in _DEFAULT_SHOP_ITEMS]
    # Unique names — /shop seed relies on the UNIQUE(guild_id, name) constraint.
    assert len(names) == len(set(names))
    for spec in _DEFAULT_SHOP_ITEMS:
        assert 1 <= len(spec["name"]) <= 80
        assert 0 <= spec["price"] <= COIN_MAX
        assert spec["reward"].strip()  # custom items need fulfilment text
        assert len(spec["description"]) <= 200
        assert spec.get("limit", 0) >= 0


async def test_daily_lock_serializes_per_user_and_not_across_users():
    # _daily_lock returns a KeyedLocks hold (an async context manager) keyed by
    # user id alone — the wallet is global, so a member claiming /daily in two
    # servers at once must serialize on the same lock. Different users don't
    # block each other, and idle entries are pruned.
    cog = Economy(bot=None)
    async with cog._daily_lock(2):
        async with cog._daily_lock(3):
            pass
        # The same user must NOT be re-acquirable while held, whichever server
        # the second attempt came from.
        import asyncio

        import pytest

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(cog._daily_lock(2).__aenter__(), timeout=0.05)
    assert len(cog._daily_locks) == 0  # idle entries pruned


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


def test_daily_reject_flags_duplicate_within_window():
    now = 1_000_000.0
    # Claimed 2s ago: a double-fired invocation (one user action delivered
    # twice), not a real cooldown hit. The command swallows the second reply.
    res = compute_daily(now=now, last_daily=now - 2, streak=1, base=100, bonus=10)
    assert res["ok"] is False
    assert res["duplicate"] is True


def test_daily_reject_not_duplicate_after_window():
    now = 1_000_000.0
    # Claimed an hour ago: a genuine "come back tomorrow", shown to the user.
    res = compute_daily(now=now, last_daily=now - 3600, streak=1, base=100, bonus=10)
    assert res["ok"] is False
    assert res["duplicate"] is False


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
