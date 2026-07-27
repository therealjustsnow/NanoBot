"""Pure activities helpers (no Discord deps) — covered by
tests/test_activities_helpers.py.

Every random decision takes an explicit roll in [0, 1) so the cog owns the
randomness and tests stay deterministic (the resolve_gamble pattern used
throughout the economy/fishing cogs).
"""

from utils.helpers import weighted_pick

from .constants import (
    ACTIVITY_DEFAULT_COOLDOWNS,
    CAREER_LADDER,
    COOLDOWN_MIN,
    EXPLORE_OUTCOMES,
    HUNT_INJURY_CHANCE,
    HUNT_INJURY_FINE_MAX,
    HUNT_ODDS,
    HUNT_PADLOCK_CHANCE,
    MINE_CAVE_IN_CHANCE,
    MINE_TREASURE_KEY_CHANCE,
    ORE_ODDS,
    PICKAXES,
    ROB_BASE_SUCCESS,
    ROB_LUCK_BONUS,
    ROB_STEAL_CAP,
    ROB_STEAL_MAX_PCT,
    ROB_STEAL_MIN_PCT,
    ROB_SUCCESS_CAP,
    WORK_PAY_MAX,
    WORK_PAY_MIN,
    WORK_SCENES,
    _MINE_HIGH_TIERS,
)


# ══════════════════════════════════════════════════════════════════════════════
#  Cooldowns
# ══════════════════════════════════════════════════════════════════════════════
def effective_cooldown(activity: str, configured) -> int:
    """The cooldown actually enforced for an activity.

    `configured` is the bot owner's override for this activity (see
    utils/db/settings.py) or None when there isn't one. Cooldown claims are
    global, so this length is bot-wide too — no server gets to shorten it. See
    "Cross-server farming" in constants.py.

    An unset, unknown, or nonsense value falls back to the activity's default
    rather than to zero, and anything below COOLDOWN_MIN is lifted to it: a
    cooldown that silently becomes "no cooldown" is the one failure mode this
    must never have.
    """
    default = ACTIVITY_DEFAULT_COOLDOWNS.get(activity, 3600)
    if configured is None:
        return default
    try:
        value = int(configured)
    except (TypeError, ValueError):
        return default
    return max(value, COOLDOWN_MIN)


# ══════════════════════════════════════════════════════════════════════════════
#  /work
# ══════════════════════════════════════════════════════════════════════════════
def career_info(shifts: int) -> dict:
    """The career tier a lifetime shift count has reached.

    Returns {"tier": index, "title": str, "bonus": int}. The highest ladder
    threshold the shift count meets or exceeds wins.
    """
    tier = 0
    title, bonus = CAREER_LADDER[0][1], CAREER_LADDER[0][2]
    for i, (threshold, t, b) in enumerate(CAREER_LADDER):
        if shifts >= threshold:
            tier, title, bonus = i, t, b
    return {"tier": tier, "title": title, "bonus": bonus}


def next_career(shifts: int) -> dict | None:
    """The next career tier up, or None when already at the top."""
    info = career_info(shifts)
    if info["tier"] >= len(CAREER_LADDER) - 1:
        return None
    threshold, title, bonus = CAREER_LADDER[info["tier"] + 1]
    return {
        "tier": info["tier"] + 1,
        "title": title,
        "bonus": bonus,
        "shifts": threshold,
    }


def roll_work_pay(roll: float, bonus: int = 0) -> int:
    """Pay for one shift: a base roll in [WORK_PAY_MIN, WORK_PAY_MAX] plus the
    flat career bonus."""
    base = WORK_PAY_MIN + roll * (WORK_PAY_MAX - WORK_PAY_MIN)
    return max(1, round(base) + bonus)


def pick_work_scene(roll: float) -> str:
    """Pick a flavor scene from a roll in [0, 1)."""
    return WORK_SCENES[min(int(roll * len(WORK_SCENES)), len(WORK_SCENES) - 1)]


