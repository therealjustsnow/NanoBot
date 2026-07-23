"""cogs/crafting/recipes.py — the recipe registry.

A recipe is a data record: a key, an output item + quantity, and the item
inputs it consumes. Adding new content is a single `RecipeDef` entry here —
no command or schema changes. Recipes may output a brand-new `craft_*` item
(cogs/crafting/items.py) or an *existing* item from another package (e.g.
gold_ore + diamond → treasure_key) — recipes only reference item keys, they
don't care who registered them.

── Value math (checked against utils/items.py / cogs/activities/constants.py /
   cogs/fishing/items.py sell prices at the time these were written) ──────────

Material sell values in play: stone=5, coal=12, iron_ore=25, gold_ore=60,
diamond=200, pelt=20, meat=15, golden_antler=300. padlock and bait_glowgrub
have value=0 (not sellable) so they don't factor into an input-value sum.

The rule: a crafted item's sell `value` (utils/items.py) stays well below the
combined sell value of its inputs — crafting buys convenience/effects, not
free coins. Effect-only consumables are priced at value=0 (can't be
re-sold at all, so there's no exploit surface). Collectible outputs (misc/
treasure) may sit modestly above the input sum as a *time* sink, capped at
1.5x the summed input value:

  campfire_feast     meat×3 (45) + coal×2 (24)          = 69   → output value 0 (consumable)
  iron_pick_charm    iron_ore×5 (125) + stone×10 (50)   = 175  → output value 0 (consumable)
  reinforced_padlock padlock×1 (0) + iron_ore×3 (75)    = 75   → output value 0 (consumable)
  golden_lure        gold_ore×2 (120) + bait_glowgrub×2 (0) = 120 → output value 0 (consumable)
  fur_coat           pelt×5 (100)                        = 100  → output value 145  (1.45x, < 1.5x cap)
  trophy_mount       golden_antler×1 (300) + iron_ore×2 (50) = 350 → output value 500  (1.43x, < 1.5x cap)
  gem_ring           gold_ore×2 (120) + diamond×1 (200)  = 320  → output value 450  (1.41x, < 1.5x cap)
  treasure_key       gold_ore×3 (180) + diamond×1 (200)  = 380  → output value 0    (treasure_key itself
                                                                    isn't sellable — see utils/items.py)

All ratios above stay at or under the 1.5x collectible cap called for in the
design brief; consumables never carry a sell value at all, so there's nothing
to arbitrage there regardless of input cost.
"""

from dataclasses import dataclass, field

from utils import items as item_catalog


@dataclass(frozen=True)
class RecipeDef:
    key: str  # stable id used by /craft make|info — never rename once shipped
    output_item: str  # an item_key from utils.items.ITEMS
    output_qty: int
    inputs: dict[str, int] = field(default_factory=dict)  # item_key -> qty consumed
    description: str = ""


RECIPES: dict[str, RecipeDef] = {}


def register(*defs: RecipeDef) -> None:
    """Add recipe definitions to the registry."""
    for d in defs:
        RECIPES[d.key] = d


register(
    RecipeDef(
        key="campfire_feast",
        output_item="craft_campfire_feast",
        output_qty=1,
        inputs={"meat": 3, "coal": 2},
        description="Grill wild meat over hot coals for a luck-boosting feast.",
    ),
    RecipeDef(
        key="iron_pick_charm",
        output_item="craft_iron_pick_charm",
        output_qty=1,
        inputs={"iron_ore": 5, "stone": 10},
        description="Hammer scrap iron and stone into a lucky charm.",
    ),
    RecipeDef(
        key="reinforced_padlock",
        output_item="craft_reinforced_padlock",
        output_qty=1,
        inputs={"padlock": 1, "iron_ore": 3},
        description="Reinforce a padlock with iron for a longer /rob shield.",
    ),
    RecipeDef(
        key="golden_lure",
        output_item="craft_golden_lure",
        output_qty=1,
        inputs={"gold_ore": 2, "bait_glowgrub": 2},
        description="Plate a glowgrub lure in gold for a premium fishing bait.",
    ),
    RecipeDef(
        key="fur_coat",
        output_item="craft_fur_coat",
        output_qty=1,
        inputs={"pelt": 5},
        description="Stitch cured pelts into a warm, sellable coat.",
    ),
    RecipeDef(
        key="trophy_mount",
        output_item="craft_trophy_mount",
        output_qty=1,
        inputs={"golden_antler": 1, "iron_ore": 2},
        description="Mount a golden antler on an iron-braced plaque.",
    ),
    RecipeDef(
        key="gem_ring",
        output_item="craft_gem_ring",
        output_qty=1,
        inputs={"gold_ore": 2, "diamond": 1},
        description="Set a mined diamond into a gold band.",
    ),
    RecipeDef(
        key="treasure_key",
        output_item="treasure_key",
        output_qty=1,
        inputs={"gold_ore": 3, "diamond": 1},
        description="Forge a treasure key out of gold and diamond.",
    ),
)


def validate_recipes() -> list[str]:
    """Sanity-check the registry against the live item catalogue: unknown
    item keys, non-positive quantities, empty input lists. Returns a list of
    problem strings — empty means every recipe is well-formed.

    Requires every item-registering module (utils.items itself plus whichever
    feature packages contribute the materials referenced here — fishing,
    activities, crafting) to already be imported, since items only exist in
    utils.items.ITEMS after their module runs.
    """
    problems: list[str] = []
    for key, r in RECIPES.items():
        if not r.inputs:
            problems.append(f"{key}: has no inputs")
        if r.output_qty <= 0:
            problems.append(f"{key}: output_qty must be positive")
        if item_catalog.get(r.output_item) is None:
            problems.append(f"{key}: unknown output item {r.output_item!r}")
        for item_key, qty in r.inputs.items():
            if item_catalog.get(item_key) is None:
                problems.append(f"{key}: unknown input item {item_key!r}")
            if qty <= 0:
                problems.append(f"{key}: input {item_key!r} qty must be positive")
    return problems
