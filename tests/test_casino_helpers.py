"""
Tests for the pure casino helpers in cogs/casino/ (no Discord deps).
"""

import math

import pytest

from cogs.casino import (
    CHALLENGE_COUNT,
    CHALLENGE_POOL,
    CHALLENGE_REWARD_MAX,
    CHALLENGE_REWARD_MIN,
    CHALLENGE_TARGET_RANGE,
    PAIR_PAYOUTS,
    SLOT_SYMBOLS,
    STREAK_BONUS_CAP,
    STREAK_MIN,
    TRIPLE_PAYOUTS,
    apply_streak_bonus,
    card_label,
    challenge_label,
    challenge_reward,
    challenge_short_label,
    dealer_should_hit,
    generate_challenges,
    hand_value,
    is_blackjack,
    new_shoe,
    parse_roulette_space,
    pick_symbol,
    resolve_dice,
    resolve_flip,
    resolve_roulette,
    resolve_slots,
    roll_die,
    roulette_color,
    settle_blackjack,
    slots_expected_rtp,
    spin_number,
    streak_bonus,
)


# ── Win-streak bonus ─────────────────────────────────────────────────────────────
def test_streak_bonus_below_minimum_is_zero():
    for s in range(STREAK_MIN):
        assert streak_bonus(s) == 0.0


def test_streak_bonus_scales_and_caps():
    assert streak_bonus(3) == pytest.approx(0.05)
    assert streak_bonus(4) == pytest.approx(0.10)
    assert streak_bonus(7) == pytest.approx(0.25)
    assert streak_bonus(20) == STREAK_BONUS_CAP


def test_apply_streak_bonus_rounds():
    assert apply_streak_bonus(100, 0) == 100
    assert apply_streak_bonus(100, 3) == 105
    assert apply_streak_bonus(100, 7) == 125


# ── /casino flip ─────────────────────────────────────────────────────────────────
def test_resolve_flip_heads_and_tails():
    win = resolve_flip(100, "heads", 0.0)
    assert win == {"result": "heads", "won": True, "payout": 192}
    lose = resolve_flip(100, "heads", 0.999999)
    assert lose == {"result": "tails", "won": False, "payout": 0}


def test_resolve_flip_case_insensitive_choice():
    assert resolve_flip(100, "HEADS", 0.0)["won"] is True


# ── /casino dice ─────────────────────────────────────────────────────────────────
def test_roll_die_bounds():
    assert roll_die(0.0) == 1
    assert roll_die(0.999999) == 6


def test_resolve_dice_player_wins():
    # player rolls 6+6=12, dealer rolls 1+1=2
    result = resolve_dice(100, (0.999, 0.999), (0.0, 0.0))
    assert result["player_total"] == 12
    assert result["dealer_total"] == 2
    assert result["outcome"] == "win"
    assert result["payout"] == 210


def test_resolve_dice_dealer_wins():
    result = resolve_dice(100, (0.0, 0.0), (0.999, 0.999))
    assert result["outcome"] == "lose"
    assert result["payout"] == 0


def test_resolve_dice_push_refunds():
    result = resolve_dice(100, (0.0, 0.999), (0.999, 0.0))  # both total 7
    assert result["player_total"] == result["dealer_total"] == 7
    assert result["outcome"] == "push"
    assert result["payout"] == 100


# ── /casino slots ────────────────────────────────────────────────────────────────
def test_slot_weights_sum_to_one():
    assert math.isclose(sum(w for _s, w in SLOT_SYMBOLS), 1.0)


def test_pick_symbol_walks_the_table():
    assert pick_symbol(0.0) == SLOT_SYMBOLS[0][0]
    assert pick_symbol(0.999999) == SLOT_SYMBOLS[-1][0]


def test_resolve_slots_triple_seven_flags_jackpot():
    result = resolve_slots(100, (0.999999, 0.999999, 0.999999))
    assert result["outcome"] == "triple"
    assert result["reels"] == ["7️⃣", "7️⃣", "7️⃣"]
    assert result["jackpot_win"] is True
    assert result["payout"] == round(100 * TRIPLE_PAYOUTS["7️⃣"])


def test_resolve_slots_triple_non_seven_no_jackpot():
    result = resolve_slots(100, (0.0, 0.0, 0.0))
    assert result["outcome"] == "triple"
    assert result["jackpot_win"] is False
    assert result["payout"] == round(100 * TRIPLE_PAYOUTS["🍒"])


