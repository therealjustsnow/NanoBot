"""Pure casino helpers (no Discord deps) — covered by tests/test_casino_helpers.py.

Every random decision takes an explicit roll in [0, 1) (or an explicit,
pre-dealt card list) so the cog owns all randomness and these stay
deterministic under test — the same resolve_gamble pattern fishing uses.
"""

import random
from collections import Counter

from .constants import (
    BLACKJACK_NATURAL_PAYOUT,
    BLACKJACK_PAYOUT,
    CHALLENGE_COUNT,
    CHALLENGE_EDGE_ASSUMPTION,
    CHALLENGE_FLAT_REWARD,
    CHALLENGE_POOL,
    CHALLENGE_REWARD_MAX,
    CHALLENGE_REWARD_MIN,
    CHALLENGE_SHORT_LABEL,
    CHALLENGE_TARGET_RANGE,
    DICE_PAYOUT,
    FLIP_PAYOUT,
    PAIR_PAYOUTS,
    RANKS,
    RED_NUMBERS,
    ROULETTE_NUMBER_PAYOUT,
    ROULETTE_OUTSIDE_PAYOUT,
    ROULETTE_SPACES,
    SLOT_JACKPOT_SYMBOL,
    SLOT_SYMBOLS,
    STREAK_BONUS_CAP,
    STREAK_BONUS_STEP,
    STREAK_MIN,
    SUITS,
    TRIPLE_PAYOUTS,
)


# ── Win-streak bonus ─────────────────────────────────────────────────────────────
def streak_bonus(streak: int) -> float:
    """Payout bonus fraction for a (post-win) consecutive-win streak.

    0 below STREAK_MIN; +STREAK_BONUS_STEP per step above STREAK_MIN - 1,
    capped at STREAK_BONUS_CAP. streak=3 -> 0.05, streak=7+ -> capped at 0.25.
    """
    if streak < STREAK_MIN:
        return 0.0
    steps = streak - (STREAK_MIN - 1)
    return min(STREAK_BONUS_CAP, STREAK_BONUS_STEP * steps)


def apply_streak_bonus(payout: int, streak: int) -> int:
    """Boost a winning payout by the streak bonus for `streak` (post-win count)."""
    bonus = streak_bonus(streak)
    return round(payout * (1 + bonus)) if bonus else payout


# ── /casino flip ─────────────────────────────────────────────────────────────────
def resolve_flip(bet: int, choice: str, roll: float) -> dict:
    """Resolve a coin flip. `choice` is 'heads' or 'tails'; `roll` in [0, 1)."""
    result = "heads" if roll < 0.5 else "tails"
    won = result == choice.strip().lower()
    payout = round(bet * FLIP_PAYOUT) if won else 0
    return {"result": result, "won": won, "payout": payout}


# ── /casino dice ─────────────────────────────────────────────────────────────────
def roll_die(roll: float) -> int:
    """Map a roll in [0, 1) onto a d6 face (1-6)."""
    return min(6, int(roll * 6) + 1)


def resolve_dice(
    bet: int, player_rolls: tuple[float, float], dealer_rolls: tuple[float, float]
) -> dict:
    """Resolve a 2d6-vs-dealer round. Higher total wins; a tie pushes."""
    player = [roll_die(r) for r in player_rolls]
    dealer = [roll_die(r) for r in dealer_rolls]
    player_total, dealer_total = sum(player), sum(dealer)
    if player_total > dealer_total:
        outcome, payout = "win", round(bet * DICE_PAYOUT)
    elif player_total < dealer_total:
        outcome, payout = "lose", 0
    else:
        outcome, payout = "push", bet
    return {
        "player_rolls": player,
        "dealer_rolls": dealer,
        "player_total": player_total,
        "dealer_total": dealer_total,
        "outcome": outcome,
        "payout": payout,
    }


# ── /casino slots ────────────────────────────────────────────────────────────────
def pick_symbol(roll: float) -> str:
    """Map a roll in [0, 1) onto a reel symbol via the weighted table."""
    acc = 0.0
    for symbol, weight in SLOT_SYMBOLS:
        acc += weight
        if roll < acc:
            return symbol
    return SLOT_SYMBOLS[-1][0]


