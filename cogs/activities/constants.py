"""Activities cog constants: career ladder, catalogues, odds tables, tool
ladders, and the cooldown defaults/bounds.

Balance note (checked against the "≤~150 coins/hour of cooldown" guardrail):
  /work    1h  cooldown, pay 60-140 (+career bonus up to 45)   → EV ~100-145/h
  /mine   30m  cooldown, ore EV ~18-45 (luck-shifted)          → EV ~36-90/h
  /hunt   45m  cooldown, catch EV ~29 net of injury fine       → EV ~39/h
  /explore 3h  cooldown, coin EV ~125 (rest is non-coin items) → EV ~42/h
  /rob     4h  cooldown, steal capped 1000/attempt             → zero-sum
           wealth *transfer* (not newly minted coins) plus a fine that's a
           pure sink, so the per-hour income guardrail doesn't apply the same
           way — it never inflates the total coin supply.

Cross-server farming
────────────────────
Cooldown *claims* are global (utils/db/activities.py keys the stats row by
user_id alone), so running /work in one server blocks it in every other one.
That closes the obvious hole — but it also means a per-guild cooldown *length*
never described anything real: with coins and items global too, the SHORTEST
length among a member's servers was the one that actually governed them, and a
single permissive server minted coins spendable everywhere.

So the lengths are not a server setting at all. They are bot-wide and
owner-only (`!cooldown` in cogs/admin, stored in utils/db/settings.py); a guild
still decides whether an activity runs there, which is genuinely its own call
and affects only its own members. The defaults below are what an activity uses
until the owner overrides it, and `helpers.effective_cooldown` is the one place
a stored value is turned into a length — it falls back to the default rather
than to "no cooldown" if the value is missing or nonsense.
"""

# ══════════════════════════════════════════════════════════════════════════════
#  /work — safe, steady income + a career ladder
# ══════════════════════════════════════════════════════════════════════════════

WORK_PAY_MIN = 60
WORK_PAY_MAX = 140

WORK_COOLDOWN_DEFAULT = 3600  # 1 hour
WORK_COOLDOWN_MAX = 86_400

# Lifetime shift count → (title, flat pay bonus). The highest threshold a
# member's shift count meets or exceeds wins (see helpers.career_info).
CAREER_LADDER: list[tuple[int, str, int]] = [
    (0, "🍵 Intern", 0),
    (10, "📋 Junior Associate", 5),
    (25, "🗂️ Associate", 10),
    (50, "📊 Senior Associate", 15),
    (100, "🧑‍💼 Manager", 20),
    (200, "📈 Senior Manager", 25),
    (400, "🏢 Director", 30),
    (750, "🎩 Vice President", 35),
    (1500, "👑 Executive", 40),
    (3000, "🏆 Legend of the Office", 45),
]

WORK_SCENES: list[str] = [
    "You clock in early and knock out the morning rush.",
    "A double shift at the front desk pays off.",
    "You cover for a sick coworker and the manager notices.",
    "Inventory day — tedious, but the paycheck doesn't care.",
    "A tricky customer, a lot of patience, and a solid tip jar.",
    "You finally fix that jammed printer everyone gave up on.",
    "Overtime on a big order — exhausting but lucrative.",
    "A quiet shift lets you catch up on a mountain of paperwork.",
    "You train the new hire and somehow still hit your numbers.",
    "A surprise inspection goes smoothly thanks to you.",
]

# ══════════════════════════════════════════════════════════════════════════════
#  /mine — ore mining, pickaxe ladder, occasional cave-in
# ══════════════════════════════════════════════════════════════════════════════

MINE_COOLDOWN_DEFAULT = 1800  # 30 minutes
MINE_COOLDOWN_MAX = 86_400

# 8% of digs yield nothing at all (a cave-in), independent of ore rarity.
MINE_CAVE_IN_CHANCE = 0.08

# A small independent chance of a bonus treasure_key on top of the ore roll.
MINE_TREASURE_KEY_CHANCE = 0.05

ORES: dict[str, dict] = {
    "stone": {"name": "Stone", "emoji": "🪨", "value": 5},
    "coal": {"name": "Coal", "emoji": "⚫", "value": 12},
    "iron_ore": {"name": "Iron Ore", "emoji": "⚙️", "value": 25},
    "gold_ore": {"name": "Gold Ore", "emoji": "🟡", "value": 60},
    "diamond": {"name": "Diamond", "emoji": "💎", "value": 200},
}

