"""Pure economy helpers (no Discord deps) — covered by tests/test_economy_helpers.py."""

from cogs.fishing.constants import CAST_COOLDOWN, FISH
from cogs.fishing.helpers import rarity_odds
from cogs.fishing.spots import DEFAULT_SPOT, fish_pool, spot_odds

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
    """Scale a starter price to the bot-wide daily reward.

    The _DEFAULT_SHOP_ITEMS prices are tuned to the default 100-coin daily, so a
    bot running a much bigger/smaller daily would find them trivially cheap or
    impossibly dear. Scale by daily/100 to keep the same "days of saving" feel,
    rounded to a clean multiple of 10 and clamped to [10, COIN_MAX]. A daily of 0
    (disabled) falls back to the base price.

    Only /shop seed uses this; once an item exists its price is the guild's, and
    nothing rewrites it.
    """
    if daily_amount <= 0:
        return base
    scaled = round(base * daily_amount / 100 / 10) * 10
    return max(10, min(COIN_MAX, scaled))


# ══════════════════════════════════════════════════════════════════════════════
#  The pricing yardstick
# ══════════════════════════════════════════════════════════════════════════════
# A mod setting a shop price is choosing how long they want members to work for
# a reward — but "8,000 coins" says nothing about that on its own, so the number
# was always picked blind. These turn a price into a time, so /shop can show it.
#
# The yardstick is fishing, for one reason: it dwarfs everything else. At the
# 20s cast cooldown an active angler earns ~5,000 coins/hour, where /work pays
# 100/hour and /daily amortises to ~4/hour. Any estimate built on the other
# faucets would be wrong by more than an order of magnitude, so the honest
# figure is "how long would you have to fish for this". It is a floor on the
# real answer — nobody fishes continuously — and the /shop footer says so.
#
# Borrowing pure catalogue data + pure helpers from cogs/fishing follows the
# same precedent as cogs/progression reading the fishing/activities ladders.
# tests/test_economy_balance.py recomputes this independently and asserts the
# two agree, so the yardstick can't drift away from the balance model.
def coins_per_cast(luck: float = 0.0, spot: str = DEFAULT_SPOT) -> float:
    """Expected coins from one cast at `luck`, straight off the drop tables.

    Weight scaling averages out (0.6x-1.4x around the base value) and treasure
    pays its value in coins, so the base value is the right expectation.

    `spot` matters because a fishing spot changes both halves of the sum: it
    bends the rarity table and it adds species to the pool that tier averages
    over. It defaults to the starter spot, which is what the yardstick below
    quotes — see the note there on why that's the honest number.
    """
    return sum(
        chance
        * (
            sum(FISH[key]["value"] for key in fish_pool(rarity, spot))
            / len(fish_pool(rarity, spot))
        )
        for rarity, chance in spot_odds(rarity_odds(luck), spot)
        if fish_pool(rarity, spot)
    )


def coins_per_hour(luck: float = 0.0, spot: str = DEFAULT_SPOT) -> float:
    """Expected coins from an hour of uninterrupted casting at `luck`."""
    return coins_per_cast(luck, spot) * (3600 / CAST_COOLDOWN)


def seconds_to_afford(price: int, luck: float = 0.0) -> int:
    """Roughly how long earning `price` takes, in seconds. 0 for a free item.

    Quoted at the **starter spot**, at zero luck, on purpose. This number is
    shown to a mod pricing a shop reward, and the useful reading is "how long
    would this take someone who has nothing" — a figure that doesn't move when
    a member charters the Trench. Every improvement an angler buys (rod, level,
    bait, spot) makes the real answer shorter than the quote, which is the
    right direction for an estimate to be wrong in, and the /shop footer says
    it's a floor.
    """
    if price <= 0:
        return 0
    rate = coins_per_hour(luck)
    if rate <= 0:  # can't happen with a non-empty catalogue; never divide by it
        return 0
    return max(1, round(price / rate * 3600))


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