def test_resolve_slots_pair():
    # 0.0, 0.0 -> cherry, cherry ; something else for the third reel.
    result = resolve_slots(100, (0.0, 0.0, 0.5))
    assert result["outcome"] == "pair"
    assert result["payout"] == round(100 * PAIR_PAYOUTS["🍒"])
    assert result["jackpot_win"] is False


def test_resolve_slots_no_match():
    result = resolve_slots(100, (0.0, 0.5, 0.9))
    assert result["outcome"] == "none"
    assert result["payout"] == 0


def test_slots_rtp_in_range():
    """Cash RTP (excluding the jackpot bonus) is tuned to ~92-96%."""
    rtp = slots_expected_rtp()
    assert 0.92 <= rtp <= 0.96


# ── /casino roulette ──────────────────────────────────────────────────────────────
def test_spin_number_bounds():
    assert spin_number(0.0) == 0
    assert spin_number(0.999999) == 36


def test_roulette_color_zero_is_green():
    assert roulette_color(0) == "green"


def test_roulette_color_red_black_partition():
    reds = blacks = 0
    for n in range(1, 37):
        c = roulette_color(n)
        assert c in ("red", "black")
        reds += c == "red"
        blacks += c == "black"
    assert reds == blacks == 18


def test_parse_roulette_space_named():
    assert parse_roulette_space("Red") == "red"
    assert parse_roulette_space(" odd ") == "odd"


def test_parse_roulette_space_number():
    assert parse_roulette_space("17") == 17
    assert parse_roulette_space("0") == 0
    assert parse_roulette_space("36") == 36


def test_parse_roulette_space_invalid():
    assert parse_roulette_space("37") is None
    assert parse_roulette_space("purple") is None
    assert parse_roulette_space("-1") is None


def test_resolve_roulette_number_bet():
    win = resolve_roulette(100, 17, 17)
    assert win == {
        "number": 17,
        "color": roulette_color(17),
        "won": True,
        "payout": 3500,
    }
    lose = resolve_roulette(100, 17, 18)
    assert lose["won"] is False
    assert lose["payout"] == 0


def test_resolve_roulette_color_bet():
    win = resolve_roulette(100, "red", 1)  # 1 is red
    assert win["won"] is True
    assert win["payout"] == 200
    lose = resolve_roulette(100, "red", 2)  # 2 is black
    assert lose["won"] is False


def test_resolve_roulette_zero_loses_outside_bets():
    for space in ("red", "black", "odd", "even", "high", "low"):
        assert resolve_roulette(100, space, 0)["won"] is False


def test_resolve_roulette_odd_even_high_low():
    assert resolve_roulette(100, "odd", 7)["won"] is True
    assert resolve_roulette(100, "even", 7)["won"] is False
    assert resolve_roulette(100, "high", 30)["won"] is True
    assert resolve_roulette(100, "low", 30)["won"] is False


def test_resolve_roulette_unknown_space_raises():
    with pytest.raises(ValueError):
        resolve_roulette(100, "purple", 5)


# ── /casino blackjack ─────────────────────────────────────────────────────────────
def test_new_shoe_size_and_composition():
    shoe = new_shoe(4)
    assert len(shoe) == 52 * 4
    assert shoe.count(("A", "♠")) == 4


def test_card_label():
    assert card_label(("10", "♥")) == "10♥"
    assert card_label(("A", "♠")) == "A♠"


def test_hand_value_simple():
    assert hand_value([("10", "♠"), ("7", "♥")]) == (17, False)


def test_hand_value_soft_ace():
    assert hand_value([("A", "♠"), ("6", "♥")]) == (17, True)


def test_hand_value_ace_adjusts_when_busting():
    # A + 6 + 10 = 17 (ace drops from 11 to 1, no longer soft)
    assert hand_value([("A", "♠"), ("6", "♥"), ("10", "♦")]) == (17, False)


def test_hand_value_multiple_aces():
    # A + A + 9 = 21 (one ace as 11, one as 1)
    assert hand_value([("A", "♠"), ("A", "♥"), ("9", "♦")]) == (21, True)


