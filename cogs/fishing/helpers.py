"""Pure fishing helpers (no Discord deps) — covered by tests/test_fishing_helpers.py.

Every random decision takes an explicit roll in [0, 1) so the cog owns the
randomness and tests stay deterministic (the resolve_gamble pattern).
"""

import random

from utils.helpers import weighted_pick

from .constants import (
    EVENT_POOL,
    FISH,
    FISH_BY_RARITY,
    LEVEL_LUCK_CAP,
    LEVEL_LUCK_PER_LEVEL,
    LEVEL_XP_BASE,
    QUEST_CATCH_ANY_COIN_MULT,
    QUEST_CATCH_ANY_TARGET,
    QUEST_CATCH_ANY_XP_MULT,
    QUEST_CATCH_RARITY_COIN_MULT,
    QUEST_CATCH_RARITY_POOL,
    QUEST_CATCH_RARITY_TARGET,
    QUEST_CATCH_RARITY_XP_MULT,
    QUEST_EARN_COINS_COIN_FRACTION,
    QUEST_EARN_COINS_TARGET,
    QUEST_EARN_COINS_XP_FRACTION,
    RARITY_ODDS,
    RODS,
    STREAK_COIN_CAP,
    STREAK_COIN_PER_DAY,
)

# Rarities that luck shifts mass *into*, in table order.
_HIGH_TIERS = ("uncommon", "rare", "epic", "legendary", "treasure")


def rarity_odds(luck: float = 0.0) -> list[tuple[str, float]]:
    """Effective rarity odds for a given rod luck (clamped to [0, 1]).

    Luck removes `luck` of the junk mass and half that fraction of the common
    mass, then scales the higher tiers up proportionally so the odds still sum
    to 1. At luck 0 this returns RARITY_ODDS unchanged.
    """
    luck = max(0.0, min(1.0, luck))
    base = dict(RARITY_ODDS)
    junk = base["junk"] * (1 - luck)
    common = base["common"] * (1 - 0.5 * luck)
    freed = (base["junk"] - junk) + (base["common"] - common)
    high_sum = sum(base[r] for r in _HIGH_TIERS)
    scale = (high_sum + freed) / high_sum
    out = []
    for rarity, p in RARITY_ODDS:
        if rarity == "junk":
            out.append((rarity, junk))
        elif rarity == "common":
            out.append((rarity, common))
        else:
            out.append((rarity, p * scale))
    return out


def pick_rarity(roll: float, luck: float = 0.0) -> str:
    """Map a roll in [0, 1) onto the effective rarity table."""
    return weighted_pick(rarity_odds(luck), roll)


def pick_fish(rarity: str, roll: float) -> str:
    """Pick a catalogue key within a rarity tier from a roll in [0, 1)."""
    keys = FISH_BY_RARITY[rarity]
    return keys[min(int(roll * len(keys)), len(keys) - 1)]


def roll_weight(fish_key: str, roll: float) -> float:
    """Roll a weight (kg) inside the species' range, rounded to 2 dp."""
    lo, hi = FISH[fish_key]["weight"]
    return round(lo + roll * (hi - lo), 2)


def catch_value(fish_key: str, weight: float) -> int:
    """Coin value of a bagged catch.

    Junk is worth its flat base value. Fish scale 0.6x–1.4x of base by where
    the weight landed in the species' range (a trophy specimen pays more).
    """
    fish = FISH[fish_key]
    if fish["rarity"] == "junk":
        return fish["value"]
    lo, hi = fish["weight"]
    frac = 0.0 if hi <= lo else (weight - lo) / (hi - lo)
    frac = max(0.0, min(1.0, frac))
    return max(1, round(fish["value"] * (0.6 + 0.8 * frac)))


def treasure_coins(fish_key: str, roll: float) -> int:
    """Immediate coin payout for a treasure pull: 0.5x–1.5x of base value."""
    return max(1, round(FISH[fish_key]["value"] * (0.5 + roll)))


def rod_info(level: int) -> dict:
    """The rod at a stored level, clamped into the ladder."""
    return RODS[max(0, min(level, len(RODS) - 1))]


def next_rod(level: int) -> dict | None:
    """The next rod tier up, or None when already at the top."""
    if level >= len(RODS) - 1:
        return None
    return RODS[max(0, level) + 1]


def fmt_weight(kg: float) -> str:
    """Human weight: grams under 1 kg, else kg with two decimals."""
    if kg < 1:
        return f"{kg * 1000:.0f} g"
    return f"{kg:,.2f} kg"


def find_fish(query: str) -> str | None:
    """Resolve user input to a catalogue key (key or name, case-insensitive)."""
    q = query.strip().lower()
    if q in FISH:
        return q
    for key, fish in FISH.items():
        if fish["name"].lower() == q:
            return key
    return None


