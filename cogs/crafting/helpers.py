"""cogs/crafting/helpers.py — pure, Discord/DB-free crafting helpers.

Inventory snapshots are plain ``{item_key: qty}`` dicts (matching
``db.get_inventory``'s shape once flattened) so these stay unit-testable
without touching SQLite.
"""

from .recipes import RECIPES, RecipeDef

# Cap on how many of one recipe can be crafted in a single /craft make call.
MAX_CRAFT_QTY = 25

# What a member can type instead of a number to mean "as many as I can".
# Nobody counts their own ore before crafting, so the quantity that is actually
# wanted is almost always this one — the numbers are the special case.
MAX_WORDS = frozenset({"max", "all", "everything", "*", "full"})

# What a member can type instead of a recipe name to mean "every recipe I can
# make". The /inventory sell bulk targets, in the same words.
ALL_WORDS = frozenset({"all", "everything", "*"})


def clamp_craft_qty(qty: int | None) -> int:
    """Normalize a requested craft quantity into [1, MAX_CRAFT_QTY]."""
    return max(1, min(int(qty or 1), MAX_CRAFT_QTY))


def max_craftable(recipe: RecipeDef, inventory: dict[str, int]) -> int:
    """How many of `recipe` this inventory covers, before the per-call cap.

    Uncapped on purpose: the answer to "how many can I make" is a fact about
    the materials, and clamping it here would make a screen quoting it lie
    about what someone owns. Call sites pass it through clamp_craft_qty when
    they're about to *craft*.
    """
    if not recipe.inputs:
        return 0
    return min(inventory.get(key, 0) // need for key, need in recipe.inputs.items())


def parse_craft_qty(raw: str | int | None, maximum: int) -> int | None:
    """Read a quantity option that may be a number or a max-it-out word.

    Returns the clamped count, or None when `raw` is neither — the caller words
    its own refusal rather than this guessing at 1, since crafting the wrong
    amount consumes materials.
    """
    if raw is None or raw == "":
        return 1
    if isinstance(raw, str):
        text = raw.strip().lower()
        if text in MAX_WORDS:
            return clamp_craft_qty(maximum) if maximum > 0 else 0
        try:
            raw = int(text)
        except ValueError:
            return None
    return clamp_craft_qty(raw)


def is_craft_all(query: str) -> bool:
    """True when a recipe option is really the craft-everything bulk target."""
    return query.strip().lower() in ALL_WORDS


def craft_all_plan(inventory: dict[str, int]) -> list[tuple[str, int]]:
    """What "craft everything" would make: [(recipe_key, qty), …].

    Pure over an inventory snapshot, so the preview and the crafting that
    follows it agree on the rules — the run simply re-evaluates it against a
    fresh read (the _bulk_plan contract).

    Recipes are walked in key order against a *simulated* inventory, so two
    recipes sharing an input can't both be promised the same ore. That makes
    the order load-bearing, which is why it's alphabetical rather than
    registry order: a recipe added later must not quietly re-shuffle what an
    existing plan makes.
    """
    left = dict(inventory)
    plan: list[tuple[str, int]] = []
    for key in sorted(RECIPES):
        recipe = RECIPES[key]
        possible = max_craftable(recipe, left)
        if possible <= 0:
            continue
        count = clamp_craft_qty(possible)
        for item_key, need in recipe.inputs.items():
            left[item_key] = left.get(item_key, 0) - need * count
        plan.append((key, count))
    return plan


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
