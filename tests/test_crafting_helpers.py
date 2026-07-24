"""
Tests for cogs/crafting/: the pure recipe/registry helpers (no Discord deps)
plus a db-level check of the atomic consume-with-refund-on-partial-failure
pattern /craft make relies on (mirroring tests/test_items_db.py's in-memory
SQLite fixture).
"""

import aiosqlite
import pytest

import utils.db as db
from utils import items as item_catalog

# Importing these packages registers every item the recipes reference
# (materials from activities/fishing, plus crafting's own craft_* outputs)
# into utils.items.ITEMS as a side effect.
import cogs.activities  # noqa: F401
import cogs.fishing  # noqa: F401
from cogs.crafting import (
    MAX_CRAFT_QTY,
    RECIPES,
    can_craft,
    clamp_craft_qty,
    find_recipe,
    missing_inputs,
    validate_recipes,
)


# ── Registry validation ──────────────────────────────────────────────────────
def test_registry_is_well_formed():
    assert validate_recipes() == []


def test_registry_is_not_empty():
    assert len(RECIPES) >= 7


def test_all_quantities_positive():
    for key, r in RECIPES.items():
        assert r.output_qty > 0, key
        assert r.inputs, key
        for item_key, qty in r.inputs.items():
            assert qty > 0, f"{key}: {item_key}"


def test_every_item_key_resolves():
    for key, r in RECIPES.items():
        assert item_catalog.get(r.output_item) is not None, key
        for item_key in r.inputs:
            assert item_catalog.get(item_key) is not None, f"{key}: {item_key}"


def test_no_sellable_output_exceeds_value_cap():
    """A sellable crafted output's coin value never exceeds 1.5x the summed
    sell value of its inputs — crafting is a convenience/collectible sink,
    not a way to launder cheap materials into expensive ones."""
    for key, r in RECIPES.items():
        out = item_catalog.get(r.output_item)
        if out is None or out.value <= 0:
            continue
        input_value = sum(
            (item_catalog.get(item_key).value or 0) * qty
            for item_key, qty in r.inputs.items()
        )
        assert out.value <= 1.5 * input_value, (
            f"{key}: output value {out.value} exceeds 1.5x input value "
            f"{input_value}"
        )


# ── find_recipe ───────────────────────────────────────────────────────────────
def test_find_recipe_by_key():
    assert find_recipe("fur_coat") is RECIPES["fur_coat"]


def test_find_recipe_by_key_with_spaces():
    assert find_recipe("fur coat") is RECIPES["fur_coat"]


def test_find_recipe_by_output_item_name():
    assert find_recipe("Gem Ring") is RECIPES["gem_ring"]


def test_find_recipe_case_insensitive():
    assert find_recipe("CAMPFIRE_FEAST") is RECIPES["campfire_feast"]


def test_find_recipe_unknown_returns_none():
    assert find_recipe("nonexistent_recipe") is None


# ── missing_inputs / can_craft ────────────────────────────────────────────────
def test_missing_inputs_empty_when_fully_stocked():
    recipe = RECIPES["fur_coat"]
    assert missing_inputs(recipe, {"pelt": 5}) == {}
    assert can_craft(recipe, {"pelt": 5}) is True


def test_missing_inputs_reports_shortfall():
    recipe = RECIPES["fur_coat"]
    assert missing_inputs(recipe, {"pelt": 2}) == {"pelt": 3}
    assert can_craft(recipe, {"pelt": 2}) is False


def test_missing_inputs_scales_with_qty():
    recipe = RECIPES["gem_ring"]  # gold_ore x2, diamond x1
    inv = {"gold_ore": 3, "diamond": 1}
    assert missing_inputs(recipe, inv, qty=2) == {"gold_ore": 1, "diamond": 1}


def test_missing_inputs_absent_item_counts_as_zero():
    recipe = RECIPES["campfire_feast"]  # meat x3, coal x2
    assert missing_inputs(recipe, {}) == {"meat": 3, "coal": 2}


# ── clamp_craft_qty ───────────────────────────────────────────────────────────
def test_clamp_craft_qty_defaults_to_one():
    assert clamp_craft_qty(None) == 1


def test_clamp_craft_qty_floors_at_one():
    assert clamp_craft_qty(0) == 1
    assert clamp_craft_qty(-5) == 1


def test_clamp_craft_qty_caps_at_max():
    assert clamp_craft_qty(9999) == MAX_CRAFT_QTY


def test_clamp_craft_qty_passthrough():
    assert clamp_craft_qty(3) == 3


# ── Atomic consume-with-refund-on-partial-failure (db level) ─────────────────
@pytest.fixture(autouse=True)
async def database(monkeypatch):
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    monkeypatch.setattr(db, "_db", conn)
    await db._ensure_items_tables()
    yield conn
    await conn.close()


G, U = 777, 1


async def _craft(guild_id: int, user_id: int, recipe, qty: int = 1):
    """Mirrors Crafting.craft_make's consume-then-refund-on-shortfall flow,
    exercised directly against the db layer (no Discord/cog involved)."""
    consumed: list[tuple[str, int]] = []
    for item_key, need in recipe.inputs.items():
        total = need * qty
        if await db.try_consume_item(user_id, item_key, total):
            consumed.append((item_key, total))
        else:
            for k, amount in consumed:
                await db.add_item(user_id, k, amount)
            return False
    await db.add_item(user_id, recipe.output_item, recipe.output_qty * qty)
    return True


async def test_successful_craft_consumes_all_inputs_and_grants_output():
    recipe = RECIPES["fur_coat"]  # pelt x5
    await db.add_item(U, "pelt", 5)
    assert await _craft(G, U, recipe) is True
    assert await db.get_item_qty(U, "pelt") == 0
    assert await db.get_item_qty(U, "craft_fur_coat") == 1


async def test_partial_failure_refunds_already_consumed_inputs():
    recipe = RECIPES["campfire_feast"]  # meat x3, coal x2
    await db.add_item(U, "meat", 3)
    # No coal at all — the second consume must fail and the meat must come back.
    assert await _craft(G, U, recipe) is False
    assert await db.get_item_qty(U, "meat") == 3
    assert await db.get_item_qty(U, "coal") == 0
    assert await db.get_item_qty(U, "craft_campfire_feast") == 0


async def test_partial_failure_refunds_multiple_already_consumed_inputs():
    recipe = RECIPES["trophy_mount"]  # golden_antler x1, iron_ore x2
    await db.add_item(U, "golden_antler", 1)
    await db.add_item(U, "iron_ore", 1)  # one short of the required 2
    assert await _craft(G, U, recipe) is False
    # The golden_antler consumed before the iron_ore shortfall was hit is refunded.
    assert await db.get_item_qty(U, "golden_antler") == 1
    assert await db.get_item_qty(U, "iron_ore") == 1
    assert await db.get_item_qty(U, "craft_trophy_mount") == 0
