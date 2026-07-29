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
FISH_NET = "fish_net"
FISH_TRAP = "fish_trap"

# What tackle is actually priced against
# ──────────────────────────────────────
# None of the fishing consumables pay for themselves in coins, and that is the
# design, not an oversight: a Worm costs 25 and its +5% luck over five casts is
# worth perhaps eight. They are coin *sinks* dressed as equipment, which is why
# tests/test_economy_balance.py asserts a bait costs a sane slice of a cast
# rather than that it turns a profit.
#
# So what the tackle below sells is **time**, which is the thing fishing is
# actually short of — a 20-second cooldown is the binding constraint on income,
# not the value of any one fish. A net beats the cooldown (three fish for one
# cast); a trap fishes while you are not there at all. Both are worth coins to
# a member for a reason that has nothing to do with their sale value, and both
# take coins out of the economy on the way.

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
    items.ItemDef(
        key=FISH_NET,
        name="Cast Net",
        emoji="🕸️",
        category="tackle",
        description="Arm it with /inventory use — your next 3 casts pull in "
        "three fish at once instead of one.",
        price=500,
        effect={"key": "fish_net", "magnitude": 3.0, "uses": 3},
    ),
    items.ItemDef(
        key=FISH_TRAP,
        name="Fish Trap",
        emoji="🪤",
        category="tackle",
        # Not an /inventory use effect: a trap goes in the water at a *place*
        # and is pulled later, which is a fishing action, so /fish trap owns it.
        description="Set it with /fish trap and leave it. Come back in a few "
        "hours for a full basket from wherever you set it.",
        price=250,
    ),
)
