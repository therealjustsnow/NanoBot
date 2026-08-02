"""Pure activities helpers (no Discord deps) — covered by
tests/test_activities_helpers.py.

Every random decision takes an explicit roll in [0, 1) so the cog owns the
randomness and tests stay deterministic (the resolve_gamble pattern used
throughout the economy/fishing cogs).
"""

from utils.helpers import weighted_pick

from .constants import (
    ACTIVITY_DEFAULT_COOLDOWNS,
    ACTIVITY_MAX_CHARGES,
    CAREER_LADDER,
    COOLDOWN_MIN,
    ENCOUNTERS,
    ENCOUNTER_CHANCE,
    ENCOUNTER_ITEM_COIN_EQUIV,
    ENCOUNTER_OUTCOMES,
    EXPLORE_COINS_BIG,
    EXPLORE_COINS_SMALL,
    EXPLORE_OUTCOMES,
    HUNT_BAG_ODDS,
    HUNT_CATCHES,
    HUNT_INJURY_CHANCE,
    HUNT_INJURY_FINE_MAX,
    HUNT_ODDS,
    HUNT_PADLOCK_CHANCE,
    MINE_CAVE_IN_CHANCE,
    MINE_TREASURE_KEY_CHANCE,
    MINE_VEIN_ODDS,
    ORES,
    ORE_ODDS,
    PICKAXES,
    ROB_BASE_SUCCESS,
    ROB_LUCK_BONUS,
    ROB_STEAL_CAP,
    ROB_STEAL_MAX_PCT,
    ROB_STEAL_MIN_PCT,
    ROB_SUCCESS_CAP,
    STREAK_BONUS_CAP,
    STREAK_BONUS_PER_DAY,
    WORK_PAY_MAX,
    WORK_PAY_MIN,
    WORK_SCENES,
    _MINE_HIGH_TIERS,
)


# ══════════════════════════════════════════════════════════════════════════════
#  Cooldowns and charges
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


def max_charges(activity: str) -> int:
    """How many runs of an activity bank up while a member is away.

    A code constant rather than a setting — see the note in constants.py. An
    activity nobody registered a cap for banks nothing, which is the safe
    answer: it behaves exactly as it did before charges existed.
    """
    return max(1, ACTIVITY_MAX_CHARGES.get(activity, 1))