# ── Fishing XP & levels ─────────────────────────────────────────────────────────
def cum_xp_for_level(level: int) -> int:
    """Cumulative XP required to reach `level` (level 0 = 0 XP)."""
    level = max(0, level)
    return LEVEL_XP_BASE * level * level


def fish_level(xp: int) -> int:
    """The level a cumulative XP total puts you at (monotonic, floors at 0)."""
    xp = max(0, xp)
    level = int((xp / LEVEL_XP_BASE) ** 0.5)
    while cum_xp_for_level(level + 1) <= xp:
        level += 1
    while level > 0 and cum_xp_for_level(level) > xp:
        level -= 1
    return level


def level_progress(xp: int) -> tuple[int, int, int]:
    """(level, xp earned into the level, xp needed for the next level)."""
    level = fish_level(xp)
    floor = cum_xp_for_level(level)
    ceiling = cum_xp_for_level(level + 1)
    return level, xp - floor, ceiling - floor


def level_luck_bonus(level: int) -> float:
    """Luck bonus from fishing level alone, capped."""
    return min(LEVEL_LUCK_CAP, max(0, level) * LEVEL_LUCK_PER_LEVEL)


# ── Daily streak ────────────────────────────────────────────────────────────────
def next_streak(last_day: int, today: int, streak: int) -> int:
    """Consecutive-day streak after a cast on `today` (epoch day numbers)."""
    if last_day == today - 1:
        return streak + 1
    return 1


def streak_bonus_coins(streak: int) -> int:
    """Coin bonus for hitting a streak day, capped."""
    return min(STREAK_COIN_CAP, max(0, streak) * STREAK_COIN_PER_DAY)


# ── Daily quest ──────────────────────────────────────────────────────────────────
def quest_reward(quest_key: str, target: int) -> tuple[int, int]:
    """(coins, xp) reward for completing a quest."""
    if quest_key == "catch_any":
        return target * QUEST_CATCH_ANY_COIN_MULT, target * QUEST_CATCH_ANY_XP_MULT
    if quest_key.startswith("catch_rarity:"):
        rarity = quest_key.split(":", 1)[1]
        coin_mult = QUEST_CATCH_RARITY_COIN_MULT.get(rarity, 20)
        xp_mult = QUEST_CATCH_RARITY_XP_MULT.get(rarity, 10)
        return target * coin_mult, target * xp_mult
    if quest_key == "earn_coins":
        return (
            round(target * QUEST_EARN_COINS_COIN_FRACTION),
            round(target * QUEST_EARN_COINS_XP_FRACTION),
        )
    return 0, 0


def quest_label(quest_key: str, target: int) -> str:
    """Human-readable description of a quest."""
    if quest_key == "catch_any":
        return f"Catch {target} fish"
    if quest_key.startswith("catch_rarity:"):
        rarity = quest_key.split(":", 1)[1]
        return f"Catch {target} {rarity} fish"
    if quest_key == "earn_coins":
        return f"Earn {target} coins fishing"
    return "Unknown quest"


def generate_quest(guild_id: int, user_id: int, day: int) -> dict:
    """Deterministically pick the day's quest for a member.

    Seeded on (guild_id, user_id, day), so the same trio always yields the same
    quest — callers regenerate it on demand instead of persisting the pick.
    """
    rng = random.Random(f"{int(guild_id)}:{int(user_id)}:{int(day)}")
    kind = rng.choice(["catch_any", "catch_rarity", "earn_coins"])
    if kind == "catch_any":
        lo, hi = QUEST_CATCH_ANY_TARGET
        target = rng.randint(lo, hi)
        quest_key = "catch_any"
    elif kind == "catch_rarity":
        rarity = rng.choice(QUEST_CATCH_RARITY_POOL)
        lo, hi = QUEST_CATCH_RARITY_TARGET[rarity]
        target = rng.randint(lo, hi)
        quest_key = f"catch_rarity:{rarity}"
    else:
        lo, hi = QUEST_EARN_COINS_TARGET
        target = rng.randint(lo, hi)
        quest_key = "earn_coins"
    coins, xp = quest_reward(quest_key, target)
    return {
        "quest_key": quest_key,
        "target": target,
        "label": quest_label(quest_key, target),
        "reward_coins": coins,
        "reward_xp": xp,
    }


# ── Guild fishing events ─────────────────────────────────────────────────────────
def pick_event(roll: float) -> dict:
    """Pick a weighted event definition from EVENT_POOL by a roll in [0, 1)."""
    total = sum(e["weight"] for e in EVENT_POOL)
    acc = 0.0
    for event in EVENT_POOL:
        acc += event["weight"] / total
        if roll < acc:
            return event
    return EVENT_POOL[-1]


def roll_event_duration(roll: float, duration_range: tuple[int, int]) -> int:
    """Roll an event duration (seconds) inside a (min, max) range."""
    lo, hi = duration_range
    return int(lo + roll * (hi - lo))