# Base ore odds at pickaxe luck 0. Must sum to 1.0. Luck shifts mass from
# stone/coal up into iron/gold/diamond — see helpers.mine_odds.
ORE_ODDS: list[tuple[str, float]] = [
    ("stone", 0.42),
    ("coal", 0.28),
    ("iron_ore", 0.19),
    ("gold_ore", 0.08),
    ("diamond", 0.03),
]

# Tiers luck shifts mass *into*, in table order.
_MINE_HIGH_TIERS = ("iron_ore", "gold_ore", "diamond")

# Pickaxe ladder, indexed by stored pickaxe_level. A deliberate coin sink;
# `luck` (0..1) feeds helpers.mine_odds.
#
# Priced off the same staged curve as the rod ladder (see cogs/fishing/
# constants.py RODS). Mining's own income didn't change, but coins are fungible
# and fishing is what actually pays for these — leaving the old numbers would
# have made the whole ladder 3x cheaper in real terms.
PICKAXES: list[dict] = [
    {"name": "Bare Hands", "emoji": "✊", "price": 0, "luck": 0.0},
    {"name": "Stone Pickaxe", "emoji": "⛏️", "price": 750, "luck": 0.15},
    {"name": "Iron Pickaxe", "emoji": "⛏️", "price": 4_000, "luck": 0.30},
    {"name": "Steel Pickaxe", "emoji": "⛏️", "price": 24_000, "luck": 0.45},
    {"name": "Obsidian Pickaxe", "emoji": "⛏️", "price": 100_000, "luck": 0.60},
]

# ══════════════════════════════════════════════════════════════════════════════
#  /hunt — medium-risk foraging with an injury chance
# ══════════════════════════════════════════════════════════════════════════════

HUNT_COOLDOWN_DEFAULT = 2700  # 45 minutes
HUNT_COOLDOWN_MAX = 86_400

HUNT_CATCHES: dict[str, dict] = {
    "pelt": {"name": "Animal Pelt", "emoji": "🦫", "value": 20},
    "meat": {"name": "Wild Meat", "emoji": "🥩", "value": 15},
    "golden_antler": {"name": "Golden Antler", "emoji": "🏆", "value": 300},
}

# Must sum to 1.0.
HUNT_ODDS: list[tuple[str, float]] = [
    ("pelt", 0.57),
    ("meat", 0.38),
    ("golden_antler", 0.05),
]

HUNT_INJURY_CHANCE = 0.12
HUNT_INJURY_FINE_MAX = 50  # coin fine rolled in [0, this], never below 0
HUNT_PADLOCK_CHANCE = 0.06  # independent chance of finding a defensive padlock

# ══════════════════════════════════════════════════════════════════════════════
#  /explore — long shot, high variance
# ══════════════════════════════════════════════════════════════════════════════

EXPLORE_COOLDOWN_DEFAULT = 10_800  # 3 hours
EXPLORE_COOLDOWN_MAX = 172_800

EXPLORE_COINS_SMALL = (100, 400)
EXPLORE_COINS_BIG = (500, 1000)

# Must sum to 1.0. Walked in order by helpers.pick_explore_outcome.
EXPLORE_OUTCOMES: list[tuple[str, float]] = [
    ("nothing", 0.30),
    ("coins_small", 0.35),
    ("treasure_key", 0.12),
    ("treasure_chest", 0.08),
    ("lucky_charm", 0.10),
    ("coins_big", 0.05),
]

EXPLORE_FLAVOR: dict[str, str] = {
    "nothing": "You wander for hours and find... nothing but a nice view.",
    "coins_small": "You spot something glinting in the underbrush.",
    "treasure_key": "A rusted key, half-buried, catches your eye.",
    "treasure_chest": "You stumble onto a locked chest, half-buried in the dirt.",
    "lucky_charm": "A four-leaf clover, somehow still fresh.",
    "coins_big": "You crack open an old cache no one else ever found.",
}

# ══════════════════════════════════════════════════════════════════════════════
#  /rob — PvP risk
# ══════════════════════════════════════════════════════════════════════════════

ROB_COOLDOWN_DEFAULT = 14_400  # 4 hours
ROB_COOLDOWN_MAX = 172_800

ROB_MIN_ROBBER_BALANCE = 250
ROB_MIN_TARGET_BALANCE = 500

ROB_STEAL_MIN_PCT = 0.10
ROB_STEAL_MAX_PCT = 0.20
ROB_STEAL_CAP = 1000

ROB_BASE_SUCCESS = 0.35
ROB_LUCK_BONUS = 0.10  # added when the robber has an active "luck" effect
ROB_SUCCESS_CAP = 0.50

