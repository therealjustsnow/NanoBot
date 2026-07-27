"""
Tests for the pure activities helpers in cogs/activities/ (no Discord deps).
"""

import math

import pytest

from cogs.activities import (
    ACTIVITY_COOLDOWN_BOUNDS,
    ACTIVITY_DEFAULT_COOLDOWNS,
    COOLDOWN_MIN,
    CAREER_LADDER,
    EXPLORE_OUTCOMES,
    HUNT_ODDS,
    ORE_ODDS,
    ORES,
    PICKAXES,
    ROB_BASE_SUCCESS,
    ROB_LUCK_BONUS,
    ROB_STEAL_CAP,
    ROB_SUCCESS_CAP,
    WORK_PAY_MAX,
    WORK_PAY_MIN,
    career_info,
    effective_cooldown,
    hunt_injury_fine,
    mine_odds,
    next_career,
    next_pickaxe,
    pick_explore_outcome,
    pick_hunt_catch,
    pick_ore,
    pick_work_scene,
    pickaxe_info,
    rob_steal_amount,
    rob_success,
    roll_cave_in,
    roll_coin_amount,
    roll_hunt_injury,
    roll_hunt_padlock,
    roll_mine_treasure_key,
    roll_work_pay,
)


# ── Career ladder / /work ───────────────────────────────────────────────────────
def test_career_ladder_ascends():
    for a, b in zip(CAREER_LADDER, CAREER_LADDER[1:]):
        assert b[0] > a[0]  # threshold
        assert b[2] >= a[2]  # bonus never decreases


def test_career_info_clamps_to_tier():
    assert career_info(0)["tier"] == 0
    assert career_info(9)["tier"] == 0
    assert career_info(10)["tier"] == 1
    assert career_info(3000)["tier"] == len(CAREER_LADDER) - 1
    assert (
        career_info(999_999)["tier"] == len(CAREER_LADDER) - 1
    )  # clamped, no overflow


def test_next_career_progression():
    assert next_career(0)["tier"] == 1
    assert next_career(0)["shifts"] == CAREER_LADDER[1][0]
    assert next_career(999_999) is None


def test_roll_work_pay_bounds():
    assert roll_work_pay(0.0, 0) == WORK_PAY_MIN
    assert roll_work_pay(0.999999, 0) == WORK_PAY_MAX
    assert roll_work_pay(0.0, 10) == WORK_PAY_MIN + 10
    assert roll_work_pay(0.5, 0) >= 1  # never non-positive


def test_pick_work_scene_deterministic():
    from cogs.activities.constants import WORK_SCENES

    assert pick_work_scene(0.0) == WORK_SCENES[0]
    assert pick_work_scene(0.999999) == WORK_SCENES[-1]


# ── Mining odds / /mine ────────────────────────────────────────────────────────
def test_ore_catalogue_well_formed():
    for key, ore in ORES.items():
        assert ore["value"] >= 1, key
    assert set(k for k, _p in ORE_ODDS) == set(ORES.keys())


def test_base_ore_odds_sum_to_one():
    assert math.isclose(sum(p for _k, p in ORE_ODDS), 1.0)


@pytest.mark.parametrize("luck", [0.0, 0.15, 0.5, 0.75, 1.0])
def test_mine_odds_always_sum_to_one(luck):
    assert math.isclose(sum(p for _k, p in mine_odds(luck)), 1.0)


def test_mine_odds_luck_zero_matches_base():
    assert mine_odds(0.0) == ORE_ODDS


def test_mine_odds_luck_shifts_mass_upward():
    base = dict(mine_odds(0.0))
    lucky = dict(mine_odds(0.75))
    assert lucky["stone"] < base["stone"]
    assert lucky["coal"] < base["coal"]
    for tier in ("iron_ore", "gold_ore", "diamond"):
        assert lucky[tier] > base[tier]


def test_mine_odds_luck_clamped():
    assert mine_odds(-5) == mine_odds(0.0)
    assert mine_odds(5) == mine_odds(1.0)


