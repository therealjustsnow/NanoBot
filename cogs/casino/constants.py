"""Casino cog constants: bet bounds, payout tables, streak-bonus tuning, and the
roulette wheel/slots paytables.

Every payout number below is a deliberate house-edge choice — see the module
docstring in cog.py for the per-game edge summary.
"""

# Per-guild bet bounds (overridable via /casino limit). Config row default.
# Scaled 1:1 with income when the cast cooldown went 60s -> 20s: the point of a
# bet cap is how much of an hour's earnings a member can put on one spin, and
# leaving it at 1,000 would have quietly made the casino a third as relevant.
# Migration 3 moves guilds still sitting on the old defaults; a guild that set
# its own limits with /casino limit keeps them.
DEFAULT_MIN_BET = 25
DEFAULT_MAX_BET = 3000

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
BLACKJACK_PAYOUT = 2.0  # regular win: total return
BLACKJACK_NATURAL_PAYOUT = 2.5  # blackjack (3:2): total return

# An open hand (bet already debited) is persisted in casino_blackjack so a
# restart can resume or auto-settle it instead of orphaning the bet. BJ_TIMEOUT
# is how long a hand can sit with no Hit/Stand before a cog-owned timer auto-
# stands it (dealer plays out, payout settles, message updated if reachable) —
# the same "persist + cog-owned expiry timer" pattern as economy's /raid.
BJ_TIMEOUT = 600  # 10 minutes

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

# ── Daily casino challenges ───────────────────────────────────────────────────────
# Two challenges/day per member (deterministic per (guild, user, UTC day) — see
# helpers.generate_challenges, same seeded-Random pattern as fishing's daily
# quest), auto-claimed the instant progress reaches target.
#
# Reward-sizing goal: a completed challenge shouldn't, on average, hand back
# more coins than the house already expects to keep from the wagering the
# challenge required — the reward rides on top of house-edge revenue instead
# of minting new coins. The per-game edges documented above run ~2.7%-8%;
# CHALLENGE_EDGE_ASSUMPTION deliberately uses a low-end figure so the derived
# reward undercounts (never overstates) the real expected house take.
CHALLENGE_EDGE_ASSUMPTION = 0.05
CHALLENGE_COUNT = 2  # challenges generated/shown per member per day
CHALLENGE_REWARD_MIN = 150
CHALLENGE_REWARD_MAX = 300

CHALLENGE_POOL = ("win_games", "play_games", "wager_coins", "big_payout")

# (low, high) inclusive target range per challenge kind.
CHALLENGE_TARGET_RANGE = {
    "win_games": (3, 6),  # win this many games today (any game)
    "play_games": (8, 14),  # play this many games today (any outcome)
    "wager_coins": (3000, 6000),  # wager this many coins total today
    "big_payout": (500, 1200),  # land one payout this big in a single game
}

# Flat reward for challenge kinds whose target isn't itself a coin amount, so
# there's nothing sensible to scale the reward against — kept modest and
# within the CHALLENGE_REWARD_MIN/MAX band. `wager_coins` instead computes
# reward = target * CHALLENGE_EDGE_ASSUMPTION (clamped to the same band) —
# see helpers.challenge_reward — tying it directly to (and below) the house's
# expected take on the wagering that challenge demanded.
CHALLENGE_FLAT_REWARD = {
    "win_games": 150,
    "play_games": 150,
    "big_payout": 250,
}

# Compact noun used in the one-line game-result footer hint, e.g.
# "Challenge: 2/3 wins". The full sentence (used by /casino challenge) comes
# from helpers.challenge_label instead.
CHALLENGE_SHORT_LABEL = {
    "win_games": "wins",
    "play_games": "games",
    "wager_coins": "coins wagered",
    "big_payout": "big payout",
}
