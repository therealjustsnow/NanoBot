"""Activities cog constants: career ladder, catalogues, odds tables, tool
ladders, encounters, and the interval defaults/bounds.

Who this is paced for
─────────────────────
A player who opens Discord, runs a handful of commands, and goes back to their
life — twice a day, not once an hour. That sentence is the whole balance model,
and it is worth stating because the previous two passes both quietly assumed
somebody who was always there.

The first pass priced everything per *hour of claiming*, which nobody does. The
second added charges, which helped, but sized the caps at two hours — so being
away for two hours and being away for a whole day paid exactly the same, and a
member checking in twice a day collected about a tenth of what the loop was
nominally generating. The theoretical rate was a number no real person ever saw.

The fix is to decouple two things that had been conflated:

  the **interval** decides what an engaged player finds waiting when they check
  in, so it wants to be short enough that there is usually something there;
  the **cap** decides what a casual player *loses* by not being there, so it
  wants to cover the gap between real visits — half a day.

Sized that way, a member who shows up twice a day collects essentially 100% of
what the clock generated, and someone who checks in hourly collects the same
total in smaller pieces. Playing more stops being how you earn and becomes how
you spend your time; the reward for it is fishing, which is the one faucet that
genuinely pays for attention.

Balance (every activity claimed, before encounters and the daily streak):
  /work    3h x4 charges, pay 200-360 (+career bonus up to 140) → ~300/run
  /mine    3h x4 charges, ore EV ~31 x a ~6.9 vein, 8% cave-in  → ~200/run
  /hunt    4h x3 charges, catch EV ~48 x a ~6 bag, net fine     → ~280/run
  /explore 6h x2 charges, coin EV ~460 (rest is non-coin items) → ~450/run
  /rob     4h x1 charge,  steal capped 1000/attempt             → zero-sum
           wealth *transfer* (not newly minted coins) plus a fine that's a
           pure sink, so the income guardrail doesn't apply the same way — it
           never inflates the total coin supply.

Those are the luck-0 figures — a member with nothing, which is what every price
in the bot is quoted against. Mining is the one line a purchase moves: a fully
upgraded pickaxe takes a dig from ~200 to ~544 (see PICKAXES, which is priced
against that gain rather than against the rod ladder it used to copy).

That is ~7,500 coins a day over 26 runs, of which two visits collect all of it
in 13 taps — or one, with Collect all. Against a member who also fishes for a
solid hour, the gap is about 1.5x rather than the 3.5x it used to be.
`helpers.adventure_coins_per_hour` publishes the rate and
`helpers.adventure_coins_per_day` the figure that actually matters;
tests/test_economy_balance.py recomputes both from these tables, asserts the
casual/grinder gap, and turns every ladder price into *days of ordinary play*
— so a change that only works for someone playing constantly fails CI.

Two multipliers ride on top and are deliberately excluded from the figures
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

WORK_PAY_MIN = 200
WORK_PAY_MAX = 360

WORK_COOLDOWN_DEFAULT = 10_800  # 3 hours
WORK_COOLDOWN_MAX = 86_400

# Lifetime shift count → (title, flat pay bonus). The highest threshold a
# member's shift count meets or exceeds wins (see helpers.career_info).
#
# The bonuses scale with the base pay, so a promotion is always worth the same
# *share* of a paycheck. The thresholds don't move with the interval: at four
# shifts a day the top of the ladder is a genuinely long haul, which is what a
# lifetime title should be.
CAREER_LADDER: list[tuple[int, str, int]] = [
    (0, "🍵 Intern", 0),
    (10, "📋 Junior Associate", 16),
    (25, "🗂️ Associate", 31),
    (50, "📊 Senior Associate", 47),
    (100, "🧑‍💼 Manager", 62),
    (200, "📈 Senior Manager", 78),
    (400, "🏢 Director", 93),
    (750, "🎩 Vice President", 109),
    (1500, "👑 Executive", 124),
    (3000, "🏆 Legend of the Office", 140),
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

MINE_COOLDOWN_DEFAULT = 10_800  # 3 hours
MINE_COOLDOWN_MAX = 86_400

# 8% of digs yield nothing at all (a cave-in), independent of ore rarity.
MINE_CAVE_IN_CHANCE = 0.08

# A small independent chance of a bonus treasure_key on top of the ore roll.
#
# Sized against the chest rate below. A key is worthless on its own — it only
# ever unlocks a treasure_chest, and /explore is the sole chest source — so the
# two supplies have to track each other, which is asserted in
# tests/test_activities_helpers.py from the live intervals rather than from
# these percentages (mining claims twice as often as exploring, and comparing
# the raw chances hides that). The roll is per *dig*, not per ore, so the vein
# table below doesn't multiply it.
MINE_TREASURE_KEY_CHANCE = 0.05

# How much ore one successful dig yields *with bare hands*. At a three-hour
# interval a dig is a session's worth of mining rather than a swing of a
# pickaxe, so it comes back with a seam: a spread rather than a fixed number, so
# the same tap has something to hope for. Must sum to 1.0. EV ≈ 6.85 ore.
#
# A pickaxe's `vein` bonus is added on top of whatever this rolls (see PICKAXES
# and helpers.roll_vein), so the table itself is the luck-0 floor every balance
# figure is quoted against.
MINE_VEIN_ODDS: list[tuple[int, float]] = [
    (4, 0.35),
    (6, 0.30),
    (9, 0.25),
    (14, 0.10),
]

ORES: dict[str, dict] = {
    "stone": {"name": "Stone", "emoji": "🪨", "value": 8},
    "coal": {"name": "Coal", "emoji": "⚫", "value": 18},
    "iron_ore": {"name": "Iron Ore", "emoji": "⚙️", "value": 38},
    "gold_ore": {"name": "Gold Ore", "emoji": "🟡", "value": 90},
    "diamond": {"name": "Diamond", "emoji": "💎", "value": 300},
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

# Pickaxe ladder, indexed by stored pickaxe_level. A deliberate coin sink.
# `luck` (0..1) feeds helpers.mine_odds; `vein` is extra ore added to every
# successful dig's roll (helpers.roll_vein).
#
# Why this is NOT the rod ladder's price curve
# ────────────────────────────────────────────
# It used to be, copied over on the reasoning that "coins are fungible and
# fishing is what pays for these". That confuses being able to *afford*
# something with it being worth *buying*, and the two ladders sell returns of
# completely different shape:
#
#   a rod raises the value of a *cast*, and a player chooses how many to make —
#   ~180 an hour. Rod luck compounds against the one faucet that pays for
#   attention, so a rod's return scales with the price you paid for it.
#
#   a pickaxe raises the value of a *dig*, and nobody chooses how many to make:
#   the interval does, at 8 a day. So a pickaxe's return is bounded by mining's
#   throughput no matter what it costs.
#
# On the old numbers that bound was brutal. `mine_odds` shifts ore mass linearly
# in luck, so every tier bought exactly the same +36 coins/dig — +286 a day,
# flat — while the price went up 5-6x a rung. Payback ran 2.6 days, then 14,
# then 84, then 350: the top two pickaxes could not repay themselves inside a
# year of mining. tests/test_economy_balance.py asserts every fishing charter
# pays itself back and there was no equivalent guard here, which is exactly how
# it drifted that far.
#
# The fix is both halves, because repricing alone produces a degenerate ladder —
# priced honestly against a flat +286/day the whole thing tops out around 7,000
# coins, under a day of ordinary play, and mining has nothing left to spend on:
#
#   1. the upper tiers now break more rock (`vein`), so the return *multiplies*
#      the ore value instead of adding a constant to it — the same compounding a
#      rod gets from casts, taken from the one lever mining has;
#   2. prices are then set from that return, at 3 / 5 / 11 / 21 days of ordinary
#      mining to pay themselves back — ascending, so each rung is a longer
#      commitment than the last, and finite, so each one is a real purchase.
#
# The ladder ends up much cheaper in absolute coins than the rods', and that is
# the honest answer rather than a concession: a sink can only be as large as the
# thing it improves. Fully kitted, a dig is worth 2.7x bare hands — deliberately
# just under the 3x bound the fishing spots hold to, for the same reason (past
# that, the starting state stops being somewhere anyone would play). If mining
# should carry a bigger sink than this, raise its ceiling, not its prices.
PICKAXES: list[dict] = [
    {"name": "Bare Hands", "emoji": "✊", "price": 0, "luck": 0.0, "vein": 0},
    {"name": "Stone Pickaxe", "emoji": "⛏️", "price": 750, "luck": 0.15, "vein": 0},
    {"name": "Iron Pickaxe", "emoji": "⛏️", "price": 3_000, "luck": 0.30, "vein": 1},
    {"name": "Steel Pickaxe", "emoji": "⛏️", "price": 7_500, "luck": 0.45, "vein": 2},
    {
        "name": "Obsidian Pickaxe",
        "emoji": "⛏️",
        "price": 24_000,
        "luck": 0.60,
        "vein": 4,
    },
]

# ══════════════════════════════════════════════════════════════════════════════
#  /hunt — medium-risk foraging with an injury chance
# ══════════════════════════════════════════════════════════════════════════════

HUNT_COOLDOWN_DEFAULT = 14_400  # 4 hours
HUNT_COOLDOWN_MAX = 86_400

HUNT_CATCHES: dict[str, dict] = {
    "pelt": {"name": "Animal Pelt", "emoji": "🦫", "value": 30},
    "meat": {"name": "Wild Meat", "emoji": "🥩", "value": 22},
    "golden_antler": {"name": "Golden Antler", "emoji": "🏆", "value": 450},
}

# Must sum to 1.0.
HUNT_ODDS: list[tuple[str, float]] = [
    ("pelt", 0.57),
    ("meat", 0.38),
    ("golden_antler", 0.05),
]

HUNT_INJURY_CHANCE = 0.12
HUNT_INJURY_FINE_MAX = 120  # coin fine rolled in [0, this], never below 0
HUNT_PADLOCK_CHANCE = 0.06  # independent chance of finding a defensive padlock

# How much a single hunt brings back — mining's vein table, for the same
# reason. Must sum to 1.0. EV ≈ 1.75 catches per hunt.
HUNT_BAG_ODDS: list[tuple[int, float]] = [
    (4, 0.40),
    (6, 0.35),
    (9, 0.25),
]

# ══════════════════════════════════════════════════════════════════════════════
#  /explore — long shot, high variance
# ══════════════════════════════════════════════════════════════════════════════

EXPLORE_COOLDOWN_DEFAULT = 21_600  # 6 hours
EXPLORE_COOLDOWN_MAX = 172_800

# Explore is the loop's lottery ticket, so its purses carry the variance the
# other four deliberately don't. The gap between the two matters more than
# either number: a big find has to feel like a different event, not a good roll.
# On a six-hour interval you get three or four of these a day, so a big one
# landing is a story rather than a Tuesday.
EXPLORE_COINS_SMALL = (450, 1100)
EXPLORE_COINS_BIG = (2400, 5200)

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
#  Charges — how many runs an activity banks while you're away
# ══════════════════════════════════════════════════════════════════════════════
#
# See utils.db.activities.try_claim_activity for the mechanism. These caps
# change the *shape* of a session, never its rate: three banked shifts pay
# exactly what three shifts an hour apart would have.
#
# Every cap is half a day of that activity's interval, which is the number that
# matters: it is what a member loses by turning up twice a day instead of
# constantly, and at twelve hours the answer is nothing. Raising an interval
# without raising its cap would quietly reintroduce the problem this whole
# rebalance exists to fix, so tests/test_activities_helpers.py checks the two
# against each other.
#
# /rob is the one activity that deliberately banks nothing — a stored-up run of
# robberies is a different (and much less welcome) experience for the person on
# the receiving end, and the cooldown there is a protection, not a pacer.
ACTIVITY_MAX_CHARGES: dict[str, int] = {
    "work": 4,
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