def charge_state(last: float, now: float, cooldown: int, charges: int) -> dict:
    """Read-only view of a member's charge bucket for one activity.

    The read-side mirror of utils.db.activities.try_claim_activity — same token
    bucket, no writes, so the dashboard can paint "3 ready, next in 4m" without
    touching the row. The claim itself stays the sole arbiter of whether a run
    happens; anything this returns can be stale by the time a button is pressed,
    which is fine because the press re-claims atomically.

    Returns {"ready": int, "max": int, "next_in": int} — `next_in` is seconds
    to the next charge, and 0 when the bucket is already full.
    """
    charges = max(1, int(charges))
    cooldown = max(1, int(cooldown))
    if not last:
        # Never run: a full bucket, not an empty one. (Also the shared-row case
        # — activities_stats is one row per user, so an activity a member has
        # never touched still has a 0 in its column.)
        return {"ready": charges, "max": charges, "next_in": 0}
    elapsed = now - max(last, now - charges * cooldown)
    ready = min(charges, int(elapsed // cooldown))
    if ready >= charges:
        return {"ready": charges, "max": charges, "next_in": 0}
    return {
        "ready": max(0, ready),
        "max": charges,
        "next_in": max(1, int(cooldown - (elapsed - ready * cooldown))),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Daily streak
# ══════════════════════════════════════════════════════════════════════════════
def next_adventure_streak(last_day: int, today: int, streak: int) -> int:
    """Consecutive-day streak after adventuring on `today` (epoch day numbers).

    Same rule as the fishing streak: yesterday continues it, anything else
    starts over at one. A second activity the *same* day never reaches here —
    db.try_claim_adventure_streak refuses the claim.
    """
    if last_day == today - 1:
        return streak + 1
    return 1


def streak_multiplier(streak: int) -> float:
    """Coin payout multiplier for a streak length. 1.0 on day one."""
    days = max(0, streak) - 1
    return 1.0 + min(STREAK_BONUS_CAP, max(0, days) * STREAK_BONUS_PER_DAY)


def apply_streak(amount: int, streak: int) -> int:
    """Scale a coin payout by the streak, never below the unscaled amount.

    The floor is what keeps a streak from being a liability: a fine or a cost
    is a negative amount, and multiplying that up would punish the member for
    turning up seven days running.
    """
    return max(amount, round(amount * streak_multiplier(streak)))


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
    """Independent bonus treasure_key chance on top of the ore roll.

    Rolled once per *dig*, not once per ore, so a fat vein doesn't multiply the
    key supply — the key:chest ratio is asserted in tests/test_activities_helpers.
    """
    return roll < chance


def roll_vein(roll: float, bonus: int = 0) -> int:
    """How many ore a successful dig yields — a roll in [0, 1) on MINE_VEIN_ODDS,
    plus the pickaxe's flat `vein` bonus.

    The bonus is what makes a pickaxe's return scale with its price: luck alone
    shifts ore mass linearly, so it bought the same +36 coins a dig at every
    tier while the price climbed 5-6x a rung (see the note on PICKAXES). Extra
    ore multiplies the luck-shifted ore value instead of adding to it, which is
    the only lever mining has — its throughput is fixed by the cooldown.
    """
    return int(weighted_pick(MINE_VEIN_ODDS, roll)) + max(0, bonus)


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


def roll_hunt_bag(roll: float) -> int:
    """How many catches one hunt brings back — a roll in [0, 1) on HUNT_BAG_ODDS."""
    return int(weighted_pick(HUNT_BAG_ODDS, roll))


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


# ══════════════════════════════════════════════════════════════════════════════
#  Encounters
# ══════════════════════════════════════════════════════════════════════════════
def encounters_for(activity: str) -> list[str]:
    """Encounter keys that can *open* on an activity, in registry order.

    Follow-up stages carry `activity: None` and so never match, which is the
    whole mechanism keeping a stage out of the opening roll — see the note above
    ENCOUNTERS.
    """
    return [k for k, e in ENCOUNTERS.items() if e["activity"] == activity]


def is_stage(encounter_key: str) -> bool:
    """Whether an encounter is a follow-up rather than something a run opens."""
    encounter = ENCOUNTERS.get(encounter_key)
    return encounter is not None and encounter["activity"] is None


def roll_encounter(
    activity: str, fire_roll: float, pick_roll: float, chance: float = ENCOUNTER_CHANCE
) -> str | None:
    """The encounter this run triggers, or None.

    Two explicit rolls: whether one fires at all, and which. Kept separate so a
    test can force the second without disturbing the first, and so an activity
    that grows a second encounter needs no change here.
    """
    if fire_roll >= chance:
        return None
    keys = encounters_for(activity)
    if not keys:
        return None
    return keys[min(int(pick_roll * len(keys)), len(keys) - 1)]


def find_option(encounter_key: str, option_key: str) -> dict | None:
    """One option of an encounter by key, or None if it isn't one of them."""
    encounter = ENCOUNTERS.get(encounter_key)
    if not encounter:
        return None
    for option in encounter["options"]:
        if option["key"] == option_key:
            return option
    return None


def resolve_encounter(encounter_key: str, option_key: str, roll: float) -> dict | None:
    """Resolve a chosen option to one outcome definition.

    Returns the ENCOUNTER_OUTCOMES entry (never a copy the caller can mutate
    into the registry — callers read `coins`/`item`/`text` and nothing else),
    or None when either key is unknown, which is the shape a stale button press
    after a reload takes.
    """
    option = find_option(encounter_key, option_key)
    if option is None:
        return None
    return ENCOUNTER_OUTCOMES.get(weighted_pick(option["outcomes"], roll))


def outcome_coins(outcome: dict, roll: float) -> int:
    """Coins an outcome pays (negative if it costs), from a roll in [0, 1)."""
    lo, hi = outcome.get("coins", (0, 0))
    return round(lo + roll * (hi - lo))


def outcome_next(outcome: dict | None) -> str | None:
    """The follow-up stage an outcome opens, if it opens one.

    Guarded rather than a bare `.get` because the two callers (the cog's view
    and the web engine) both reach here holding something that may be None after
    a stale press, and a chain that silently ends is the correct behaviour for a
    registry that changed underneath a live message.
    """
    if not outcome:
        return None
    nxt = outcome.get("next")
    return nxt if nxt in ENCOUNTERS else None


# ══════════════════════════════════════════════════════════════════════════════
#  The balance model
# ══════════════════════════════════════════════════════════════════════════════
# What the whole loop is worth per hour, recomputed from the tables above rather
# than written down anywhere. cogs/economy/helpers.py owns the matching figure
# for fishing, and tests/test_economy_balance.py recomputes both from the raw
# catalogues and asserts the ratio between them — so a change to an odds table
# that quietly doubles the loop's income fails CI instead of shipping.
#
# Item yields are counted at their sell value, since /inventory sell is where
# they end up. Encounters and the daily streak are excluded: both are earned by
# turning up and answering, and folding them in would describe a player who
# never misses a day as the baseline.
def _expected(odds: list[tuple], value_of) -> float:
    return sum(chance * value_of(key) for key, chance in odds)


# ── What the encounters are worth ────────────────────────────────────────────
# The first version of the encounter system was small enough that "about +8%"
# could be asserted by reading it. Three openers an activity, three options
# wide and two stages deep is past that, so the figure is computed from the
# tables instead — the same treatment the ore ladder and the fishing charters
# get, and for the same reason: the failure mode is a generous new entry that
# nobody notices has doubled the loop's income.
def coin_value(item_key: str) -> float:
    """What an item is worth in coins, for the balance model only.

    Most items answer for themselves — the catalogue holds the sell price the
    member will actually get. The two that don't are the chest and the key,
    which sell for nothing and are worth a great deal; ENCOUNTER_ITEM_COIN_EQUIV
    prices those, with the reasoning beside it.
    """
    if item_key in ENCOUNTER_ITEM_COIN_EQUIV:
        return float(ENCOUNTER_ITEM_COIN_EQUIV[item_key])
    # Deferred: cogs/activities/items.py registers the ore and hunt defs into
    # the shared catalogue at import time, and it imports this package.
    from utils import items as catalogue

    definition = catalogue.get(item_key)
    return float(definition.value) if definition else 0.0


def outcome_value(outcome_key: str, _seen: frozenset = frozenset()) -> float:
    """Expected coin-equivalent of one outcome, including whatever it opens."""
    outcome = ENCOUNTER_OUTCOMES.get(outcome_key)
    if outcome is None:
        return 0.0
    lo, hi = outcome.get("coins", (0, 0))
    value = (lo + hi) / 2
    if "item" in outcome:
        item_key, qty = outcome["item"]
        value += coin_value(item_key) * qty
    nxt = outcome.get("next")
    if nxt:
        value += encounter_value(nxt, _seen)
    return value


def option_value(encounter_key: str, option_key: str, _seen: frozenset = frozenset()):
    """Expected coin-equivalent of taking one option."""
    option = find_option(encounter_key, option_key)
    if option is None:
        return 0.0
    return sum(p * outcome_value(o, _seen) for o, p in option["outcomes"])


def encounter_value(encounter_key: str, _seen: frozenset = frozenset()) -> float:
    """What an encounter is worth to someone who picks the best option.

    The *best* rather than the average, because that is the bound worth
    guarding: an encounter is only as inflationary as its most rewarding
    answer, and members find that answer quickly. `_seen` breaks a cycle — the
    registry is a DAG today, and a typo'd `next` pointing back up the chain
    would otherwise recurse forever rather than fail a test.
    """
    encounter = ENCOUNTERS.get(encounter_key)
    if encounter is None or encounter_key in _seen:
        return 0.0
    seen = _seen | {encounter_key}
    return max(
        (option_value(encounter_key, o["key"], seen) for o in encounter["options"]),
        default=0.0,
    )


def activity_coins_per_run(
    activity: str, luck: float = 0.0, vein_bonus: int = 0
) -> float:
    """Expected coin value of one run of an activity, straight off the tables.

    `luck` and `vein_bonus` are the two things a pickaxe buys; every other
    activity ignores both. The defaults are the luck-0 floor the docs and the
    price curve are quoted against.
    """
    if activity == "work":
        return (WORK_PAY_MIN + WORK_PAY_MAX) / 2
    if activity == "mine":
        ore = _expected(mine_odds(luck), lambda k: ORES[k]["value"])
        vein = _expected(MINE_VEIN_ODDS, float) + max(0, vein_bonus)
        return (1 - MINE_CAVE_IN_CHANCE) * vein * ore
    if activity == "hunt":
        catch = _expected(HUNT_ODDS, lambda k: HUNT_CATCHES[k]["value"])
        bag = _expected(HUNT_BAG_ODDS, float)
        fine = HUNT_INJURY_CHANCE * (HUNT_INJURY_FINE_MAX / 2)
        return bag * catch - fine
    if activity == "explore":
        odds = dict(EXPLORE_OUTCOMES)
        small = sum(EXPLORE_COINS_SMALL) / 2
        big = sum(EXPLORE_COINS_BIG) / 2
        return odds["coins_small"] * small + odds["coins_big"] * big
    # /rob moves coins between two wallets and burns a fine; it mints nothing.
    return 0.0


def adventure_coins_per_hour(luck: float = 0.0) -> float:
    """What the loop generates per hour, whether or not anyone is collecting.

    Charges mean the clock runs while a member is away, so this is a
    *generation* rate rather than a claim rate — which is why it's the honest
    basis for `adventure_coins_per_day` below, and why it is no longer directly
    comparable to fishing's per-hour figure (fishing generates nothing while
    you're not casting).
    """
    return sum(
        activity_coins_per_run(activity, luck)
        * (3600 / ACTIVITY_DEFAULT_COOLDOWNS[activity])
        for activity in ACTIVITY_DEFAULT_COOLDOWNS
        if activity in ACTIVITY_MAX_CHARGES
    )


def adventure_coins_per_day(luck: float = 0.0) -> float:
    """The number the whole balance model is written against.

    Not a ceiling for once: with every cap covering half a day, a member who
    turns up twice collects all of it, so this is what an ordinary player
    actually earns from the loop rather than what a hypothetical constant one
    would. `/shop`'s time-to-earn and every ladder price are sized against it
    (see tests/test_economy_balance.py).
    """
    return adventure_coins_per_hour(luck) * 24


def coins_banked_after(hours: float, luck: float = 0.0) -> float:
    """Coins waiting to be collected after `hours` away, capped per activity.

    The function that shows what the old caps were doing: at a two-hour cap,
    this stopped rising after two hours, so a day away was worth the same as a
    lunch break. Every cap is now half a day, so it keeps rising until a real
    person would have checked in.
    """
    return sum(
        min(
            max_charges(activity),
            int(hours * 3600 // ACTIVITY_DEFAULT_COOLDOWNS[activity]),
        )
        * activity_coins_per_run(activity, luck)
        for activity in ACTIVITY_MAX_CHARGES
    )


def encounter_share(luck: float = 0.0) -> float:
    """What the encounter system adds to the loop's income, as a fraction.

    Deliberately *not* folded into `adventure_coins_per_hour`: that figure is
    what the clock generates whether or not anyone is there, and an encounter
    only pays a member who was present and answered it. This is the number to
    keep an eye on instead — encounters are meant to make a run a decision, not
    to be where the money is, and there is no natural ceiling on a registry
    somebody keeps adding good outcomes to.
    """
    base = extra = 0.0
    for activity in ACTIVITY_MAX_CHARGES:
        per_hour = 3600 / ACTIVITY_DEFAULT_COOLDOWNS[activity]
        base += activity_coins_per_run(activity, luck) * per_hour
        keys = encounters_for(activity)
        if keys:
            mean = sum(encounter_value(k) for k in keys) / len(keys)
            extra += ENCOUNTER_CHANCE * mean * per_hour
    return extra / base if base else 0.0


def mine_coins_per_day(tier: int) -> float:
    """What mining generates a day at a pickaxe tier, every charge collected.

    The denominator every pickaxe price is set from. Mining's throughput is
    fixed by its interval, so this — not the size of a member's wallet — is the
    ceiling on what a pickaxe can ever be worth.
    """
    pickaxe = pickaxe_info(tier)
    per_run = activity_coins_per_run("mine", pickaxe["luck"], pickaxe["vein"])
    return per_run * 24 * 3600 / ACTIVITY_DEFAULT_COOLDOWNS["mine"]


def pickaxe_payback_days(tier: int) -> float:
    """Days of ordinary mining for the tier-`tier` pickaxe to repay its price.

    The guard the ladder was missing. Fishing charters have had a payback test
    since they shipped; pickaxes were priced off the rod curve instead, and the
    top two took 84 and 350 days to break even without anything failing. The
    ladder is now priced from this function (tests/test_economy_balance.py
    asserts the figures ascend and stay finite), so a price edit that makes a
    tier a trap fails CI.
    """
    if tier <= 0 or tier >= len(PICKAXES):
        return 0.0
    gain = mine_coins_per_day(tier) - mine_coins_per_day(tier - 1)
    if gain <= 0:
        return float("inf")
    return PICKAXES[tier]["price"] / gain
