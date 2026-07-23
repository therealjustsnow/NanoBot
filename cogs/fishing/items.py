"""cogs/fishing/items.py — bait & consumable item definitions.

Registered into the shared item catalogue (utils/items.py) at import time, so
the generic /inventory commands (list/use/sell/give) work with these items for
free — /inventory use is what arms the "fish_bait"/"fish_xp" effect that
cogs/fishing/cog.py reads at cast time. Fishing-specific keys are prefixed
`bait_`/`fish_` so /fish buy can restrict its shop to just these without a
separate catalogue.
"""

from utils import items

BAIT_WORM = "bait_worm"
BAIT_SHRIMP = "bait_shrimp"
BAIT_GLOWGRUB = "bait_glowgrub"
BAIT_MAGNET = "bait_magnet"
FISH_XP_POTION = "fish_xp_potion"

items.register(
    items.ItemDef(
        key=BAIT_WORM,
        name="Worm",
        emoji="🪱",
        category="bait",
        description="Basic bait. Arm it with /inventory use for +5% luck, " "5 casts.",
        price=25,
        effect={"key": "fish_bait", "magnitude": 0.05, "uses": 5},
    ),
    items.ItemDef(
        key=BAIT_SHRIMP,
        name="Shrimp",
        emoji="🦐",
        category="bait",
        description="Fresh bait. Arm it with /inventory use for +12% luck, " "5 casts.",
        price=100,
        effect={"key": "fish_bait", "magnitude": 0.12, "uses": 5},
    ),
    items.ItemDef(
        key=BAIT_GLOWGRUB,
        name="Glowgrub",
        emoji="🟢",
        category="bait",
        description="Glowing lure. Arm it with /inventory use for +25% luck, "
        "5 casts.",
        price=300,
        effect={"key": "fish_bait", "magnitude": 0.25, "uses": 5},
    ),
    items.ItemDef(
        key=BAIT_MAGNET,
        name="Treasure Magnet",
        emoji="🧲",
        category="bait",
        description="Premium bait tuned for the big pulls. Arm it with "
        "/inventory use for +35% luck, 3 casts.",
        price=500,
        effect={"key": "fish_bait", "magnitude": 0.35, "uses": 3},
    ),
    items.ItemDef(
        key=FISH_XP_POTION,
        name="XP Potion",
        emoji="🧪",
        category="consumable",
        description="Arm it with /inventory use to double fishing XP for "
        "30 minutes.",
        price=200,
        effect={"key": "fish_xp", "magnitude": 2.0, "duration": 1800},
    ),
)
