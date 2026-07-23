"""
Tests for the pure fishing helpers in cogs/fishing/ (no Discord deps).
"""

import math

import pytest

from cogs.fishing import (
    FISH,
    FISH_BY_RARITY,
    RARITIES,
    RARITY_ODDS,
    RODS,
    catch_value,
    find_fish,
    fmt_weight,
    next_rod,
    pick_fish,
    pick_rarity,
    rarity_odds,
    rod_info,
    roll_weight,
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
