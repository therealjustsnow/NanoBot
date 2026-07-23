"""Pure fishing helpers (no Discord deps) — covered by tests/test_fishing_helpers.py.

Every random decision takes an explicit roll in [0, 1) so the cog owns the
randomness and tests stay deterministic (the resolve_gamble pattern).
"""

from .constants import FISH, FISH_BY_RARITY, RARITY_ODDS, RODS

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
    acc = 0.0
    odds = rarity_odds(luck)
    for rarity, p in odds:
        acc += p
        if roll < acc:
            return rarity
    return odds[-1][0]


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
