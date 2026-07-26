"""Fishing cog constants: the fish catalogue, rarity odds/colors, and rod tiers."""

# Seconds between casts. One fixed, bot-wide value — not a per-guild setting:
# the cast claim is per user (global), so a per-server number was ambiguous the
# moment a member fished in two servers, and it let one server set the pace of
# everyone's economy. See the balance note on FISH below.
CAST_COOLDOWN = 20

# Base rarity odds at luck 0 (a bare Twig & String). Must sum to 1.0. Rod luck
# shifts mass from junk/common up the table — see helpers.rarity_odds.
RARITY_ODDS: list[tuple[str, float]] = [
    ("junk", 0.15),
    ("common", 0.45),
    ("uncommon", 0.22),
    ("rare", 0.11),
    ("epic", 0.045),
    ("legendary", 0.01),
    ("treasure", 0.015),
]

# rarity → (display label, embed color)
RARITIES: dict[str, tuple[str, int]] = {
    "junk": ("🗑️ Junk", 0x95A5A6),
    "common": ("⚪ Common", 0x99AAB5),
    "uncommon": ("🟢 Uncommon", 0x57F287),
    "rare": ("🔵 Rare", 0x3498DB),
    "epic": ("🟣 Epic", 0x9B59B6),
    "legendary": ("🟠 Legendary", 0xE67E22),
    "treasure": ("💰 Treasure", 0xF1C40F),
}

