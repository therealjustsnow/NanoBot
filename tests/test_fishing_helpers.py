"""
Tests for the pure fishing helpers in cogs/fishing/ (no Discord deps).
"""

import math

import pytest

from cogs.fishing import (
    EVENT_POOL,
    FISH,
    FISH_BY_RARITY,
    LEVEL_LUCK_CAP,
    RARITIES,
    RARITY_ODDS,
    RODS,
    catch_value,
    cum_xp_for_level,
    find_fish,
    fish_level,
    fmt_weight,
    generate_quest,
    level_luck_bonus,
    level_progress,
    next_rod,
    next_streak,
    pick_event,
    pick_fish,
    pick_rarity,
    quest_label,
    quest_reward,
    rarity_odds,
    rod_info,
    roll_event_duration,
    roll_weight,
    streak_bonus_coins,
    treasure_coins,
)


# ── Catalogue sanity ───────────────────────────────────────────────────────────
def test_catalogue_is_well_formed():
    names = set()
    for key, fish in FISH.items():
        assert fish["rarity"] in RARITIES, key
        lo, hi = fish["weight"]
        assert 0 < lo <= hi, key
        assert fish["value"] >= 1, key
        assert fish["name"] not in names, f"duplicate name {fish['name']}"
        names.add(fish["name"])


def test_every_rarity_has_fish():
    for rarity, _p in RARITY_ODDS:
        assert FISH_BY_RARITY[rarity], f"no fish in tier {rarity}"


def test_base_odds_sum_to_one():
    assert math.isclose(sum(p for _r, p in RARITY_ODDS), 1.0)


def test_rod_ladder_ascends():
    for a, b in zip(RODS, RODS[1:]):
        assert b["price"] > a["price"]
        assert b["luck"] > a["luck"]
    assert RODS[0]["price"] == 0


# ── Rarity odds + picking ──────────────────────────────────────────────────────
@pytest.mark.parametrize("luck", [0.0, 0.15, 0.5, 0.75, 1.0])
def test_rarity_odds_always_sum_to_one(luck):
    assert math.isclose(sum(p for _r, p in rarity_odds(luck)), 1.0)


def test_luck_zero_matches_base_odds():
    assert rarity_odds(0.0) == RARITY_ODDS


def test_luck_shifts_mass_upward():
    base = dict(rarity_odds(0.0))
    lucky = dict(rarity_odds(0.75))
    assert lucky["junk"] < base["junk"]
    assert lucky["common"] < base["common"]
    for tier in ("uncommon", "rare", "epic", "legendary", "treasure"):
        assert lucky[tier] > base[tier]


def test_luck_is_clamped():
    assert rarity_odds(-5) == rarity_odds(0.0)
    assert rarity_odds(5) == rarity_odds(1.0)


def test_pick_rarity_walks_the_table():
    # Roll 0 lands in the first tier; a roll just under 1 lands in the last.
    assert pick_rarity(0.0) == RARITY_ODDS[0][0]
    assert pick_rarity(0.999999) == RARITY_ODDS[-1][0]
    # Junk ends at 0.15 with no luck: just below stays junk, just above doesn't.
    assert pick_rarity(0.1499) == "junk"
    assert pick_rarity(0.1501) == "common"


def test_pick_fish_deterministic_and_in_tier():
    for rarity, _p in RARITY_ODDS:
        keys = FISH_BY_RARITY[rarity]
        assert pick_fish(rarity, 0.0) == keys[0]
        assert pick_fish(rarity, 0.999999) == keys[-1]


# ── Weights + values ───────────────────────────────────────────────────────────
def test_roll_weight_bounds():
    for key in FISH:
        lo, hi = FISH[key]["weight"]
        assert roll_weight(key, 0.0) == round(lo, 2)
        assert roll_weight(key, 1.0) == round(hi, 2)
        assert lo <= roll_weight(key, 0.5) <= hi


def test_catch_value_scales_with_weight():
    lo, hi = FISH["salmon"]["weight"]
    base = FISH["salmon"]["value"]
    assert catch_value("salmon", lo) == round(base * 0.6)
    assert catch_value("salmon", hi) == round(base * 1.4)
    assert catch_value("salmon", lo) < catch_value("salmon", hi)


def test_catch_value_junk_is_flat():
    lo, hi = FISH["boot"]["weight"]
    assert catch_value("boot", lo) == FISH["boot"]["value"]
    assert catch_value("boot", hi) == FISH["boot"]["value"]


def test_catch_value_clamps_out_of_range_weight():
    lo, hi = FISH["salmon"]["weight"]
    assert catch_value("salmon", lo - 100) == catch_value("salmon", lo)
    assert catch_value("salmon", hi + 100) == catch_value("salmon", hi)


def test_catch_value_never_below_one():
    for key in FISH:
        lo, _hi = FISH[key]["weight"]
        assert catch_value(key, lo) >= 1


def test_treasure_coins_range():
    base = FISH["chest"]["value"]
    assert treasure_coins("chest", 0.0) == round(base * 0.5)
    assert treasure_coins("chest", 0.999999) == round(base * 1.5)


# ── Rods ───────────────────────────────────────────────────────────────────────
def test_rod_info_clamps():
    assert rod_info(-1) == RODS[0]
    assert rod_info(0) == RODS[0]
    assert rod_info(999) == RODS[-1]


