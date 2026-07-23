"""Item definitions the crafting cog contributes to the shared catalogue
(utils/items.py). Registered at import time — see utils/items.py's docstring
for the registration pattern.

Crafted item keys are prefixed `craft_` so they never collide with materials
contributed by other feature packages (activities' ores/pelts, fishing's
bait, the generic treasure_key/lucky_charm, …). Crafting only ever *consumes*
those existing items via recipes.py — it never redefines them here.
"""

from utils import items

items.register(
    items.ItemDef(
        key="craft_campfire_feast",
        name="Campfire Feast",
        emoji="🍖",
        category="consumable",
        description="A hearty grilled meal cooked over hot coals. Use it for "
        "+20% luck for 1 hour.",
        value=0,
        effect={"key": "luck", "magnitude": 0.2, "duration": 3600},
    ),
    items.ItemDef(
        key="craft_iron_pick_charm",
        name="Iron Pick Charm",
        emoji="🔩",
        category="consumable",
        description="A lucky charm hammered out of scrap iron and stone. Use "
        "it for +10% luck for 2 hours.",
        value=0,
        effect={"key": "luck", "magnitude": 0.1, "duration": 7200},
    ),
    items.ItemDef(
        key="craft_reinforced_padlock",
        name="Reinforced Padlock",
        emoji="🛡️",
        category="consumable",
        description="A padlock reinforced with scavenged iron. Use it to "
        "block /rob attempts for 3 days.",
        value=0,
        effect={"key": "rob_shield", "magnitude": 1, "duration": 259_200},
    ),
    items.ItemDef(
        key="craft_golden_lure",
        name="Golden Lure",
        emoji="✨",
        category="consumable",
        description="A hand-tooled lure plated in gold. Use it for +30% "
        "fishing luck, 5 casts.",
        value=0,
        effect={"key": "fish_bait", "magnitude": 0.3, "uses": 5},
    ),
    items.ItemDef(
        key="craft_fur_coat",
        name="Fur Coat",
        emoji="🧥",
        category="misc",
        description="A warm coat stitched together from cured pelts. A "
        "collectible with no use besides selling or showing off.",
        value=145,
    ),
    items.ItemDef(
        key="craft_trophy_mount",
        name="Trophy Mount",
        emoji="🦌",
        category="misc",
        description="A golden antler polished and mounted on a plaque. A "
        "prized hunting collectible.",
        value=500,
    ),
    items.ItemDef(
        key="craft_gem_ring",
        name="Gem Ring",
        emoji="💍",
        category="treasure",
        description="A gold band set with a mined diamond. A dazzling " "collectible.",
        value=450,
    ),
)
