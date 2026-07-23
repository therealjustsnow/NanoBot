"""cogs/crafting/helpers.py — pure, Discord/DB-free crafting helpers.

Inventory snapshots are plain ``{item_key: qty}`` dicts (matching
``db.get_inventory``'s shape once flattened) so these stay unit-testable
without touching SQLite.
"""

from .recipes import RECIPES, RecipeDef

# Cap on how many of one recipe can be crafted in a single /craft make call.
MAX_CRAFT_QTY = 25


def clamp_craft_qty(qty: int | None) -> int:
    """Normalize a requested craft quantity into [1, MAX_CRAFT_QTY]."""
    return max(1, min(int(qty or 1), MAX_CRAFT_QTY))


def missing_inputs(
    recipe: RecipeDef, inventory: dict[str, int], qty: int = 1
) -> dict[str, int]:
    """item_key -> how many more are needed to craft `qty` of this recipe.

    An empty dict means the recipe is fully craftable right now from
    `inventory`.
    """
    missing: dict[str, int] = {}
    for item_key, need in recipe.inputs.items():
        total_need = need * qty
        have = inventory.get(item_key, 0)
        if have < total_need:
            missing[item_key] = total_need - have
    return missing


def can_craft(recipe: RecipeDef, inventory: dict[str, int], qty: int = 1) -> bool:
    """True when `inventory` covers every input for `qty` crafts."""
    return not missing_inputs(recipe, inventory, qty)


def find_recipe(query: str) -> RecipeDef | None:
    """Look a recipe up by key or (case-insensitive, space-tolerant) name.

    Mirrors utils.items.find's degrade-gracefully style: exact key match
    first (spaces normalized to underscores so "campfire feast" finds
    "campfire_feast"), then a case-insensitive match against either the
    recipe key or its crafted output item's display name.
    """
    from utils import items as item_catalog

    q = query.strip().lower()
    r = RECIPES.get(q.replace(" ", "_"))
    if r:
        return r
    for candidate in RECIPES.values():
        if candidate.key.lower() == q:
            return candidate
        out = item_catalog.get(candidate.output_item)
        if out and out.name.lower() == q:
            return candidate
    return None
