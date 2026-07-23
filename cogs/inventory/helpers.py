"""cogs/inventory/helpers.py — pure, Discord-free inventory helpers.

Random decisions take an explicit roll in [0, 1) (the resolve_gamble pattern)
so they stay deterministic under test.
"""

from .constants import CHEST_COINS_MAX, CHEST_COINS_MIN


def chest_payout(roll: float) -> int:
    """Coins inside a treasure chest for a roll in [0, 1)."""
    roll = min(max(roll, 0.0), 1.0)
    return int(CHEST_COINS_MIN + roll * (CHEST_COINS_MAX - CHEST_COINS_MIN))