# ══════════════════════════════════════════════════════════════════════════════
#  /mine
# ══════════════════════════════════════════════════════════════════════════════
def mine_odds(luck: float = 0.0) -> list[tuple[str, float]]:
    """Effective ore odds for a given pickaxe luck (clamped to [0, 1]).

    Mirrors cogs.fishing.helpers.rarity_odds: luck removes `luck` of the stone
    mass and half that fraction of the coal mass, then scales iron/gold/diamond
    up proportionally so the odds still sum to 1. At luck 0 this returns
    ORE_ODDS unchanged.
    """
    luck = max(0.0, min(1.0, luck))
    base = dict(ORE_ODDS)
    stone = base["stone"] * (1 - luck)
    coal = base["coal"] * (1 - 0.5 * luck)
    freed = (base["stone"] - stone) + (base["coal"] - coal)
    high_sum = sum(base[r] for r in _MINE_HIGH_TIERS)
    scale = (high_sum + freed) / high_sum
    out = []
    for key, p in ORE_ODDS:
        if key == "stone":
            out.append((key, stone))
        elif key == "coal":
            out.append((key, coal))
        else:
            out.append((key, p * scale))
    return out


def roll_cave_in(roll: float, chance: float = MINE_CAVE_IN_CHANCE) -> bool:
    """Whether this dig is a cave-in (no yield) — a roll in [0, 1)."""
    return roll < chance


def pick_ore(rarity_roll: float, luck: float = 0.0) -> str:
    """Map a roll in [0, 1) onto the luck-adjusted ore table."""
    return weighted_pick(mine_odds(luck), rarity_roll)


def roll_mine_treasure_key(
    roll: float, chance: float = MINE_TREASURE_KEY_CHANCE
) -> bool:
    """Independent bonus treasure_key chance on top of the ore roll."""
    return roll < chance


def pickaxe_info(level: int) -> dict:
    """The pickaxe at a stored level, clamped into the ladder."""
    return PICKAXES[max(0, min(level, len(PICKAXES) - 1))]


def next_pickaxe(level: int) -> dict | None:
    """The next pickaxe tier up, or None when already at the top."""
    if level >= len(PICKAXES) - 1:
        return None
    return PICKAXES[max(0, level) + 1]


# ══════════════════════════════════════════════════════════════════════════════
#  /hunt
# ══════════════════════════════════════════════════════════════════════════════
def pick_hunt_catch(roll: float) -> str:
    """Map a roll in [0, 1) onto HUNT_ODDS."""
    return weighted_pick(HUNT_ODDS, roll)


def roll_hunt_injury(roll: float, chance: float = HUNT_INJURY_CHANCE) -> bool:
    return roll < chance


def hunt_injury_fine(roll: float, max_fine: int = HUNT_INJURY_FINE_MAX) -> int:
    """A coin fine in [0, max_fine] from a roll in [0, 1)."""
    return round(roll * max_fine)


def roll_hunt_padlock(roll: float, chance: float = HUNT_PADLOCK_CHANCE) -> bool:
    return roll < chance


# ══════════════════════════════════════════════════════════════════════════════
#  /explore
# ══════════════════════════════════════════════════════════════════════════════
def pick_explore_outcome(roll: float) -> str:
    """Map a roll in [0, 1) onto EXPLORE_OUTCOMES."""
    return weighted_pick(EXPLORE_OUTCOMES, roll)


def roll_coin_amount(roll: float, lo: int, hi: int) -> int:
    """Roll a coin amount inside [lo, hi] from a roll in [0, 1)."""
    return round(lo + roll * (hi - lo))


# ══════════════════════════════════════════════════════════════════════════════
#  /rob
# ══════════════════════════════════════════════════════════════════════════════
def rob_success(roll: float, has_luck: bool = False) -> bool:
    """Whether a robbery succeeds. Luck adds ROB_LUCK_BONUS, capped at
    ROB_SUCCESS_CAP."""
    chance = ROB_BASE_SUCCESS + (ROB_LUCK_BONUS if has_luck else 0.0)
    chance = min(chance, ROB_SUCCESS_CAP)
    return roll < chance


def rob_steal_amount(roll: float, target_balance: int) -> int:
    """Coins stolen on a successful robbery: 10-20% of the target's balance,
    capped at ROB_STEAL_CAP."""
    pct = ROB_STEAL_MIN_PCT + roll * (ROB_STEAL_MAX_PCT - ROB_STEAL_MIN_PCT)
    amount = round(target_balance * pct)
    return max(0, min(amount, ROB_STEAL_CAP))