ROB_FINE = 200  # coins the robber pays on a failed attempt (a pure sink)

# ══════════════════════════════════════════════════════════════════════════════
#  Shared admin metadata
# ══════════════════════════════════════════════════════════════════════════════

ACTIVITY_NAMES: tuple[str, ...] = ("work", "mine", "hunt", "explore", "rob")

# Player-facing metadata for the five activities: what to type, what it does,
# and how risky it is. Drives the /adventure overview card and the admin
# toggle/cooldown pickers, so a new activity describes itself in one place.
ACTIVITY_INFO: dict[str, dict] = {
    "work": {
        "emoji": "💼",
        "command": "/work",
        "blurb": "Safe. Steady pay and a career ladder.",
    },
    "mine": {
        "emoji": "⛏️",
        "command": "/mine",
        "blurb": "Dig ore to sell. Small cave-in risk.",
    },
    "hunt": {
        "emoji": "🏹",
        "command": "/adventure hunt",
        "blurb": "Pelts, meat, rare trophy. Injury risk.",
    },
    "explore": {
        "emoji": "🧭",
        "command": "/adventure explore",
        "blurb": "Long shot: usually nothing, sometimes huge.",
    },
    "rob": {
        "emoji": "🥷",
        "command": "/rob",
        "blurb": "Steal from a member. Fail and you're fined.",
    },
}

# Cooldown values suggested by the owner-only !cooldown command, filtered to
# each activity's own bounds. Any exact number in range still works.
COOLDOWN_PRESETS: tuple[int, ...] = (
    60,
    300,
    900,
    1800,
    2700,
    3600,
    7200,
    10_800,
    21_600,
    43_200,
    86_400,
    172_800,
)

# /squad and /raid live in cogs/economy, but their payout is a per-user claim on
# a global wallet exactly like an activity's, so they share this registry (and
# therefore `!cooldown` and `effective_cooldown`). They are NOT in
# ACTIVITY_NAMES: /adventure neither lists nor toggles them.
#
# The lengths follow the same "≤~150 coins/hour" guardrail as the activities
# above, measured against the default rewards: /squad pays 50 per member, so 30
# minutes is 100/hour; /raid pays 100, so an hour is 100/hour. Before this they
# had no claim at all — only the invoker's few-second command cooldown — so two
# members could confirm a squad every half-minute for ~6,000 coins/hour each,
# guaranteed and risk-free.
COOP_COOLDOWN_DEFAULT = 1800  # 30 minutes
COOP_COOLDOWN_MAX = 86_400
RAID_COOLDOWN_DEFAULT = 3600  # 1 hour
RAID_COOLDOWN_MAX = 172_800

ACTIVITY_DEFAULT_COOLDOWNS: dict[str, int] = {
    "work": WORK_COOLDOWN_DEFAULT,
    "mine": MINE_COOLDOWN_DEFAULT,
    "hunt": HUNT_COOLDOWN_DEFAULT,
    "explore": EXPLORE_COOLDOWN_DEFAULT,
    "rob": ROB_COOLDOWN_DEFAULT,
    "coop": COOP_COOLDOWN_DEFAULT,
    "raid": RAID_COOLDOWN_DEFAULT,
}

# The smallest length the owner can set. Not a balance guardrail — the owner
# runs the bot and can make an activity as fast as they like — just a floor
# that keeps a typo from turning an activity into a no-cooldown coin printer.
COOLDOWN_MIN = 10

ACTIVITY_COOLDOWN_BOUNDS: dict[str, tuple[int, int]] = {
    "work": (COOLDOWN_MIN, WORK_COOLDOWN_MAX),
    "mine": (COOLDOWN_MIN, MINE_COOLDOWN_MAX),
    "hunt": (COOLDOWN_MIN, HUNT_COOLDOWN_MAX),
    "explore": (COOLDOWN_MIN, EXPLORE_COOLDOWN_MAX),
    "rob": (COOLDOWN_MIN, ROB_COOLDOWN_MAX),
    "coop": (COOLDOWN_MIN, COOP_COOLDOWN_MAX),
    "raid": (COOLDOWN_MIN, RAID_COOLDOWN_MAX),
}

# Everything `!cooldown` can set — the five /adventure activities plus the two
# co-op payouts. ACTIVITY_NAMES stays the /adventure surface on purpose.
COOLDOWN_KEYS: tuple[str, ...] = tuple(ACTIVITY_DEFAULT_COOLDOWNS)
