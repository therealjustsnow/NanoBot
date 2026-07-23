"""cogs/progression/definitions.py — the achievement and weekly-objective
registries.

Pure data — no Discord, no DB. `stat` fields must be keys registered in
`cogs.progression.stats.STAT_PROVIDERS`; `tests/test_progression_helpers.py`
asserts that (plus unique keys and positive thresholds/targets) so a typo
here fails CI instead of silently never firing.

Reward dict vocabulary (all keys optional, freely combined):
  - {"coins": n}                 — credited via db.add_coins
  - {"item": "key", "qty": n}    — granted via db.add_item; `qty` defaults to
                                    1. The item key must exist in the shared
                                    utils.items catalogue — the cog looks it
                                    up at award time and skips the grant (with
                                    a log line) if some other feature's items
                                    module hasn't registered it (e.g. its cog
                                    isn't loaded), so this stays independent
                                    of load order.
  - {"title": "Display Name"}    — purely cosmetic: becomes selectable via
                                    /progress title once earned, no DB write
                                    of its own (derived from the earned-
                                    achievements table + this registry).
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AchievementDef:
    key: str  # stable id stored in achievements_earned — never rename once shipped
    name: str
    emoji: str
    description: str
    category: str  # display grouping in /progress achievements
    stat: str  # a cogs.progression.stats.STAT_PROVIDERS key
    threshold: float
    points: int
    reward: dict = field(default_factory=dict)


@dataclass(frozen=True)
class WeeklyObjectiveDef:
    key: str  # stable id stored as weekly_objectives.obj_key
    name: str
    emoji: str
    description: str
    stat: str  # a cogs.progression.stats.STAT_PROVIDERS key
    target: float
    coins: int
    points: int


CAT_FISHING = "🎣 Fishing"
CAT_WEALTH = "🪙 Wealth"
CAT_CASINO = "🎰 Casino"
CAT_ACTIVITIES = "⛏️ Activities"

# ══════════════════════════════════════════════════════════════════════════════
#  Achievements
# ══════════════════════════════════════════════════════════════════════════════
ACHIEVEMENTS: list[AchievementDef] = [
    # ── Fishing: caught ──────────────────────────────────────────────────────
    AchievementDef(
        "fish_caught_10",
        "Bait & Switch",
        "🐟",
        "Catch 10 fish.",
        CAT_FISHING,
        "fish_caught",
        10,
        10,
        {"coins": 100},
    ),
    AchievementDef(
        "fish_caught_100",
        "Reel Deal",
        "🎣",
        "Catch 100 fish.",
        CAT_FISHING,
        "fish_caught",
        100,
        25,
        {"coins": 500, "item": "bait_shrimp", "qty": 3},
    ),
    AchievementDef(
        "fish_caught_1000",
        "Master Angler",
        "🏆",
        "Catch 1,000 fish.",
        CAT_FISHING,
        "fish_caught",
        1000,
        75,
        {"coins": 2500, "title": "Master Angler"},
    ),
    # ── Fishing: earned ──────────────────────────────────────────────────────
    AchievementDef(
        "fish_earned_1k",
        "Small Fry Profits",
        "💵",
        "Earn 1,000 coins from fishing.",
        CAT_FISHING,
        "fish_earned",
        1_000,
        10,
        {"coins": 100},
    ),
    AchievementDef(
        "fish_earned_10k",
        "Fish Market",
        "💰",
        "Earn 10,000 coins from fishing.",
        CAT_FISHING,
        "fish_earned",
        10_000,
        30,
        {"coins": 750},
    ),
    AchievementDef(
        "fish_earned_100k",
        "Coin Fisher",
        "🐋",
        "Earn 100,000 coins from fishing.",
        CAT_FISHING,
        "fish_earned",
        100_000,
        80,
        {"coins": 3000, "title": "Coin Fisher"},
    ),
    # ── Fishing: best weight ─────────────────────────────────────────────────
    AchievementDef(
        "fish_weight_10",
        "Decent Haul",
        "⚖️",
        "Land a fish weighing 10 kg or more.",
        CAT_FISHING,
        "fish_best_weight",
        10,
        10,
        {"coins": 100},
    ),
    AchievementDef(
        "fish_weight_25",
        "Big Catch",
        "🦈",
        "Land a fish weighing 25 kg or more.",
        CAT_FISHING,
        "fish_best_weight",
        25,
        25,
        {"coins": 400},
    ),
    AchievementDef(
        "fish_weight_50",
        "Leviathan",
        "🐳",
        "Land a fish weighing 50 kg or more.",
        CAT_FISHING,
        "fish_best_weight",
        50,
        60,
        {"coins": 1500, "item": "bait_magnet", "qty": 1},
    ),
    # ── Fishing: login streak ────────────────────────────────────────────────
    AchievementDef(
        "fish_streak_3",
        "Getting Hooked",
        "🔥",
        "Reach a 3-day fishing streak.",
        CAT_FISHING,
        "fish_streak",
        3,
        10,
        {"coins": 150},
    ),
    AchievementDef(
        "fish_streak_7",
        "Weekly Regular",
        "📅",
        "Reach a 7-day fishing streak.",
        CAT_FISHING,
        "fish_streak",
        7,
        20,
        {"coins": 350},
    ),
    AchievementDef(
        "fish_streak_30",
        "Tide Walker",
        "🌊",
        "Reach a 30-day fishing streak.",
        CAT_FISHING,
        "fish_streak",
        30,
        60,
        {"coins": 2000, "item": "fish_xp_potion", "qty": 3},
    ),
    # ── Fishing: dex ─────────────────────────────────────────────────────────
    AchievementDef(
        "fish_dex_10",
        "Collector",
        "📖",
        "Catch 10 different species.",
        CAT_FISHING,
        "fish_dex_size",
        10,
        15,
        {"coins": 200},
    ),
    AchievementDef(
        "fish_dex_20",
        "Naturalist",
        "🔬",
        "Catch 20 different species.",
        CAT_FISHING,
        "fish_dex_size",
        20,
        35,
        {"coins": 600},
    ),
    AchievementDef(
        "fish_dex_30",
        "Complete Dex",
        "📚",
        "Catch every species in the catalogue.",
        CAT_FISHING,
        "fish_dex_size",
        30,
        70,
        {"coins": 2000, "title": "Naturalist"},
    ),
    # ── Wealth: balance ──────────────────────────────────────────────────────
    AchievementDef(
        "balance_1k",
        "Piggy Bank",
        "🐷",
        "Hold 1,000 coins at once.",
        CAT_WEALTH,
        "balance",
        1_000,
        10,
        {"coins": 50},
    ),
    AchievementDef(
        "balance_10k",
        "Comfortable",
        "💼",
        "Hold 10,000 coins at once.",
        CAT_WEALTH,
        "balance",
        10_000,
        30,
        {"coins": 250},
    ),
    AchievementDef(
        "balance_100k",
        "Tycoon",
        "🏦",
        "Hold 100,000 coins at once.",
        CAT_WEALTH,
        "balance",
        100_000,
        80,
        {"coins": 1000, "title": "Tycoon"},
    ),
    # ── Wealth: contribution ─────────────────────────────────────────────────
    AchievementDef(
        "contrib_500",
        "Team Player",
        "🤝",
        "Earn 500 lifetime contribution points.",
        CAT_WEALTH,
        "contribution",
        500,
        15,
        {"coins": 200},
    ),
    AchievementDef(
        "contrib_5000",
        "Pillar of the Guild",
        "🏛️",
        "Earn 5,000 lifetime contribution points.",
        CAT_WEALTH,
        "contribution",
        5_000,
        50,
        {"coins": 1500, "title": "Pillar of the Guild"},
    ),
    # ── Casino: games ────────────────────────────────────────────────────────
    AchievementDef(
        "casino_games_10",
        "First Bets",
        "🎲",
        "Play 10 casino games.",
        CAT_CASINO,
        "casino_games",
        10,
        10,
        {"coins": 100},
    ),
    AchievementDef(
        "casino_games_100",
        "Regular",
        "🃏",
        "Play 100 casino games.",
        CAT_CASINO,
        "casino_games",
        100,
        30,
        {"coins": 500},
    ),
    AchievementDef(
        "casino_games_1000",
        "High Roller",
        "🎩",
        "Play 1,000 casino games.",
        CAT_CASINO,
        "casino_games",
        1000,
        75,
        {"coins": 2500, "title": "High Roller"},
    ),
    # ── Casino: biggest win ──────────────────────────────────────────────────
    AchievementDef(
        "casino_win_500",
        "Nice Hand",
        "♠️",
        "Win 500 coins in a single casino game.",
        CAT_CASINO,
        "casino_biggest_win",
        500,
        20,
        {"coins": 150},
    ),
    AchievementDef(
        "casino_win_5000",
        "Jackpot Chaser",
        "🎰",
        "Win 5,000 coins in a single casino game.",
        CAT_CASINO,
        "casino_biggest_win",
        5_000,
        60,
        {"coins": 1000},
    ),
    # ── Casino: best streak ──────────────────────────────────────────────────
    AchievementDef(
        "casino_streak_5",
        "Hot Streak",
        "🔥",
        "Win 5 casino games in a row.",
        CAT_CASINO,
        "casino_best_streak",
        5,
        15,
        {"coins": 200},
    ),
    AchievementDef(
        "casino_streak_10",
        "Unstoppable",
        "⚡",
        "Win 10 casino games in a row.",
        CAT_CASINO,
        "casino_best_streak",
        10,
        40,
        {"coins": 750, "title": "Unstoppable"},
    ),
    # ── Activities: work ─────────────────────────────────────────────────────
    AchievementDef(
        "work_10",
        "Employee of the Month",
        "🧑‍💼",
        "Complete 10 work shifts.",
        CAT_ACTIVITIES,
        "work_shifts",
        10,
        10,
        {"coins": 100},
    ),
    AchievementDef(
        "work_100",
        "Career Ladder",
        "📈",
        "Complete 100 work shifts.",
        CAT_ACTIVITIES,
        "work_shifts",
        100,
        30,
        {"coins": 600},
    ),
    AchievementDef(
        "work_500",
        "Workaholic",
        "🏢",
        "Complete 500 work shifts.",
        CAT_ACTIVITIES,
        "work_shifts",
        500,
        70,
        {"coins": 2500, "title": "Workaholic"},
    ),
    # ── Activities: mining ───────────────────────────────────────────────────
    AchievementDef(
        "mine_25",
        "Rock Breaker",
        "⛏️",
        "Complete 25 mining trips.",
        CAT_ACTIVITIES,
        "mine_count",
        25,
        15,
        {"coins": 250},
    ),
    AchievementDef(
        "mine_250",
        "Deep Delver",
        "🪨",
        "Complete 250 mining trips.",
        CAT_ACTIVITIES,
        "mine_count",
        250,
        50,
        {"coins": 1200, "item": "diamond", "qty": 1},
    ),
    # ── Activities: hunting ──────────────────────────────────────────────────
    AchievementDef(
        "hunt_25",
        "Tracker",
        "🏹",
        "Complete 25 hunts.",
        CAT_ACTIVITIES,
        "hunt_count",
        25,
        15,
        {"coins": 250},
    ),
    AchievementDef(
        "hunt_250",
        "Apex Predator",
        "🐺",
        "Complete 250 hunts.",
        CAT_ACTIVITIES,
        "hunt_count",
        250,
        50,
        {"coins": 1200, "item": "padlock", "qty": 2},
    ),
    # ── Activities: exploring ────────────────────────────────────────────────
    AchievementDef(
        "explore_10",
        "Wanderer",
        "🧭",
        "Complete 10 explorations.",
        CAT_ACTIVITIES,
        "explore_count",
        10,
        15,
        {"coins": 300},
    ),
    AchievementDef(
        "explore_100",
        "Globetrotter",
        "🗺️",
        "Complete 100 explorations.",
        CAT_ACTIVITIES,
        "explore_count",
        100,
        50,
        {"coins": 1500, "title": "Globetrotter"},
    ),
    # ── Activities: robbing ──────────────────────────────────────────────────
    AchievementDef(
        "rob_5",
        "Sticky Fingers",
        "🖐️",
        "Successfully rob 5 members.",
        CAT_ACTIVITIES,
        "rob_count",
        5,
        15,
        {"coins": 200},
    ),
    AchievementDef(
        "rob_25",
        "Cat Burglar",
        "🐈‍⬛",
        "Successfully rob 25 members.",
        CAT_ACTIVITIES,
        "rob_count",
        25,
        50,
        {"coins": 1000, "title": "Cat Burglar"},
    ),
    # ── Activities: pickaxe tier ─────────────────────────────────────────────
    AchievementDef(
        "pickaxe_3",
        "Iron-Shod",
        "🔩",
        "Upgrade to pickaxe tier 3.",
        CAT_ACTIVITIES,
        "pickaxe_level",
        3,
        20,
        {"coins": 300},
    ),
    AchievementDef(
        "pickaxe_5",
        "Obsidian Edge",
        "🖤",
        "Upgrade to the best pickaxe.",
        CAT_ACTIVITIES,
        "pickaxe_level",
        5,
        60,
        {"coins": 1500, "title": "Obsidian Edge"},
    ),
]

# key -> AchievementDef, for O(1) lookups by cog/tests.
ACHIEVEMENTS_BY_KEY: dict[str, AchievementDef] = {a.key: a for a in ACHIEVEMENTS}


# ══════════════════════════════════════════════════════════════════════════════
#  Weekly objective pool — 3 are drawn deterministically per (guild, user, week)
# ══════════════════════════════════════════════════════════════════════════════
WEEKLY_POOL: list[WeeklyObjectiveDef] = [
    WeeklyObjectiveDef(
        "weekly_fish_catch",
        "Weekly Haul",
        "🎣",
        "Catch 20 fish this week.",
        "fish_caught",
        20,
        150,
        10,
    ),
    WeeklyObjectiveDef(
        "weekly_fish_earn",
        "Fish Market Run",
        "💵",
        "Earn 1,500 coins fishing this week.",
        "fish_earned",
        1_500,
        150,
        10,
    ),
    WeeklyObjectiveDef(
        "weekly_fish_dex",
        "New Discoveries",
        "📖",
        "Catch 2 new species this week.",
        "fish_dex_size",
        2,
        150,
        10,
    ),
    WeeklyObjectiveDef(
        "weekly_casino_games",
        "Table Time",
        "🎲",
        "Play 10 casino games this week.",
        "casino_games",
        10,
        150,
        10,
    ),
    WeeklyObjectiveDef(
        "weekly_work",
        "Punch the Clock",
        "🧑‍💼",
        "Complete 5 work shifts this week.",
        "work_shifts",
        5,
        150,
        10,
    ),
    WeeklyObjectiveDef(
        "weekly_mine",
        "Ore Quota",
        "⛏️",
        "Complete 5 mining trips this week.",
        "mine_count",
        5,
        150,
        10,
    ),
    WeeklyObjectiveDef(
        "weekly_hunt",
        "Weekly Hunt",
        "🏹",
        "Complete 5 hunts this week.",
        "hunt_count",
        5,
        150,
        10,
    ),
    WeeklyObjectiveDef(
        "weekly_explore",
        "Scouting Trip",
        "🧭",
        "Complete 3 explorations this week.",
        "explore_count",
        3,
        200,
        15,
    ),
    WeeklyObjectiveDef(
        "weekly_rob",
        "Opportunist",
        "🖐️",
        "Successfully rob 2 members this week.",
        "rob_count",
        2,
        200,
        15,
    ),
    WeeklyObjectiveDef(
        "weekly_contribution",
        "Team Effort",
        "🤝",
        "Earn 200 contribution points this week.",
        "contribution",
        200,
        150,
        10,
    ),
    WeeklyObjectiveDef(
        "weekly_items",
        "Stockpile",
        "📦",
        "Collect 10 items this week.",
        "items_owned",
        10,
        150,
        10,
    ),
    WeeklyObjectiveDef(
        "weekly_casino_wagered",
        "Big Spender",
        "🃏",
        "Wager 1,000 coins at the casino this week.",
        "casino_wagered",
        1_000,
        150,
        10,
    ),
]

WEEKLY_POOL_BY_KEY: dict[str, WeeklyObjectiveDef] = {w.key: w for w in WEEKLY_POOL}
