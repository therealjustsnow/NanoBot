"""Activities cog constants: career ladder, catalogues, odds tables, tool
ladders, encounters, and the interval defaults/bounds.

Why these numbers moved
───────────────────────
The adventure loop used to pay ~220 coins/hour with every activity claimed
around the clock, against fishing's ~5,100 for an hour of actual casting. That
ratio is what made the shop unreachable for the people who *prefer* this loop:
a 55,000-coin cosmetic was eleven hours of fishing or two hundred and fifty of
adventuring, so anyone who ignored fishing simply never bought anything.

The diagnosis was not that the payouts were small. Per *action* they were
generous — a shift paid 100 against a cast's 28. The problem was that the loop
offered about five actions an hour and fishing offered a hundred and eighty. So
both halves of the fix are about actions, not multipliers:

  * intervals came down and every activity now banks charges (see
    `ACTIVITY_MAX_CHARGES` and utils.db.activities.try_claim_activity), so
    being away for an hour returns three taps rather than one, and a cooldown
    stops being a punishment for having a life;
  * the yields that were a flat one-item-per-run grew a spread — ore comes in
    veins, a hunt fills a bag — so the same tap has a range worth watching.

Balance after the change (all five claimed at full rate, before encounters and
the daily streak):
  /work    20m x3 charges, pay 100-200 (+career bonus up to 72)  → EV ~505/h
  /mine    12m x4 charges, ore EV ~21 x a ~1.96 vein, 8% cave-in → EV ~190/h
  /hunt    15m x3 charges, catch EV ~32 x a ~1.75 bag, net fine  → EV ~210/h
  /explore 45m x2 charges, coin EV ~220 (rest is non-coin items) → EV ~295/h
  /rob      2h x1 charge,  steal capped 1000/attempt             → zero-sum
           wealth *transfer* (not newly minted coins) plus a fine that's a
           pure sink, so the per-hour income guardrail doesn't apply the same
           way — it never inflates the total coin supply.

That is ~1,200/h, a shade under a quarter of fishing, up from a twentieth.
Fishing stays the fastest way to earn — it is the one faucet that rewards
sitting still and grinding, and nothing here should take that away from the
people doing it — but the adventure loop is now a real second route to the
shop rather than a rounding error beside it. `helpers.adventure_coins_per_hour`
computes that figure from the tables in this file, and
tests/test_economy_balance.py recomputes it independently and asserts the ratio
to fishing, so neither side can drift.

Two multipliers ride on top and are deliberately excluded from the figure
above, because both are earned rather than idle: encounters (`ENCOUNTERS`, a
follow-up choice on ~8% of runs, worth roughly +8%) and the daily streak
(`STREAK_*`, up to +25% on coin payouts for showing up seven days running).

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

Charge *caps* are not settable at all — they are the code constants in
`ACTIVITY_MAX_CHARGES`. The owner's interval already sets how fast an activity
pays; a cap only decides how long you may go without collecting, and the long-run
rate is identical either way. Making it a knob would offer a choice with no
balance consequence and one more thing to get wrong.
"""

# ══════════════════════════════════════════════════════════════════════════════
#  /work — safe, steady income + a career ladder
# ══════════════════════════════════════════════════════════════════════════════

WORK_PAY_MIN = 100
WORK_PAY_MAX = 200

WORK_COOLDOWN_DEFAULT = 1200  # 20 minutes
WORK_COOLDOWN_MAX = 86_400

