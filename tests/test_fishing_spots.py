"""
Tests for cogs/fishing/spots.py — the fishing-spot registry and its pure
helpers (no Discord deps, no database).

The spot system is the one place in fishing where a data edit can silently
change the whole economy: a weight typo moves income, a species without a home
disappears from every pool, a level gate above the price gate makes a charter
unbuyable. These check the shape rather than the numbers, so tuning a spot is
free and breaking one isn't. The *income* consequences live in
tests/test_economy_balance.py.
"""

import math

import pytest

from cogs.fishing.constants import FISH, RARITY_ODDS
from cogs.fishing.helpers import rarity_odds
from cogs.fishing.spots import (
    DEFAULT_SPOT,
    SPOTS,
    SPOT_ORDER,
    SPOT_POOLS,
    exclusive_keys,
    find_spot,
    fish_pool,
    next_spot,
    pick_spot_fish,
    pick_spot_rarity,
    resolve_spot,
    roll_hazard,
    spot_info,
    spot_odds,
    unlock_state,
)

_RARITIES = [rarity for rarity, _p in RARITY_ODDS]


# ── Registry shape ───────────────────────────────────────────────────────────
def test_every_spot_is_well_formed():
    for key, spot in SPOTS.items():
        assert spot["name"] and spot["emoji"] and spot["blurb"], key
        assert spot["level"] >= 0, key
        assert spot["price"] >= 0, key
        assert 0.0 <= spot["hazard"] < 0.5, key
        for rarity, weight in spot["weights"].items():
            assert rarity in _RARITIES, (key, rarity)
            assert weight > 0, (key, rarity)


def test_the_starter_spot_is_free_safe_and_first():
    assert SPOT_ORDER[0] == DEFAULT_SPOT
    assert SPOTS[DEFAULT_SPOT]["price"] == 0
    assert SPOTS[DEFAULT_SPOT]["level"] == 0
    assert SPOTS[DEFAULT_SPOT]["hazard"] == 0.0
    assert SPOTS[DEFAULT_SPOT]["weights"] == {}


def test_the_starter_spot_holds_every_general_species():
    """It has to stay a real place to fish, not a waiting room: everything
    without a home of its own is catchable there."""
    general = {k for k, f in FISH.items() if "spot" not in f}
    pooled = {k for keys in SPOT_POOLS[DEFAULT_SPOT].values() for k in keys}
    assert pooled == general


def test_every_exclusive_species_names_a_real_spot():
    for key, fish in FISH.items():
        if "spot" in fish:
            assert fish["spot"] in SPOTS, key
            assert fish["rarity"] in _RARITIES, key


def test_travelling_only_ever_adds_species():
    """A spot must never take a fish away. Someone who charters the Trench and
    then wants their Old Boot back should be able to catch one."""
    base = {k for keys in SPOT_POOLS[DEFAULT_SPOT].values() for k in keys}
    for spot in SPOT_ORDER:
        here = {k for keys in SPOT_POOLS[spot].values() for k in keys}
        assert base <= here, spot


def test_every_spot_past_the_first_has_something_of_its_own():
    """Exclusives are the reason to travel; a spot that's only a stat tweak
    isn't worth a charter."""
    for spot in SPOT_ORDER[1:]:
        assert exclusive_keys(spot), spot
    assert exclusive_keys(DEFAULT_SPOT) == []


def test_every_pool_can_answer_every_rarity():
    """pick_spot_fish indexes into a pool; an empty one would be a crash on a
    roll that happened to land there."""
    for spot in SPOT_ORDER:
        for rarity in _RARITIES:
            assert fish_pool(rarity, spot), (spot, rarity)


# ── Odds ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("spot", SPOT_ORDER)
@pytest.mark.parametrize("luck", [0.0, 0.3, 1.0])
def test_spot_odds_always_sum_to_one(spot, luck):
    assert math.isclose(sum(p for _r, p in spot_odds(rarity_odds(luck), spot)), 1.0)


def test_spot_odds_keep_the_rarity_order():
    """weighted_pick walks the table in order, so a reordering would silently
    change what every roll maps to."""
    for spot in SPOT_ORDER:
        assert [r for r, _p in spot_odds(rarity_odds(0.0), spot)] == _RARITIES


def test_the_starter_spot_leaves_the_table_alone():
    assert spot_odds(rarity_odds(0.4), DEFAULT_SPOT) == rarity_odds(0.4)


