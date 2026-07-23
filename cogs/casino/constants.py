"""Casino cog constants: bet bounds, payout tables, streak-bonus tuning, and the
roulette wheel/slots paytables.

Every payout number below is a deliberate house-edge choice — see the module
docstring in cog.py for the per-game edge summary.
"""

# Per-guild bet bounds (overridable via /casino limit). Config row default.
DEFAULT_MIN_BET = 10
DEFAULT_MAX_BET = 1000

# ── /casino flip ────────────────────────────────────────────────────────────────
# 50/50 coin flip. Win pays 1.92x total (bet returned + 0.92x profit) → house
# edge = 1 - 0.5*1.92 = 4%.
FLIP_PAYOUT = 1.92

# ── /casino dice ────────────────────────────────────────────────────────────────
# You and the dealer each roll 2d6; higher total wins, a tie pushes (bet
# refunded). Win pays 2.1x total.
DICE_PAYOUT = 2.1

# ── /casino roulette ─────────────────────────────────────────────────────────────
# Standard European wheel: single 0, numbers 1-36. Outside bets (red/black/
# odd/even/high/low) pay 2x total — the single-zero pocket is the house edge
# (~2.7%, the classic European-roulette figure). A straight number bet pays 35x
# total on a 1/37 shot (~5.4% edge).
ROULETTE_OUTSIDE_PAYOUT = 2
ROULETTE_NUMBER_PAYOUT = 35
RED_NUMBERS = frozenset(
    {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
)
ROULETTE_SPACES = ("red", "black", "odd", "even", "high", "low")

# ── /casino slots ────────────────────────────────────────────────────────────────
# Reel weights (must sum to 1.0) and paytables. Two-of-a-kind pays a small
# multiplier of the matched symbol; three-of-a-kind pays big; triple 7️⃣ also
# wins the progressive jackpot. Tuned so the cash RTP (excluding the jackpot
# bonus) lands ~95% — see tests/test_casino_helpers.py::test_slots_rtp_in_range.
SLOT_SYMBOLS: list[tuple[str, float]] = [
    ("🍒", 0.30),
    ("🍋", 0.25),
    ("🍇", 0.20),
    ("🔔", 0.15),
    ("💎", 0.07),
    ("7️⃣", 0.03),
]
SLOT_JACKPOT_SYMBOL = "7️⃣"

PAIR_PAYOUTS: dict[str, float] = {
    "🍒": 1.1,
    "🍋": 1.2,
    "🍇": 1.4,
    "🔔": 2.0,
    "💎": 3.0,
    "7️⃣": 5.0,
}
TRIPLE_PAYOUTS: dict[str, float] = {
    "🍒": 3.5,
    "🍋": 5.0,
    "🍇": 7.0,
    "🔔": 11.0,
    "💎": 22.0,
    "7️⃣": 45.0,
}

# ── Win-streak bonus (applies to every game) ─────────────────────────────────────
# 3+ consecutive wins adds +5% payout per streak step above 2, capped at +25%;
# any loss resets the streak to 0 (a push neither extends nor resets it).
STREAK_MIN = 3
STREAK_BONUS_STEP = 0.05
STREAK_BONUS_CAP = 0.25

# ── Progressive jackpot ──────────────────────────────────────────────────────────
# 20% of every net house win (i.e. what a player nets negative on a bet) feeds
# the pool. Triple 7️⃣ on /casino slots claims and reseeds it at 0.
JACKPOT_FEED_RATE = 0.20

# ── /casino blackjack ────────────────────────────────────────────────────────────
BLACKJACK_DECKS = 4
BLACKJACK_TIMEOUT = 90  # seconds a Hit/Stand view waits before auto-standing
BLACKJACK_PAYOUT = 2.0  # regular win: total return
BLACKJACK_NATURAL_PAYOUT = 2.5  # blackjack (3:2): total return

RANKS: tuple[str, ...] = (
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    "10",
    "J",
    "Q",
    "K",
    "A",
)
SUITS: tuple[str, ...] = ("♠", "♥", "♦", "♣")