# Lifetime shift count → (title, flat pay bonus). The highest threshold a
# member's shift count meets or exceeds wins (see helpers.career_info).
#
# The bonuses scaled with the base pay (roughly 1.6x) so a promotion is worth
# the same *share* of a paycheck it always was. The thresholds did not: shifts
# now come three times as fast, so leaving them alone is what stops the whole
# ladder being climbed in an afternoon.
CAREER_LADDER: list[tuple[int, str, int]] = [
    (0, "🍵 Intern", 0),
    (10, "📋 Junior Associate", 8),
    (25, "🗂️ Associate", 16),
    (50, "📊 Senior Associate", 24),
    (100, "🧑‍💼 Manager", 32),
    (200, "📈 Senior Manager", 40),
    (400, "🏢 Director", 48),
    (750, "🎩 Vice President", 56),
    (1500, "👑 Executive", 64),
    (3000, "🏆 Legend of the Office", 72),
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

MINE_COOLDOWN_DEFAULT = 720  # 12 minutes
MINE_COOLDOWN_MAX = 86_400

# 8% of digs yield nothing at all (a cave-in), independent of ore rarity.
MINE_CAVE_IN_CHANCE = 0.08

# A small independent chance of a bonus treasure_key on top of the ore roll.
#
# Deliberately low relative to the chest rate below. A key is worthless on its
# own — it only ever unlocks a treasure_chest, and /explore is the sole chest
# source. Mining claims 120x a day against explore's 32, so even a modest rate
# here dominates the key supply: at the original 0.05 it minted keys players
# banked and could never spend. The roll is per *dig*, not per ore, so the vein
# table below doesn't multiply it.
MINE_TREASURE_KEY_CHANCE = 0.02

# How much ore one successful dig yields. A flat one-per-dig made every
# non-cave-in dig identical; a vein gives the same tap something to hope for,
# and carries most of mining's income increase without touching ore *values*
# (which /inventory sell and every crafting recipe are priced against).
# Must sum to 1.0. EV ≈ 1.96 ore per successful dig.
MINE_VEIN_ODDS: list[tuple[int, float]] = [
    (1, 0.40),
    (2, 0.32),
    (3, 0.20),
    (4, 0.08),
]

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

HUNT_COOLDOWN_DEFAULT = 900  # 15 minutes
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
HUNT_INJURY_FINE_MAX = 80  # coin fine rolled in [0, this], never below 0
HUNT_PADLOCK_CHANCE = 0.06  # independent chance of finding a defensive padlock

# How much a single hunt brings back — mining's vein table, for the same
# reason. Must sum to 1.0. EV ≈ 1.75 catches per hunt.
HUNT_BAG_ODDS: list[tuple[int, float]] = [
    (1, 0.45),
    (2, 0.35),
    (3, 0.20),
]

# ══════════════════════════════════════════════════════════════════════════════
#  /explore — long shot, high variance
# ══════════════════════════════════════════════════════════════════════════════

EXPLORE_COOLDOWN_DEFAULT = 2700  # 45 minutes
EXPLORE_COOLDOWN_MAX = 172_800

# Explore is the loop's lottery ticket, so its purses carry the variance the
# other four deliberately don't. The gap between the two matters more than
# either number: a big find has to feel like a different event, not a good roll.
EXPLORE_COINS_SMALL = (200, 600)
EXPLORE_COINS_BIG = (1000, 2200)

# Must sum to 1.0. Walked in order by helpers.pick_explore_outcome.
#
# The chest weight sits *above* the key weight on purpose. Explore is the only
# chest faucet, while keys also drop from /mine (a 30m cooldown against this
# one's 3h) and can be crafted, so weighting keys higher here stacked a second
# surplus on top of mining's. Together with MINE_TREASURE_KEY_CHANCE this lands
# at roughly 1.5 keys per chest at full claim rate — enough slack that a found
# chest is never stranded without a key, without banking keys that never open
# anything.
EXPLORE_OUTCOMES: list[tuple[str, float]] = [
    ("nothing", 0.30),
    ("coins_small", 0.35),
    ("treasure_key", 0.07),
    ("treasure_chest", 0.13),
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

ROB_COOLDOWN_DEFAULT = 7200  # 2 hours
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
#  Charges — how many runs an activity banks while you're away
# ══════════════════════════════════════════════════════════════════════════════
#
# See utils.db.activities.try_claim_activity for the mechanism. These caps
# change the *shape* of a session, never its rate: three banked shifts pay
# exactly what three shifts an hour apart would have.
#
# Sized so a member who checks in twice a day loses nothing to the cap on the
# fast activities, and so no single visit is longer than a handful of taps.
# /rob is the one activity that deliberately banks nothing — a stored-up run of
# robberies is a different (and much less welcome) experience for the person on
# the receiving end, and the cooldown there is a protection, not a pacer.
ACTIVITY_MAX_CHARGES: dict[str, int] = {
    "work": 3,
    "mine": 4,
    "hunt": 3,
    "explore": 2,
    "rob": 1,
}

# ══════════════════════════════════════════════════════════════════════════════
#  Daily streak — a reason to come back tomorrow
# ══════════════════════════════════════════════════════════════════════════════
#
# One streak for the whole loop, not one per activity: the question it asks is
# "did you adventure today", and which of the five you happened to run is not
# interesting. Claimed once a day on the first activity you complete (the
# atomic stamp is db.try_claim_adventure_streak), and it multiplies COIN
# payouts only — the item activities can't pay a fraction of a pelt, and
# rounding one up would quietly make mining the best place to spend a streak.
STREAK_BONUS_PER_DAY = 0.05
STREAK_BONUS_CAP = 0.25  # reached on day 6, then held

# ══════════════════════════════════════════════════════════════════════════════
#  Encounters — a second decision inside one run
# ══════════════════════════════════════════════════════════════════════════════
#
# A run resolves in one roll, which is honest but flat. An encounter fires on a
# small share of them and hands the member a choice with no obviously correct
# answer: a safe option that always pays a little, and a greedy one with real
# variance. The point isn't the coins (the whole system is worth about +8% of
# income) — it's that the run stops being something you watch and becomes
# something you answer.
#
# Data-driven the way the item and cosmetic catalogues are: an encounter is a
# registry entry, its options are entries, and every outcome is a weighted row
# resolved by an explicit roll in helpers.resolve_encounter. Adding one is a
# dict, not a branch. /rob has none on purpose — it is already a coin flip with
# a decision in front of it.
ENCOUNTER_CHANCE = 0.08

# outcome key → what it hands over. `coins` is a (lo, hi) range rolled
# uniformly and MAY be negative (that is how an option charges for itself);
# `item` is a (catalogue key, qty) pair. Either may be absent.
ENCOUNTER_OUTCOMES: dict[str, dict] = {
    # /work — the late shift
    "overtime_paid": {
        "text": "The rush never comes, but the hours do. You clock a fat one.",
        "coins": (200, 450),
    },
    "overtime_quiet": {
        "text": "Dead quiet. You restock a shelf, wipe a counter, and go home.",
        "coins": (0, 40),
    },
    "overtime_declined": {
        "text": "You hand back the keys and take the small closing bonus.",
        "coins": (80, 140),
    },
    # /mine — the deep seam
    "seam_struck": {
        "text": "The seam opens into a pocket of gold-flecked rock.",
        "item": ("gold_ore", 2),
    },
    "seam_collapse": {
        "text": "The roof groans and comes down. You get out; the props don't.",
        "coins": (-160, -60),
    },
    "seam_shored": {
        "text": "You brace the tunnel properly and take what's safely reachable.",
        "item": ("iron_ore", 1),
    },
    # /hunt — the stag
    "stag_taken": {
        "text": "One shot, one trophy. You'll be telling this story for years.",
        "item": ("golden_antler", 1),
    },
    "stag_lost": {
        "text": "It's gone into the trees before you've finished raising your arm.",
    },
    "stag_tracked": {
        "text": "You follow it quietly and come out with a full pack instead.",
        "item": ("pelt", 2),
    },
    # /explore — the hooded trader
    "trader_chest": {
        "text": "The box is heavier than it looks — and it's a chest.",
        "coins": (-250, -250),
        "item": ("treasure_chest", 1),
    },
    "trader_key": {
        "text": "Inside the box: one key, and a note you can't read.",
        "coins": (-250, -250),
        "item": ("treasure_key", 1),
    },
    "trader_sand": {
        "text": "Inside the box: sand. The trader is already gone.",
        "coins": (-250, -250),
    },
    "trader_walked": {
        "text": "You keep your coin, and they point you at a shortcut home.",
        "coins": (150, 350),
    },
}

# Each option's outcome weights must sum to 1.0.
ENCOUNTERS: dict[str, dict] = {
    "work_overtime": {
        "activity": "work",
        "emoji": "🕗",
        "title": "The Late Shift",
        "prompt": "Your manager catches you at the door. *One more shift? "
        "Nobody else picked up the phone.*",
        "options": [
            {
                "key": "stay",
                "label": "Stay late",
                "emoji": "🕗",
                "outcomes": [("overtime_paid", 0.70), ("overtime_quiet", 0.30)],
            },
            {
                "key": "leave",
                "label": "Clock out",
                "emoji": "🚪",
                "outcomes": [("overtime_declined", 1.0)],
            },
        ],
    },
    "mine_deep_seam": {
        "activity": "mine",
        "emoji": "🕯️",
        "title": "The Deep Seam",
        "prompt": "Your lamp catches a seam running further into the dark than "
        "the props go.",
        "options": [
            {
                "key": "deeper",
                "label": "Follow it down",
                "emoji": "🕯️",
                "outcomes": [("seam_struck", 0.60), ("seam_collapse", 0.40)],
            },
            {
                "key": "shore",
                "label": "Shore it up",
                "emoji": "🪵",
                "outcomes": [("seam_shored", 1.0)],
            },
        ],
    },
    "hunt_stag": {
        "activity": "hunt",
        "emoji": "🦌",
        "title": "The Stag",
        "prompt": "A stag the size of a horse steps out of the treeline and "
        "looks straight at you.",
        "options": [
            {
                "key": "shoot",
                "label": "Take the shot",
                "emoji": "🏹",
                "outcomes": [("stag_taken", 0.40), ("stag_lost", 0.60)],
            },
            {
                "key": "track",
                "label": "Track it quietly",
                "emoji": "👣",
                "outcomes": [("stag_tracked", 0.75), ("stag_lost", 0.25)],
            },
        ],
    },
    "explore_trader": {
        "activity": "explore",
        "emoji": "🧙",
        "title": "The Hooded Trader",
        "prompt": "Someone is sitting on a milestone with a sealed box. "
        "*Two hundred and fifty, no questions, no refunds.*",
        "options": [
            {
                "key": "buy",
                "label": "Buy the box (250)",
                "emoji": "📦",
                "outcomes": [
                    ("trader_chest", 0.45),
                    ("trader_key", 0.30),
                    ("trader_sand", 0.25),
                ],
            },
            {
                "key": "walk",
                "label": "Walk away",
                "emoji": "🚶",
                "outcomes": [("trader_walked", 1.0)],
            },
        ],
    },
}

# How long the /adventure dashboard's buttons stay live. Long enough to burn a
# banked bucket and watch the next charge land, short enough that a stale card
# isn't sitting in a channel claiming an activity is ready when it isn't.
ADVENTURE_VIEW_TIMEOUT = 300

# How long an encounter's buttons stay live before the choice lapses. Nothing
# is charged or owed until a button is pressed, so an expiry costs the member
# only the bonus they never claimed — which is why the view is transient
# (no persistent custom_ids) unlike the /squad and /raid boards.
ENCOUNTER_TIMEOUT = 120

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
        "disabled": "Working is disabled on this server.",
        "wait_title": "💼 Not Yet",
        "wait": "You're still on shift. Try again in **{wait}**.",
    },
    "mine": {
        "emoji": "⛏️",
        "command": "/mine",
        "blurb": "Dig a vein of ore to sell. Small cave-in risk.",
        "disabled": "Mining is disabled on this server.",
        "wait_title": "⛏️ Not Yet",
        "wait": "Your pickaxe needs a rest. Dig again in **{wait}**.",
    },
    "hunt": {
        "emoji": "🏹",
        "command": "/adventure hunt",
        "blurb": "A bag of pelts, meat, rare trophy. Injury risk.",
        "disabled": "Hunting is disabled on this server.",
        "wait_title": "🏹 Not Yet",
        "wait": "You're still resting up. Hunt again in **{wait}**.",
    },
    "explore": {
        "emoji": "🧭",
        "command": "/adventure explore",
        "blurb": "Long shot: usually nothing, sometimes huge.",
        "disabled": "Exploring is disabled on this server.",
        "wait_title": "🧭 Not Yet",
        "wait": "You're still recovering from the last trip. Explore again in "
        "**{wait}**.",
    },
    "rob": {
        "emoji": "🥷",
        "command": "/rob",
        "blurb": "Steal from a member. Fail and you're fined.",
        "disabled": "Robbing is disabled on this server.",
        "wait_title": "🥷 Not Yet",
        "wait": "Lying low. Try again in **{wait}**.",
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
# The lengths are measured against the default rewards rather than the
# activities' own rate: /squad pays 50 per member, so 30 minutes is 100/hour;
# /raid pays 100, so an hour is 100/hour. Both are owner-set amounts (`!econ`)
# that most bots leave at the default, and both need other people present, so
# they were left where they were when the solo loop was re-paced. Before this
# they
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