def test_a_deep_spot_trades_junk_for_monsters():
    """The Deep's whole character, asserted: it raises junk *and* legendary at
    once, which is exactly what a shift-mass-upward transform can't express and
    why spots multiply instead."""
    pond = dict(spot_odds(rarity_odds(0.3), DEFAULT_SPOT))
    deep = dict(spot_odds(rarity_odds(0.3), "deep"))
    assert deep["junk"] > pond["junk"]
    assert deep["legendary"] > pond["legendary"]
    assert deep["common"] < pond["common"]


def test_unknown_spots_never_break_a_cast():
    """The spot comes out of a database column, so it can hold anything — a
    typo, or a spot dropped in a later version. None of it may stop fishing."""
    assert resolve_spot(None) == DEFAULT_SPOT
    assert resolve_spot("") == DEFAULT_SPOT
    assert resolve_spot("atlantis") == DEFAULT_SPOT
    assert spot_info("atlantis") is SPOTS[DEFAULT_SPOT]
    assert spot_odds(rarity_odds(0.0), "atlantis") == rarity_odds(0.0)
    assert fish_pool("common", "atlantis") == fish_pool("common", DEFAULT_SPOT)
    assert roll_hazard(0.0, "atlantis") is False


# ── Picking ──────────────────────────────────────────────────────────────────
def test_pick_walks_each_spots_own_pool():
    for spot in SPOT_ORDER:
        for rarity in _RARITIES:
            pool = fish_pool(rarity, spot)
            assert pick_spot_fish(rarity, 0.0, spot) == pool[0]
            assert pick_spot_fish(rarity, 0.999999, spot) == pool[-1]


def test_an_exclusive_is_only_reachable_at_its_spot():
    for spot in SPOT_ORDER[1:]:
        for key in exclusive_keys(spot):
            rarity = FISH[key]["rarity"]
            assert key in fish_pool(rarity, spot)
            assert key not in fish_pool(rarity, DEFAULT_SPOT)


def test_pick_spot_rarity_walks_the_adjusted_table():
    odds = rarity_odds(0.0)
    assert pick_spot_rarity(0.0, odds, "deep") == _RARITIES[0]
    assert pick_spot_rarity(0.999999, odds, "deep") == _RARITIES[-1]


# ── Hazards ──────────────────────────────────────────────────────────────────
def test_hazard_fires_on_its_own_boundary():
    for spot in SPOT_ORDER:
        chance = SPOTS[spot]["hazard"]
        if chance:
            assert roll_hazard(chance - 0.001, spot) is True
        assert roll_hazard(chance + 0.001, spot) is False


def test_the_starter_spot_never_snags():
    for roll in (0.0, 0.5, 0.999999):
        assert roll_hazard(roll, DEFAULT_SPOT) is False


# ── Lookups and gating ───────────────────────────────────────────────────────
def test_find_spot_takes_a_key_a_name_or_part_of_one():
    assert find_spot("pond") == "pond"
    assert find_spot("Old Pond") == "pond"
    assert find_spot("  CORAL reef ") == "reef"
    assert find_spot("trench") == "abyss"  # substring
    assert find_spot("atlantis") is None
    assert find_spot("") is None
    assert find_spot(None) is None


def test_next_spot_walks_the_progression():
    assert next_spot(DEFAULT_SPOT) == SPOT_ORDER[1]
    assert next_spot(SPOT_ORDER[-1]) is None
    assert next_spot("atlantis") == SPOT_ORDER[1]  # resolved to the starter


def test_unlock_state_covers_every_way_a_spot_can_be_shut():
    target = SPOT_ORDER[1]
    info = SPOTS[target]

    assert unlock_state(DEFAULT_SPOT, 0, set(), 0)["state"] == "open"
    assert unlock_state(target, 0, set(), 10**9)["state"] == "locked"
    assert unlock_state(target, info["level"], set(), 0)["state"] == "poor"
    assert unlock_state(target, info["level"], set(), info["price"])["state"] == (
        "buyable"
    )
    assert unlock_state(target, 0, {target}, 0)["state"] == "open"


def test_the_level_gate_is_checked_before_the_price():
    """An under-levelled member is told the level, not the price — being told
    to save up for something you couldn't buy anyway is a dead end."""
    target = SPOT_ORDER[-1]
    state = unlock_state(target, 0, set(), 0)
    assert state["state"] == "locked"
    assert str(SPOTS[target]["level"]) in state["reason"]


def test_a_chartered_spot_stays_open_when_you_are_broke():
    """The charter is bought once. Spending the coins afterwards must not strand
    an angler at the Trench."""
    assert unlock_state("abyss", 99, {"abyss"}, 0)["state"] == "open"
