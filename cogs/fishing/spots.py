"""cogs/fishing/spots.py — where you fish, and what that changes.

Fishing was one place. You cast, the same table rolled, and the only thing that
ever moved was your rod — so an hour at level 40 looked exactly like an hour at
level 2, minus some junk. A spot is the missing axis: somewhere to *go*, earned
rather than given, that changes what comes out of the water.

A spot changes three things, in descending order of how much they matter:

  species  — each spot past the first has fish found nowhere else. This is the
             point. A rarity shift is a number going up; a Leviathan is a
             reason to sail out to the trench, and it stays in your dex
             afterwards.
  odds     — `weights` multiply the rarity table and it is renormalised, so a
             spot is a trade rather than a straight upgrade: the Deep pays in
             epics and legendaries and hands you far more junk doing it.
  hazard   — the rich spots can lose you the catch. See the note on `hazard`
             below for exactly what a snag costs, which is deliberately less
             than it sounds.

Getting there
─────────────
A spot needs a fishing **level** before it will even be offered, and then a
one-off **charter** paid in coins. Both, on purpose: the level makes it a goal
you fish towards, and the charter makes it a sink. That second half is what
keeps the whole feature from being inflation — a better spot earns more per
cast, so unlocking it has to take coins out of the economy first, and the
charter prices are sized (in tests/test_economy_balance.py) to pay themselves
back over a few thousand casts rather than a few hundred.

Travel between unlocked spots is free and instant. Charging for it would tax
exactly the people engaging most with the feature, and it is the same wallet
either way.

Scope: which spots you have chartered and where you are standing are **per
user**, like everything else an angler owns (utils/db/fishing.py). A charter
bought in one server works in every server, because it was bought with a global
wallet.
"""

from utils.helpers import weighted_pick

from .constants import FISH, RARITY_ODDS

# The spot every angler starts at. Free, safe, no exclusives — a real place to
# fish rather than a tutorial you leave behind, since every non-exclusive
# species in the catalogue is still in its pool.
DEFAULT_SPOT = "pond"

# Registry, in progression order (the order they're listed and unlocked in).
#
#   level   fishing level before the charter is offered at all
#   price   one-off charter cost in coins
#   hazard  chance per cast of losing the catch (see `roll_hazard`)
#   weights rarity multipliers applied to the luck-adjusted table, then
#           renormalised. A rarity absent from the dict is left at 1.0.
SPOTS: dict[str, dict] = {
    "pond": {
        "name": "Old Pond",
        "emoji": "🪷",
        "blurb": "Still water, no surprises. Everything common lives here.",
        "level": 0,
        "price": 0,
        "hazard": 0.0,
        "weights": {},
    },
    "river": {
        "name": "River Bend",
        "emoji": "🏞️",
        "blurb": "Moving water. Fewer scraps, better fish, the odd snag.",
        "level": 5,
        "price": 6_000,
        "hazard": 0.03,
        "weights": {
            "junk": 0.75,
            "common": 0.90,
            "uncommon": 1.35,
            "rare": 1.45,
            "epic": 1.10,
        },
    },
    "reef": {
        "name": "Coral Reef",
        "emoji": "🪸",
        "blurb": "Warm and crowded. Sharp coral, spectacular fish.",
        "level": 12,
        "price": 35_000,
        "hazard": 0.06,
        "weights": {
            "junk": 0.85,
            "common": 0.65,
            "uncommon": 1.20,
            "rare": 1.65,
            "epic": 2.00,
            "legendary": 1.40,
            "treasure": 1.30,
        },
    },
    "deep": {
        "name": "The Deep",
        "emoji": "🌊",
        "blurb": "Open ocean. Half of it is rubbish; the other half is enormous.",
        "level": 22,
        "price": 140_000,
        "hazard": 0.10,
        "weights": {
            "junk": 1.60,
            "common": 0.45,
            "uncommon": 0.80,
            "rare": 1.50,
            "epic": 2.50,
            "legendary": 3.20,
            "treasure": 1.20,
        },
    },
    "abyss": {
        "name": "Abyssal Trench",
        "emoji": "🕳️",
        "blurb": "No light, no bottom. Things down here have no business existing.",
        "level": 35,
        "price": 600_000,
        "hazard": 0.15,
        "weights": {
            "junk": 1.90,
            "common": 0.25,
            "uncommon": 0.55,
            "rare": 1.30,
            "epic": 2.70,
            "legendary": 5.00,
            "treasure": 1.50,
        },
    },
}

SPOT_ORDER: tuple[str, ...] = tuple(SPOTS)

# spot → rarity → catalogue keys, in FISH insertion order. A spot's pool is
# every species with no `spot` of its own, plus its own exclusives — so
# travelling adds species without ever taking one away, and `pick_fish`'s
# roll → index contract holds per pool.
#
# Built once at import: this is read on every cast.
SPOT_POOLS: dict[str, dict[str, list[str]]] = {spot: {} for spot in SPOTS}
for _key, _fish in FISH.items():
    _exclusive = _fish.get("spot")
    for _spot in SPOTS:
        if _exclusive in (None, _spot):
            SPOT_POOLS[_spot].setdefault(_fish["rarity"], []).append(_key)


def spot_info(spot: str) -> dict:
    """A spot's registry entry, falling back to the starter spot.

    Never raises on an unknown key: the stored value comes from a database
    column, and a spot removed from the registry in a later version must not
    stop anyone fishing.
    """
    return SPOTS.get(spot) or SPOTS[DEFAULT_SPOT]