def test_next_rod_progression():
    assert next_rod(0) == RODS[1]
    assert next_rod(len(RODS) - 2) == RODS[-1]
    assert next_rod(len(RODS) - 1) is None
    assert next_rod(999) is None


# ── Formatting + lookup ────────────────────────────────────────────────────────
def test_fmt_weight():
    assert fmt_weight(0.05) == "50 g"
    assert fmt_weight(0.999) == "999 g"
    assert fmt_weight(1.0) == "1.00 kg"
    assert fmt_weight(1234.5) == "1,234.50 kg"


def test_find_fish_by_key_and_name():
    assert find_fish("salmon") == "salmon"
    assert find_fish("SALMON") == "salmon"
    assert find_fish("Bluefin Tuna") == "tuna"
    assert find_fish("  old boot ") == "boot"
    assert find_fish("kraken") is None


# ── Levels & XP ──────────────────────────────────────────────────────────────────
def test_cum_xp_for_level_matches_25n_squared():
    assert cum_xp_for_level(0) == 0
    assert cum_xp_for_level(1) == 25
    assert cum_xp_for_level(10) == 2500
    assert cum_xp_for_level(-5) == 0  # clamped


def test_fish_level_boundaries():
    assert fish_level(0) == 0
    assert fish_level(24) == 0
    assert fish_level(25) == 1
    assert fish_level(99) == 1
    assert fish_level(100) == 2
    assert fish_level(-10) == 0  # negative xp floors to level 0


def test_fish_level_is_monotonic():
    prev = fish_level(0)
    for xp in range(0, 5000, 17):
        lvl = fish_level(xp)
        assert lvl >= prev
        prev = lvl


def test_level_progress_reports_into_and_needed():
    level, into, needed = level_progress(30)
    assert level == 1
    assert into == 30 - cum_xp_for_level(1)
    assert needed == cum_xp_for_level(2) - cum_xp_for_level(1)
    # Exactly on a level boundary: 0 xp into the level.
    level, into, _needed = level_progress(cum_xp_for_level(3))
    assert level == 3
    assert into == 0


def test_level_luck_bonus_caps():
    assert level_luck_bonus(0) == 0.0
    assert level_luck_bonus(10) == pytest.approx(0.05)
    assert level_luck_bonus(1000) == LEVEL_LUCK_CAP
    assert level_luck_bonus(-5) == 0.0


# ── Daily streak ─────────────────────────────────────────────────────────────────
def test_next_streak_continues_on_consecutive_day():
    assert next_streak(last_day=10, today=11, streak=3) == 4


def test_next_streak_resets_on_gap_or_fresh_start():
    assert next_streak(last_day=5, today=11, streak=8) == 1
    assert next_streak(last_day=0, today=0, streak=0) == 1


def test_streak_bonus_coins_scales_and_caps():
    assert streak_bonus_coins(1) == 10
    assert streak_bonus_coins(5) == 50
    assert streak_bonus_coins(50) == 100  # capped
    assert streak_bonus_coins(0) == 0


# ── Daily quest generation ─────────────────────────────────────────────────────────
def test_generate_quest_is_deterministic_for_same_inputs():
    a = generate_quest(1, 2, 100)
    b = generate_quest(1, 2, 100)
    assert a == b


def test_generate_quest_varies_by_day_or_user():
    a = generate_quest(1, 2, 100)
    b = generate_quest(1, 2, 101)
    c = generate_quest(1, 3, 100)
    # Not asserting inequality of every field (a coincidence is possible), just
    # that varying an input can change the outcome across a decent sample.
    variants = {
        generate_quest(1, 2, day)["quest_key"]
        + str(generate_quest(1, 2, day)["target"])
        for day in range(50)
    }
    assert len(variants) > 1
    assert a is not b and a is not c  # sanity: independent dict objects


def test_generate_quest_shape_and_reward_consistency():
    for day in range(30):
        q = generate_quest(42, 7, day)
        assert q["quest_key"] in ("catch_any", "earn_coins") or q[
            "quest_key"
        ].startswith("catch_rarity:")
        assert q["target"] > 0
        assert q["label"] == quest_label(q["quest_key"], q["target"])
        assert (q["reward_coins"], q["reward_xp"]) == quest_reward(
            q["quest_key"], q["target"]
        )


def test_quest_label_and_reward_for_each_shape():
    assert quest_label("catch_any", 10) == "Catch 10 fish"
    assert quest_label("catch_rarity:epic", 2) == "Catch 2 epic fish"
    assert quest_label("earn_coins", 100) == "Earn 100 coins fishing"
    assert quest_label("bogus", 1) == "Unknown quest"

    assert quest_reward("catch_any", 10) == (80, 40)
    assert quest_reward("catch_rarity:epic", 2) == (240, 120)
    assert quest_reward("earn_coins", 100) == (50, 30)
    assert quest_reward("bogus", 1) == (0, 0)


# ── Guild fishing events ─────────────────────────────────────────────────────────
def test_pick_event_walks_the_weighted_table():
    assert pick_event(0.0) == EVENT_POOL[0]
    assert pick_event(0.999999) == EVENT_POOL[-1]


def test_roll_event_duration_bounds():
    assert roll_event_duration(0.0, (600, 1200)) == 600
    assert roll_event_duration(1.0, (600, 1200)) == 1200
    assert 600 <= roll_event_duration(0.5, (600, 1200)) <= 1200