def test_is_blackjack():
    assert is_blackjack([("A", "♠"), ("K", "♥")]) is True
    assert is_blackjack([("A", "♠"), ("9", "♥")]) is False
    assert is_blackjack([("A", "♠"), ("K", "♥"), ("2", "♦")]) is False  # 3 cards


def test_dealer_should_hit_threshold():
    assert dealer_should_hit([("10", "♠"), ("6", "♥")]) is True  # 16
    assert dealer_should_hit([("10", "♠"), ("7", "♥")]) is False  # 17
    assert dealer_should_hit([("A", "♠"), ("6", "♥")]) is False  # soft 17 stands


def test_settle_blackjack_player_natural():
    result = settle_blackjack(100, [("A", "♠"), ("K", "♥")], [("10", "♠"), ("7", "♥")])
    assert result == {"outcome": "blackjack", "payout": 250}


def test_settle_blackjack_double_natural_pushes():
    result = settle_blackjack(100, [("A", "♠"), ("K", "♥")], [("A", "♦"), ("Q", "♣")])
    assert result == {"outcome": "push", "payout": 100}


def test_settle_blackjack_player_bust():
    result = settle_blackjack(
        100, [("10", "♠"), ("8", "♥"), ("5", "♦")], [("10", "♣"), ("7", "♠")]
    )
    assert result == {"outcome": "bust", "payout": 0}


def test_settle_blackjack_dealer_bust():
    result = settle_blackjack(
        100, [("10", "♠"), ("8", "♥")], [("10", "♣"), ("6", "♠"), ("9", "♦")]
    )
    assert result == {"outcome": "win", "payout": 200}


def test_settle_blackjack_higher_total_wins():
    result = settle_blackjack(100, [("10", "♠"), ("9", "♥")], [("10", "♣"), ("8", "♠")])
    assert result == {"outcome": "win", "payout": 200}


def test_settle_blackjack_lower_total_loses():
    result = settle_blackjack(100, [("10", "♠"), ("8", "♥")], [("10", "♣"), ("9", "♠")])
    assert result == {"outcome": "lose", "payout": 0}


def test_settle_blackjack_equal_totals_push():
    result = settle_blackjack(100, [("10", "♠"), ("8", "♥")], [("10", "♣"), ("8", "♠")])
    assert result == {"outcome": "push", "payout": 100}


# ── Daily challenges ─────────────────────────────────────────────────────────────
def test_challenge_reward_flat_kinds_within_band():
    for key in ("win_games", "play_games", "big_payout"):
        lo, hi = CHALLENGE_TARGET_RANGE[key]
        for target in (lo, (lo + hi) // 2, hi):
            reward = challenge_reward(key, target)
            assert CHALLENGE_REWARD_MIN <= reward <= CHALLENGE_REWARD_MAX


def test_challenge_reward_wager_scales_with_target_and_is_clamped():
    lo, hi = CHALLENGE_TARGET_RANGE["wager_coins"]
    low_reward = challenge_reward("wager_coins", lo)
    high_reward = challenge_reward("wager_coins", hi)
    assert CHALLENGE_REWARD_MIN <= low_reward <= high_reward <= CHALLENGE_REWARD_MAX


def test_challenge_label_and_short_label_cover_every_pool_key():
    for key in CHALLENGE_POOL:
        assert challenge_label(key, 5) != "Unknown challenge"
        assert challenge_short_label(key) != "progress"


def test_generate_challenges_picks_distinct_keys_within_range():
    challenges = generate_challenges(1, 2, 100)
    assert len(challenges) == CHALLENGE_COUNT
    keys = [c["chal_key"] for c in challenges]
    assert len(set(keys)) == len(keys)  # no duplicate challenge kind in one day
    for chal in challenges:
        lo, hi = CHALLENGE_TARGET_RANGE[chal["chal_key"]]
        assert lo <= chal["target"] <= hi
        assert CHALLENGE_REWARD_MIN <= chal["reward"] <= CHALLENGE_REWARD_MAX
        assert chal["label"]


def test_generate_challenges_is_deterministic_per_seed():
    a = generate_challenges(111, 222, 42)
    b = generate_challenges(111, 222, 42)
    assert a == b


def test_generate_challenges_varies_by_day_or_user():
    a = generate_challenges(111, 222, 42)
    b = generate_challenges(111, 222, 43)
    c = generate_challenges(111, 333, 42)
    assert a != b or a != c
