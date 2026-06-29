"""Economy cog package. Splits the former single-file cog into constants,
pure helpers, views, and the command surface. The flat public API
(`from cogs.economy import fmt_coins, Economy, ...`) is preserved here.
"""

from .constants import COIN_MAX, DAILY_COOLDOWN, STREAK_WINDOW, _DEFAULT_SHOP_ITEMS
from .helpers import (
    compute_daily,
    fmt_coins,
    resolve_gamble,
    _rank_title,
    _scaled_price,
)
from .cog import Economy, setup

__all__ = [
    "COIN_MAX",
    "DAILY_COOLDOWN",
    "STREAK_WINDOW",
    "_DEFAULT_SHOP_ITEMS",
    "Economy",
    "compute_daily",
    "fmt_coins",
    "resolve_gamble",
    "_rank_title",
    "_scaled_price",
    "setup",
]