def test_pick_ore_walks_the_table():
    assert pick_ore(0.0, 0.0) == ORE_ODDS[0][0]
    assert pick_ore(0.999999, 0.0) == ORE_ODDS[-1][0]


def test_roll_cave_in_boundary():
    from cogs.activities.constants import MINE_CAVE_IN_CHANCE

    assert roll_cave_in(MINE_CAVE_IN_CHANCE - 0.001) is True
    assert roll_cave_in(MINE_CAVE_IN_CHANCE + 0.001) is False


def test_roll_mine_treasure_key_boundary():
    from cogs.activities.constants import MINE_TREASURE_KEY_CHANCE

    assert roll_mine_treasure_key(MINE_TREASURE_KEY_CHANCE - 0.001) is True
    assert roll_mine_treasure_key(MINE_TREASURE_KEY_CHANCE + 0.001) is False


def test_pickaxe_ladder_ascends():
    for a, b in zip(PICKAXES, PICKAXES[1:]):
        assert b["price"] > a["price"]
        assert b["luck"] > a["luck"]
    assert PICKAXES[0]["price"] == 0


def test_pickaxe_info_clamps():
    assert pickaxe_info(-1) == PICKAXES[0]
    assert pickaxe_info(0) == PICKAXES[0]
    assert pickaxe_info(999) == PICKAXES[-1]


def test_next_pickaxe_progression():
    assert next_pickaxe(0) == PICKAXES[1]
    assert next_pickaxe(len(PICKAXES) - 2) == PICKAXES[-1]
    assert next_pickaxe(len(PICKAXES) - 1) is None
    assert next_pickaxe(999) is None


# ── Hunting / /hunt ─────────────────────────────────────────────────────────────
def test_hunt_odds_sum_to_one():
    assert math.isclose(sum(p for _k, p in HUNT_ODDS), 1.0)
    assert set(k for k, _p in HUNT_ODDS) == set(["pelt", "meat", "golden_antler"])


def test_pick_hunt_catch_walks_the_table():
    assert pick_hunt_catch(0.0) == HUNT_ODDS[0][0]
    assert pick_hunt_catch(0.999999) == HUNT_ODDS[-1][0]


def test_roll_hunt_injury_boundary():
    from cogs.activities.constants import HUNT_INJURY_CHANCE

    assert roll_hunt_injury(HUNT_INJURY_CHANCE - 0.001) is True
    assert roll_hunt_injury(HUNT_INJURY_CHANCE + 0.001) is False


def test_hunt_injury_fine_bounds():
    assert hunt_injury_fine(0.0) == 0
    from cogs.activities.constants import HUNT_INJURY_FINE_MAX

    assert hunt_injury_fine(1.0) == HUNT_INJURY_FINE_MAX
    for roll in (0.1, 0.5, 0.9):
        assert 0 <= hunt_injury_fine(roll) <= HUNT_INJURY_FINE_MAX


def test_roll_hunt_padlock_boundary():
    from cogs.activities.constants import HUNT_PADLOCK_CHANCE

    assert roll_hunt_padlock(HUNT_PADLOCK_CHANCE - 0.001) is True
    assert roll_hunt_padlock(HUNT_PADLOCK_CHANCE + 0.001) is False


# ── Exploring / /explore ────────────────────────────────────────────────────────
def test_explore_outcomes_sum_to_one():
    assert math.isclose(sum(p for _k, p in EXPLORE_OUTCOMES), 1.0)


def test_pick_explore_outcome_walks_the_table():
    assert pick_explore_outcome(0.0) == EXPLORE_OUTCOMES[0][0]
    assert pick_explore_outcome(0.999999) == EXPLORE_OUTCOMES[-1][0]