def resolve_spot(spot: str | None) -> str:
    """The spot key to actually use — the starter spot for anything unknown."""
    return spot if spot in SPOTS else DEFAULT_SPOT


def find_spot(query: str) -> str | None:
    """Look a spot up by key or display name, case-insensitively."""
    q = (query or "").strip().lower()
    if not q:
        return None
    if q in SPOTS:
        return q
    for key, spot in SPOTS.items():
        if spot["name"].lower() == q:
            return key
    for key, spot in SPOTS.items():
        if q in spot["name"].lower():
            return key
    return None


def fish_pool(rarity: str, spot: str | None = None) -> list[str]:
    """Catalogue keys of one rarity available at a spot."""
    return SPOT_POOLS[resolve_spot(spot)].get(rarity, [])


def exclusive_keys(spot: str) -> list[str]:
    """The species only found at this spot, in catalogue order."""
    return [key for key, fish in FISH.items() if fish.get("spot") == spot]


def spot_odds(base: list[tuple[str, float]], spot: str | None = None):
    """Apply a spot's rarity weights to an already luck-adjusted table.

    Multiply then renormalise, rather than shifting mass between named tiers
    the way `rarity_odds` does for luck. The two model different things: luck
    is "less junk", which is a *direction*, while a spot is "this water holds
    different creatures", which is a whole shape. Multiplying lets a spot raise
    junk and legendaries at the same time — the Deep's entire character — which
    a shift-mass-upward transform can't express.

    Returns the table in RARITY_ODDS order, still summing to 1.
    """
    weights = spot_info(resolve_spot(spot))["weights"]
    if not weights:
        return list(base)
    weighted = [(rarity, p * weights.get(rarity, 1.0)) for rarity, p in base]
    total = sum(p for _r, p in weighted)
    if total <= 0:  # a registry edit that zeroed everything; fall back rather
        return list(base)  # than hand the caller a table it can't walk
    return [(rarity, p / total) for rarity, p in weighted]


def roll_hazard(roll: float, spot: str | None = None) -> bool:
    """Whether this cast snags and loses the catch — a roll in [0, 1).

    What a snag actually costs is deliberately small: the catch, and the one
    bait charge the cast had already spent before the line went in (see
    `cog._resolve_cast` — the charge is consumed at cast time whatever
    happens, so a snag never reaches back into the rest of the stack). XP is
    still awarded. Losing an armed bait *stack* to a bad roll would make the
    good spots feel punitive rather than risky, and would mean the correct play
    at the Trench was to fish it unbaited.
    """
    return roll < spot_info(resolve_spot(spot))["hazard"]


def pick_spot_fish(rarity: str, roll: float, spot: str | None = None) -> str:
    """Pick a species of `rarity` from a spot's pool, from a roll in [0, 1)."""
    keys = fish_pool(rarity, spot)
    if not keys:
        # Only reachable if a registry edit left a rarity with no species at
        # this spot; the starter pool always has one.
        keys = SPOT_POOLS[DEFAULT_SPOT][rarity]
    return keys[min(int(roll * len(keys)), len(keys) - 1)]


def pick_spot_rarity(roll: float, luck_odds: list[tuple[str, float]], spot=None) -> str:
    """Map a roll onto the spot-adjusted rarity table."""
    return weighted_pick(spot_odds(luck_odds, spot), roll)


def next_spot(spot: str) -> str | None:
    """The next spot along the progression, or None at the end of the map."""
    try:
        index = SPOT_ORDER.index(resolve_spot(spot))
    except ValueError:  # pragma: no cover - resolve_spot guarantees membership
        return None
    return SPOT_ORDER[index + 1] if index + 1 < len(SPOT_ORDER) else None


def unlock_state(spot: str, level: int, unlocked: set[str], balance: int) -> dict:
    """What a member can currently do about one spot.

    Returns {"state": …, "reason": str} where state is one of:
      "open"     — chartered (or free), travel any time
      "buyable"  — level met and the coins are there
      "poor"     — level met, not enough coins
      "locked"   — fishing level too low

    One place decides this so the travel screen, its buttons and the picker all
    agree; getting it wrong in one of the three is how a UI ends up offering a
    button that then refuses.
    """
    info = spot_info(spot)
    if spot == DEFAULT_SPOT or spot in unlocked:
        return {"state": "open", "reason": ""}
    if level < info["level"]:
        return {
            "state": "locked",
            "reason": f"Fishing level {info['level']} (you're {level})",
        }
    if balance < info["price"]:
        return {"state": "poor", "reason": f"Charter costs {info['price']:,}"}
    return {"state": "buyable", "reason": f"Charter for {info['price']:,}"}


__all__ = [
    "DEFAULT_SPOT",
    "SPOTS",
    "SPOT_ORDER",
    "SPOT_POOLS",
    "exclusive_keys",
    "find_spot",
    "fish_pool",
    "next_spot",
    "pick_spot_fish",
    "pick_spot_rarity",
    "resolve_spot",
    "roll_hazard",
    "spot_info",
    "spot_odds",
    "unlock_state",
]

# Referenced in the module docstring's reasoning; imported here so a reader
# following `weights` back to the base table doesn't have to go hunting.
_BASE_RARITY_ORDER = [rarity for rarity, _p in RARITY_ODDS]