# The catalogue. `weight` is the (min, max) kg roll; `value` is the base coin
# worth — junk sells for exactly `value`, fish scale 0.6x–1.4x by weight (see
# helpers.catch_value), treasure pays 0.5x–1.5x in coins immediately and never
# enters the bag.
#
# Balance note (mirrors cogs/activities/constants.py): EV per cast is ~28
# coins at luck 0 and ~62 coins at the luck cap of 1.0 (Golden Rod + level cap
# + Glowgrub bait reaches it). At the 20s CAST_COOLDOWN that is ~5,000–11,200
# coins/hour of *active, per-command* play — by far the biggest faucet in the
# economy (/work is ~100–145/h), and it takes a full hour of uninterrupted
# casting to reach. Sinks (rod ladder, pickaxes, shop, prestige, casino house
# edge) are what hold the line; if prices ever feel trivial, raise this number
# rather than nerfing individual fish.
#
# On the database side 20s is comfortable: a cast costs ~1.9 ms of the single
# shared connection, so 1,000 members casting flat out is ~50 casts/s ≈ 9.5% of
# one connection-second (saturation needs ~10k such members). The limit here is
# economy balance, not throughput.
FISH: dict[str, dict] = {
    # ── junk ──
    "boot": {
        "name": "Old Boot",
        "emoji": "👢",
        "rarity": "junk",
        "weight": (0.4, 1.2),
        "value": 1,
    },
    "can": {
        "name": "Rusty Can",
        "emoji": "🥫",
        "rarity": "junk",
        "weight": (0.1, 0.3),
        "value": 1,
    },
    "driftwood": {
        "name": "Driftwood",
        "emoji": "🪵",
        "rarity": "junk",
        "weight": (0.5, 3.0),
        "value": 2,
    },
    "kelp": {
        "name": "Tangled Kelp",
        "emoji": "🌿",
        "rarity": "junk",
        "weight": (0.2, 1.0),
        "value": 2,
    },
    # ── common ──
    "anchovy": {
        "name": "Anchovy",
        "emoji": "🐟",
        "rarity": "common",
        "weight": (0.01, 0.05),
        "value": 5,
    },
    "sardine": {
        "name": "Sardine",
        "emoji": "🐟",
        "rarity": "common",
        "weight": (0.03, 0.2),
        "value": 6,
    },
    "herring": {
        "name": "Herring",
        "emoji": "🐟",
        "rarity": "common",
        "weight": (0.2, 0.6),
        "value": 7,
    },
    "perch": {
        "name": "Perch",
        "emoji": "🐟",
        "rarity": "common",
        "weight": (0.3, 1.5),
        "value": 8,
    },
    "bluegill": {
        "name": "Bluegill",
        "emoji": "🐟",
        "rarity": "common",
        "weight": (0.2, 1.0),
        "value": 8,
    },
    "mackerel": {
        "name": "Mackerel",
        "emoji": "🐟",
        "rarity": "common",
        "weight": (0.5, 2.0),
        "value": 10,
    },
    # ── uncommon ──
    "bass": {
        "name": "Bass",
        "emoji": "🐟",
        "rarity": "uncommon",
        "weight": (1.0, 6.0),
        "value": 18,
    },
    "trout": {
        "name": "Rainbow Trout",
        "emoji": "🐟",
        "rarity": "uncommon",
        "weight": (1.0, 7.0),
        "value": 20,
    },
    "snapper": {
        "name": "Red Snapper",
        "emoji": "🐠",
        "rarity": "uncommon",
        "weight": (1.0, 8.0),
        "value": 24,
    },
    "catfish": {
        "name": "Catfish",
        "emoji": "🐟",
        "rarity": "uncommon",
        "weight": (2.0, 20.0),
        "value": 24,
    },
    "crab": {
        "name": "Crab",
        "emoji": "🦀",
        "rarity": "uncommon",
        "weight": (0.5, 3.0),
        "value": 28,
    },
    # ── rare ──
    "salmon": {
        "name": "Salmon",
        "emoji": "🐟",
        "rarity": "rare",
        "weight": (3.0, 15.0),
        "value": 45,
    },
    "pike": {
        "name": "Northern Pike",
        "emoji": "🐟",
        "rarity": "rare",
        "weight": (2.0, 12.0),
        "value": 50,
    },
    "lobster": {
        "name": "Lobster",
        "emoji": "🦞",
        "rarity": "rare",
        "weight": (0.5, 4.0),
        "value": 60,
    },
    "pufferfish": {
        "name": "Pufferfish",
        "emoji": "🐡",
        "rarity": "rare",
        "weight": (0.5, 5.0),
        "value": 60,
    },
    "tuna": {
        "name": "Bluefin Tuna",
        "emoji": "🐟",
        "rarity": "rare",
        "weight": (10.0, 250.0),
        "value": 70,
    },
    # ── epic ──
    "anglerfish": {
        "name": "Anglerfish",
        "emoji": "🎣",
        "rarity": "epic",
        "weight": (5.0, 30.0),
        "value": 120,
    },
    "octopus": {
        "name": "Giant Octopus",
        "emoji": "🐙",
        "rarity": "epic",
        "weight": (3.0, 50.0),
        "value": 140,
    },
    "swordfish": {
        "name": "Swordfish",
        "emoji": "⚔️",
        "rarity": "epic",
        "weight": (30.0, 400.0),
        "value": 160,
    },
    "shark": {
        "name": "Great White Shark",
        "emoji": "🦈",
        "rarity": "epic",
        "weight": (50.0, 1000.0),
        "value": 180,
    },
    # ── legendary ──
    "golden_koi": {
        "name": "Golden Koi",
        "emoji": "🌟",
        "rarity": "legendary",
        "weight": (2.0, 10.0),
        "value": 400,
    },
    "giant_squid": {
        "name": "Giant Squid",
        "emoji": "🦑",
        "rarity": "legendary",
        "weight": (100.0, 400.0),
        "value": 500,
    },
    "whale": {
        "name": "Blue Whale",
        "emoji": "🐳",
        "rarity": "legendary",
        "weight": (1000.0, 5000.0),
        "value": 650,
    },
    # ── treasure (paid in coins on the spot, never bagged) ──
    "bottle": {
        "name": "Message in a Bottle",
        "emoji": "🍾",
        "rarity": "treasure",
        "weight": (0.5, 1.0),
        "value": 60,
    },
    "pearl": {
        "name": "Pristine Pearl",
        "emoji": "🦪",
        "rarity": "treasure",
        "weight": (0.1, 0.5),
        "value": 100,
    },
    "chest": {
        "name": "Sunken Chest",
        "emoji": "💰",
        "rarity": "treasure",
        "weight": (5.0, 20.0),
        "value": 150,
    },
}

# Catalogue keys grouped by rarity, in FISH insertion order (pick_fish indexes
# into these lists, so order is part of the deterministic-roll contract).
FISH_BY_RARITY: dict[str, list[str]] = {}
for _key, _fish in FISH.items():
    FISH_BY_RARITY.setdefault(_fish["rarity"], []).append(_key)

