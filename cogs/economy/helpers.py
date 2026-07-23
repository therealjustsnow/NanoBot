"""Pure economy helpers (no Discord deps) — covered by tests/test_economy_helpers.py."""

from .constants import (
    COIN_MAX,
    DAILY_COOLDOWN,
    DAILY_DUP_WINDOW,
    GAMBLE_MULTIPLIER,
    GAMBLE_WIN_CHANCE,
    STREAK_WINDOW,
    _RANK_TITLES,
)


def _scaled_price(base: int, daily_amount: int) -> int:
    """Scale a starter price to a guild's daily reward.

    The _DEFAULT_SHOP_ITEMS prices are tuned to the default 100-coin daily, so a
    server running a much bigger/smaller daily would find them trivially cheap or
    impossibly dear. Scale by daily/100 to keep the same "days of saving" feel,
    rounded to a clean multiple of 10 and clamped to [10, COIN_MAX]. A daily of 0
    (disabled) falls back to the base price.
    """
    if daily_amount <= 0:
        return base
    scaled = round(base * daily_amount / 100 / 10) * 10
    return max(10, min(COIN_MAX, scaled))


from utils import helpers as _h


def fmt_coins(amount: int, name: str, emoji: str) -> str:
    """Render a coin amount, e.g. '🪙 1,234 NanoCoins'.

    Thin re-export kept for back-compat (tests and cog.py import it from
    here); the single real implementation lives in utils.helpers.
    """
    return _h.fmt_coins(amount, name, emoji)


def compute_daily(
    now: float, last_daily: float, streak: int, base: int, bonus: int
) -> dict:
    """Decide a daily claim.

    Returns {"ok": False, "retry_after": secs} if still on cooldown, else
    {"ok": True, "total": coins, "streak": new_streak}.
    """
    elapsed = now - last_daily
    if last_daily and elapsed < DAILY_COOLDOWN:
        return {
            "ok": False,
            "retry_after": int(DAILY_COOLDOWN - elapsed),
            "duplicate": elapsed < DAILY_DUP_WINDOW,
        }
    if last_daily and elapsed < STREAK_WINDOW:
        new_streak = streak + 1
    else:
        new_streak = 1
    total = base + bonus * (new_streak - 1)
    return {"ok": True, "total": total, "streak": new_streak}


def resolve_gamble(
    amount: int,
    roll: float,
    win_chance: float = GAMBLE_WIN_CHANCE,
    multiplier: float = GAMBLE_MULTIPLIER,
) -> dict:
    """Resolve a bet given a roll in [0, 1).

    Returns {"won": bool, "delta": net_coin_change}. A win nets
    +round(amount × (multiplier - 1)); a loss nets -amount.
    """
    if roll < win_chance:
        return {"won": True, "delta": round(amount * (multiplier - 1))}
    return {"won": False, "delta": -amount}


def _rank_title(position: int) -> str:
    """Title for a contribution-leaderboard position (1 = top)."""
    for threshold, title in _RANK_TITLES:
        if position <= threshold:
            return title
    return "🌱 Member"