def test_treasure_key_supply_tracks_chest_supply():
    """Keys must not out-drop chests by more than a small margin.

    A treasure_key does exactly one thing: open a treasure_chest. /explore is
    the only chest faucet, but keys drop from /mine too — on a 30m cooldown
    against explore's 3h, so mining's rate is multiplied by 6x the claims.
    Comparing the raw percentages hides that; this compares expected drops per
    day at full claim rate, which is what a player actually banks.

    Some surplus is wanted (a chest with no key is worse than a spare key), but
    a large one means keys pile up unspendable — the state this ratio was
    tightened to fix, when it stood at 5.25:1.
    """
    from cogs.activities.constants import (
        EXPLORE_COOLDOWN_DEFAULT,
        MINE_COOLDOWN_DEFAULT,
        MINE_TREASURE_KEY_CHANCE,
    )

    day = 86_400
    explore_claims = day / EXPLORE_COOLDOWN_DEFAULT
    mine_claims = day / MINE_COOLDOWN_DEFAULT
    odds = dict(EXPLORE_OUTCOMES)

    keys = (
        explore_claims * odds["treasure_key"] + mine_claims * MINE_TREASURE_KEY_CHANCE
    )
    chests = explore_claims * odds["treasure_chest"]

    assert chests > 0, "explore is the only chest source — it can't drop to zero"
    assert 1.0 <= keys / chests <= 2.0, (
        f"{keys:.2f} keys/day against {chests:.2f} chests/day "
        f"({keys / chests:.2f}:1) — keys should modestly exceed chests, not bank up"
    )


def test_roll_coin_amount_bounds():
    assert roll_coin_amount(0.0, 100, 400) == 100
    assert roll_coin_amount(1.0, 100, 400) == 400
    mid = roll_coin_amount(0.5, 100, 400)
    assert 100 <= mid <= 400


# ── Robbing / /rob ───────────────────────────────────────────────────────────────
def test_rob_success_boundary_no_luck():
    assert rob_success(ROB_BASE_SUCCESS - 0.001, False) is True
    assert rob_success(ROB_BASE_SUCCESS + 0.001, False) is False


def test_rob_success_boundary_with_luck():
    chance = min(ROB_BASE_SUCCESS + ROB_LUCK_BONUS, ROB_SUCCESS_CAP)
    assert rob_success(chance - 0.001, True) is True
    assert rob_success(chance + 0.001, True) is False


def test_rob_success_luck_never_exceeds_cap():
    chance = min(ROB_BASE_SUCCESS + ROB_LUCK_BONUS, ROB_SUCCESS_CAP)
    assert chance <= ROB_SUCCESS_CAP


def test_rob_steal_amount_bounds():
    assert rob_steal_amount(0.0, 1000) == 100  # 10% of 1000
    amount = rob_steal_amount(0.999999, 1000)
    assert amount == round(1000 * 0.2)  # ~20%, well under the cap


def test_rob_steal_amount_capped():
    assert rob_steal_amount(0.999999, 1_000_000) == ROB_STEAL_CAP


def test_rob_steal_amount_never_negative():
    assert rob_steal_amount(0.0, 0) == 0
    assert rob_steal_amount(1.0, 0) == 0


# ── cooldown resolution (lengths are bot-wide, owner-set) ─────────────────────
def test_effective_cooldown_honours_the_owners_override():
    """Only the bot owner writes these, and claims are global, so whatever they
    set is what every server runs — there is no per-server floor to apply."""
    for activity, (low, high) in ACTIVITY_COOLDOWN_BOUNDS.items():
        assert effective_cooldown(activity, low) == low
        assert effective_cooldown(activity, high) == high
        assert effective_cooldown(activity, 600) == 600


def test_no_override_means_the_activitys_default():
    for activity, default in ACTIVITY_DEFAULT_COOLDOWNS.items():
        assert effective_cooldown(activity, None) == default
        assert ACTIVITY_COOLDOWN_BOUNDS[activity][0] <= default
        assert default <= ACTIVITY_COOLDOWN_BOUNDS[activity][1]


def test_effective_cooldown_never_degrades_to_no_cooldown():
    """The one failure mode this must not have. A junk value, an unknown
    activity, or a zero/negative row falls back to a real duration."""
    assert effective_cooldown("work", "nonsense") == ACTIVITY_DEFAULT_COOLDOWNS["work"]
    assert effective_cooldown("not_an_activity", None) > 0
    assert effective_cooldown("work", 0) == COOLDOWN_MIN
    assert effective_cooldown("work", -99) == COOLDOWN_MIN