# Rod ladder, indexed by the stored rod_level. `luck` (0..1) feeds
# helpers.rarity_odds; prices are a deliberate coin sink for the economy.
# The rod ladder is the progression spine and the economy's biggest sink, so it
# carries the shape of the whole curve rather than a flat multiplier. Income per
# hour tripled when CAST_COOLDOWN went 60s -> 20s; each tier's price is scaled by
# a *different* factor so the pace changes deliberately by stage:
#
#   tier            price x   time-to-afford vs the 60s economy
#   Wooden           x1.5     ~2x faster    (a first upgrade inside ~10 min)
#   Fiberglass       x2       ~1.5x faster  (early game still snappy)
#   Carbon           x3       unchanged     (mid game keeps its old pacing)
#   Golden           x4       ~1.3x slower
#   Mythic Trident   x6       ~2x slower    (the endgame goal stays a goal)
#
# Full ladder: ~47h of active casting before, ~80h now — front-loaded so new
# anglers feel progress immediately and the last tier is worth more, not less.
# tests/test_economy_balance.py recomputes those ratios from FISH + RARITY_ODDS
# and fails if a price edit quietly flattens the curve.
RODS: list[dict] = [
    {"name": "Twig & String", "emoji": "🥢", "price": 0, "luck": 0.0},
    {"name": "Wooden Rod", "emoji": "🎣", "price": 750, "luck": 0.15},
    {"name": "Fiberglass Rod", "emoji": "🎣", "price": 5_000, "luck": 0.30},
    {"name": "Carbon Rod", "emoji": "🎣", "price": 24_000, "luck": 0.45},
    {"name": "Golden Rod", "emoji": "✨", "price": 100_000, "luck": 0.60},
    {"name": "Mythic Trident", "emoji": "🔱", "price": 450_000, "luck": 0.75},
]

# ── Fishing XP & levels ────────────────────────────────────────────────────────
# XP awarded per catch, by rarity — multiplied by any active fish_xp / double_xp
# effects/events at cast time (see helpers.fish_level / cog._do_cast).
XP_PER_RARITY: dict[str, int] = {
    "junk": 1,
    "common": 3,
    "uncommon": 6,
    "rare": 12,
    "epic": 25,
    "legendary": 60,
    "treasure": 40,
}

# Level n needs LEVEL_XP_BASE * n^2 cumulative XP (level 1 = 25 XP, level 10 =
# 2,500 XP, …) — see helpers.fish_level/cum_xp_for_level.
LEVEL_XP_BASE = 25

# Luck bonus per fishing level, capped — a level 30+ angler tops out at +15%.
LEVEL_LUCK_PER_LEVEL = 0.005
LEVEL_LUCK_CAP = 0.15

# Hard ceiling on combined luck (rod + level + bait + events) fed into
# helpers.rarity_odds, which independently clamps to the same range.
MAX_LUCK = 1.0

# ── Daily streak ─────────────────────────────────────────────────────────────
STREAK_COIN_PER_DAY = 10
STREAK_COIN_CAP = 100

# ── Daily quest pool ─────────────────────────────────────────────────────────
# Three quest shapes, picked + sized deterministically by helpers.generate_quest.
QUEST_CATCH_ANY_TARGET = (5, 15)
QUEST_CATCH_RARITY_POOL = ("uncommon", "rare", "epic")
QUEST_CATCH_RARITY_TARGET: dict[str, tuple[int, int]] = {
    "uncommon": (2, 5),
    "rare": (1, 3),
    "epic": (1, 2),
}
QUEST_EARN_COINS_TARGET = (50, 200)
QUEST_CATCH_RARITY_COIN_MULT: dict[str, int] = {"uncommon": 30, "rare": 60, "epic": 120}
QUEST_CATCH_RARITY_XP_MULT: dict[str, int] = {"uncommon": 15, "rare": 30, "epic": 60}
QUEST_CATCH_ANY_COIN_MULT = 8
QUEST_CATCH_ANY_XP_MULT = 4
QUEST_EARN_COINS_COIN_FRACTION = 0.5
QUEST_EARN_COINS_XP_FRACTION = 0.3

# ── Guild fishing events ─────────────────────────────────────────────────────
EVENT_CHANCE = 0.004  # ~0.4% chance per cast to start an event, if none active
EVENT_DURATION_RANGE = (600, 1200)  # 10-20 minutes

EVENT_POOL: list[dict] = [
    {"key": "frenzy", "weight": 0.4, "magnitude": 2.0},
    {"key": "double_xp", "weight": 0.35, "magnitude": 2.0},
    {"key": "lucky_waters", "weight": 0.25, "magnitude": 0.15},
]

EVENT_LABELS: dict[str, str] = {
    "frenzy": "🐟 Feeding Frenzy (catch values x2)",
    "double_xp": "⭐ Double XP",
    "lucky_waters": "🍀 Lucky Waters (+15% luck)",
}
