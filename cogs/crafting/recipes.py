"""cogs/crafting/recipes.py — the recipe registry.

A recipe is a data record: a key, an output item + quantity, and the item
inputs it consumes. Adding new content is a single `RecipeDef` entry here —
no command or schema changes. Recipes may output a brand-new `craft_*` item
(cogs/crafting/items.py) or an *existing* item from another package (e.g.
gold_ore + diamond → treasure_key) — recipes only reference item keys, they
don't care who registered them.

── Value math (checked against utils/items.py / cogs/activities/constants.py /
   cogs/fishing/items.py sell prices at the time these were written) ──────────

Material sell values in play: stone=8, coal=18, iron_ore=38, gold_ore=90,
diamond=300, pelt=30, meat=22, golden_antler=450. padlock and bait_glowgrub
have value=0 (not sellable) so they don't factor into an input-value sum.

The rule has **two** sides, and the floor is the one that is easy to lose.
Effect-only consumables are priced at value=0 (can't be re-sold at all, so
there's no exploit surface regardless of input cost). A *collectible* output
(misc/treasure) exists to be crafted and sold, so its sell `value` must land
in a band: strictly **above** the summed sell value of its inputs, or the
recipe is a coin shredder and nobody with a calculator would ever run it —
and at or under **1.5x** that sum, so crafting stays a modest time sink
rather than a faucet. The band is 1.3x–1.5x in practice:

  campfire_feast     meat×3 (66) + coal×2 (36)           = 102 → output value 0 (consumable)
  iron_pick_charm    iron_ore×5 (190) + stone×10 (80)    = 270 → output value 0 (consumable)
  reinforced_padlock padlock×1 (0) + iron_ore×3 (114)    = 114 → output value 0 (consumable)
  golden_lure        gold_ore×2 (180) + bait_glowgrub×2 (0) = 180 → output value 0 (consumable)
  fur_coat           pelt×5 (150)                        = 150 → output value 215  (1.43x)
  trophy_mount       golden_antler×1 (450) + iron_ore×2 (76) = 526 → output value 750  (1.43x)
  gem_ring           gold_ore×2 (180) + diamond×1 (300)  = 480 → output value 675  (1.41x)
  treasure_key       gold_ore×3 (270) + diamond×1 (300)  = 570 → output value 0    (treasure_key itself
                                                                    isn't sellable — see utils/items.py)

**Repricing a material means repricing every collectible made from it.** The
ore/pelt tables were raised ~1.5x when the economy was paced for two visits a
day, and these three outputs were not, which quietly inverted all of them:
fur_coat cost 150 to make and sold for 145. The cap test passed the whole
time because a ratio that *falls* never trips a ceiling — so the floor is
asserted too now, in tests/test_crafting_helpers.py.
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