def resolve_slots(bet: int, rolls: tuple[float, float, float]) -> dict:
    """Spin 3 reels from explicit rolls. Two-of-a-kind pays small, three pays big,
    triple 7️⃣ additionally flags a jackpot win (the cog claims the pool)."""
    reels = [pick_symbol(r) for r in rolls]
    counts = Counter(reels)
    if len(counts) == 1:
        symbol = reels[0]
        payout = round(bet * TRIPLE_PAYOUTS[symbol])
        return {
            "reels": reels,
            "outcome": "triple",
            "payout": payout,
            "jackpot_win": symbol == SLOT_JACKPOT_SYMBOL,
        }
    if len(counts) == 2:
        symbol = next(s for s, c in counts.items() if c == 2)
        payout = round(bet * PAIR_PAYOUTS[symbol])
        return {
            "reels": reels,
            "outcome": "pair",
            "payout": payout,
            "jackpot_win": False,
        }
    return {"reels": reels, "outcome": "none", "payout": 0, "jackpot_win": False}


def slots_expected_rtp() -> float:
    """Theoretical cash RTP of /casino slots (excluding the jackpot bonus)."""
    weights = dict(SLOT_SYMBOLS)
    total = 0.0
    for symbol, p in weights.items():
        total += (p**3) * TRIPLE_PAYOUTS[symbol]
        total += 3 * (p**2) * (1 - p) * PAIR_PAYOUTS[symbol]
    return total


# ── /casino roulette ──────────────────────────────────────────────────────────────
def spin_number(roll: float) -> int:
    """Map a roll in [0, 1) onto a European wheel pocket (0-36)."""
    return min(36, int(roll * 37))


def roulette_color(number: int) -> str:
    """'green' for 0, else 'red'/'black' per the standard European wheel."""
    if number == 0:
        return "green"
    return "red" if number in RED_NUMBERS else "black"


def parse_roulette_space(raw: str) -> int | str | None:
    """Normalize a /casino roulette `space` argument: a named space or a 0-36
    number. Returns None for anything unrecognized."""
    s = raw.strip().lower()
    if s in ROULETTE_SPACES:
        return s
    if s.lstrip("-").isdigit():
        n = int(s)
        if 0 <= n <= 36:
            return n
    return None


def resolve_roulette(bet: int, space: int | str, number: int) -> dict:
    """Resolve a spin already landed on `number` (0-36) against `space`."""
    color = roulette_color(number)
    if isinstance(space, int):
        won = number == space
        payout = round(bet * ROULETTE_NUMBER_PAYOUT) if won else 0
    else:
        s = space.lower()
        if s == "red":
            won = color == "red"
        elif s == "black":
            won = color == "black"
        elif s == "odd":
            won = number != 0 and number % 2 == 1
        elif s == "even":
            won = number != 0 and number % 2 == 0
        elif s == "high":
            won = 19 <= number <= 36
        elif s == "low":
            won = 1 <= number <= 18
        else:
            raise ValueError(f"unknown roulette space {space!r}")
        payout = round(bet * ROULETTE_OUTSIDE_PAYOUT) if won else 0
    return {"number": number, "color": color, "won": won, "payout": payout}


# ── /casino blackjack ─────────────────────────────────────────────────────────────
Card = tuple[str, str]  # (rank, suit)


def new_shoe(num_decks: int = 4) -> list[Card]:
    """A fresh, unshuffled `num_decks`-deck shoe. The cog shuffles it."""
    return [(rank, suit) for _ in range(num_decks) for suit in SUITS for rank in RANKS]


def card_label(card: Card) -> str:
    rank, suit = card
    return f"{rank}{suit}"


def hand_value(cards: list[Card]) -> tuple[int, bool]:
    """(best total <= 21 if possible, is_soft). Soft = an ace still counts as 11."""
    total = 0
    aces = 0
    for rank, _suit in cards:
        if rank == "A":
            total += 11
            aces += 1
        elif rank in ("J", "Q", "K"):
            total += 10
        else:
            total += int(rank)
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    soft = aces > 0
    return total, soft


def is_blackjack(cards: list[Card]) -> bool:
    """A natural: exactly two cards totalling 21."""
    return len(cards) == 2 and hand_value(cards)[0] == 21


def dealer_should_hit(cards: list[Card]) -> bool:
    """Dealer stands on 17+ (including soft 17), hits below."""
    return hand_value(cards)[0] < 17


def settle_blackjack(
    bet: int, player_cards: list[Card], dealer_cards: list[Card]
) -> dict:
    """Settle a finished hand (player has stood, busted, or both have a natural).

    Assumes the dealer has already played out (see dealer_should_hit) when the
    player didn't bust. Blackjack pays 3:2 (2.5x total); a regular win pays 2x;
    a push refunds the bet.
    """
    player_total, _ = hand_value(player_cards)
    if player_total > 21:
        return {"outcome": "bust", "payout": 0}

    player_bj = is_blackjack(player_cards)
    dealer_bj = is_blackjack(dealer_cards)
    dealer_total, _ = hand_value(dealer_cards)

    if player_bj and dealer_bj:
        return {"outcome": "push", "payout": bet}
    if player_bj:
        return {"outcome": "blackjack", "payout": round(bet * BLACKJACK_NATURAL_PAYOUT)}
    if dealer_bj:
        return {"outcome": "lose", "payout": 0}
    if dealer_total > 21:
        return {"outcome": "win", "payout": round(bet * BLACKJACK_PAYOUT)}
    if player_total > dealer_total:
        return {"outcome": "win", "payout": round(bet * BLACKJACK_PAYOUT)}
    if player_total < dealer_total:
        return {"outcome": "lose", "payout": 0}
    return {"outcome": "push", "payout": bet}


# ── Daily challenges ─────────────────────────────────────────────────────────────
def challenge_reward(chal_key: str, target: int) -> int:
    """Coin reward for completing one challenge (see constants.py for the
    reward-vs-house-edge reasoning). `wager_coins` scales with its target;
    every other kind pays a flat, modest amount."""
    if chal_key == "wager_coins":
        raw = round(target * CHALLENGE_EDGE_ASSUMPTION)
        return max(CHALLENGE_REWARD_MIN, min(CHALLENGE_REWARD_MAX, raw))
    return CHALLENGE_FLAT_REWARD.get(chal_key, CHALLENGE_REWARD_MIN)


def challenge_label(chal_key: str, target: int) -> str:
    """Full human-readable description of a challenge, for /casino challenge."""
    if chal_key == "win_games":
        return f"Win {target} games (any casino game)"
    if chal_key == "play_games":
        return f"Play {target} games"
    if chal_key == "wager_coins":
        return f"Wager {target:,} coins total"
    if chal_key == "big_payout":
        return f"Land a single payout of {target:,}+ coins"
    return "Unknown challenge"


def challenge_short_label(chal_key: str) -> str:
    """Compact noun for the one-line footer hint, e.g. 'Challenge: 2/3 wins'."""
    return CHALLENGE_SHORT_LABEL.get(chal_key, "progress")


def generate_challenges(guild_id: int, user_id: int, day: int) -> list[dict]:
    """Deterministically pick CHALLENGE_COUNT distinct daily challenges.

    Seeded on (guild_id, user_id, day) — the same trio always yields the same
    pool, so callers regenerate it on demand (the fishing generate_quest
    pattern) instead of persisting the pick; only progress/claimed state is
    persisted (see utils/db casino_challenges).
    """
    rng = random.Random(f"{int(guild_id)}:{int(user_id)}:{int(day)}:challenges")
    keys = rng.sample(CHALLENGE_POOL, CHALLENGE_COUNT)
    out = []
    for key in keys:
        lo, hi = CHALLENGE_TARGET_RANGE[key]
        target = rng.randint(lo, hi)
        out.append(
            {
                "chal_key": key,
                "target": target,
                "reward": challenge_reward(key, target),
                "label": challenge_label(key, target),
            }
        )
    return out
