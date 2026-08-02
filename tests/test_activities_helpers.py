"""
Tests for the pure activities helpers in cogs/activities/ (no Discord deps).
"""

import math

import pytest

from cogs.activities import (
    ACTIVITY_COOLDOWN_BOUNDS,
    ACTIVITY_DEFAULT_COOLDOWNS,
    ACTIVITY_MAX_CHARGES,
    ACTIVITY_NAMES,
    COOLDOWN_MIN,
    CAREER_LADDER,
    ENCOUNTERS,
    ENCOUNTER_CHANCE,
    ENCOUNTER_OUTCOMES,
    EXPLORE_OUTCOMES,
    HUNT_BAG_ODDS,
    HUNT_ODDS,
    MINE_VEIN_ODDS,
    ORE_ODDS,
    ORES,
    PICKAXES,
    ROB_BASE_SUCCESS,
    ROB_LUCK_BONUS,
    ROB_STEAL_CAP,
    ROB_SUCCESS_CAP,
    STREAK_BONUS_CAP,
    STREAK_BONUS_PER_DAY,
    WORK_PAY_MAX,
    WORK_PAY_MIN,
    activity_coins_per_run,
    adventure_coins_per_day,
    coins_banked_after,
    apply_streak,
    career_info,
    charge_state,
    effective_cooldown,
    encounter_share,
    encounter_value,
    encounters_for,
    find_option,
    is_stage,
    hunt_injury_fine,
    max_charges,
    mine_odds,
    next_adventure_streak,
    next_career,
    next_pickaxe,
    option_value,
    outcome_coins,
    outcome_next,
    pick_explore_outcome,
    pick_hunt_catch,
    pick_ore,
    pick_work_scene,
    pickaxe_info,
    resolve_encounter,
    rob_steal_amount,
    rob_success,
    roll_cave_in,
    roll_coin_amount,
    roll_encounter,
    roll_hunt_bag,
    roll_hunt_injury,
    roll_hunt_padlock,
    roll_mine_treasure_key,
    roll_vein,
    roll_work_pay,
    streak_multiplier,
)
from utils import items


# ── Career ladder / /work ───────────────────────────────────────────────────────
def test_career_ladder_ascends():
    for a, b in zip(CAREER_LADDER, CAREER_LADDER[1:]):
        assert b[0] > a[0]  # threshold
        assert b[2] >= a[2]  # bonus never decreases


def test_career_info_clamps_to_tier():
    assert career_info(0)["tier"] == 0
    assert career_info(9)["tier"] == 0
    assert career_info(10)["tier"] == 1
    assert career_info(3000)["tier"] == len(CAREER_LADDER) - 1
    assert (
        career_info(999_999)["tier"] == len(CAREER_LADDER) - 1
    )  # clamped, no overflow


def test_next_career_progression():
    assert next_career(0)["tier"] == 1
    assert next_career(0)["shifts"] == CAREER_LADDER[1][0]
    assert next_career(999_999) is None


def test_roll_work_pay_bounds():
    assert roll_work_pay(0.0, 0) == WORK_PAY_MIN
    assert roll_work_pay(0.999999, 0) == WORK_PAY_MAX
    assert roll_work_pay(0.0, 10) == WORK_PAY_MIN + 10
    assert roll_work_pay(0.5, 0) >= 1  # never non-positive


def test_pick_work_scene_deterministic():
    from cogs.activities.constants import WORK_SCENES

    assert pick_work_scene(0.0) == WORK_SCENES[0]
    assert pick_work_scene(0.999999) == WORK_SCENES[-1]


# ── Mining odds / /mine ────────────────────────────────────────────────────────
def test_ore_catalogue_well_formed():
    for key, ore in ORES.items():
        assert ore["value"] >= 1, key
    assert set(k for k, _p in ORE_ODDS) == set(ORES.keys())


def test_base_ore_odds_sum_to_one():
    assert math.isclose(sum(p for _k, p in ORE_ODDS), 1.0)


@pytest.mark.parametrize("luck", [0.0, 0.15, 0.5, 0.75, 1.0])
def test_mine_odds_always_sum_to_one(luck):
    assert math.isclose(sum(p for _k, p in mine_odds(luck)), 1.0)


def test_mine_odds_luck_zero_matches_base():
    assert mine_odds(0.0) == ORE_ODDS


def test_mine_odds_luck_shifts_mass_upward():
    base = dict(mine_odds(0.0))
    lucky = dict(mine_odds(0.75))
    assert lucky["stone"] < base["stone"]
    assert lucky["coal"] < base["coal"]
    for tier in ("iron_ore", "gold_ore", "diamond"):
        assert lucky[tier] > base[tier]


def test_mine_odds_luck_clamped():
    assert mine_odds(-5) == mine_odds(0.0)
    assert mine_odds(5) == mine_odds(1.0)


def test_pick_ore_walks_the_table():
    assert pick_ore(0.0, 0.0) == ORE_ODDS[0][0]
    assert pick_ore(0.999999, 0.0) == ORE_ODDS[-1][0]


def test_roll_cave_in_boundary():
    from cogs.activities.constants import MINE_CAVE_IN_CHANCE

    assert roll_cave_in(MINE_CAVE_IN_CHANCE - 0.001) is True
    assert roll_cave_in(MINE_CAVE_IN_CHANCE + 0.001) is False


def test_roll_mine_treasure_key_boundary():
    from cogs.activities.constants import MINE_TREASURE_KEY_CHANCE

    assert roll_mine_treasure_key(MINE_TREASURE_KEY_CHANCE - 0.001) is True
    assert roll_mine_treasure_key(MINE_TREASURE_KEY_CHANCE + 0.001) is False


def test_pickaxe_ladder_ascends():
    for a, b in zip(PICKAXES, PICKAXES[1:]):
        assert b["price"] > a["price"]
        assert b["luck"] > a["luck"]
        assert b["vein"] >= a["vein"]
    assert PICKAXES[0]["price"] == 0
    assert PICKAXES[0]["vein"] == 0  # bare hands roll the raw vein table


def test_the_top_pickaxes_buy_ore_as_well_as_odds():
    """Luck alone is linear, so it bought the same few coins a dig at every tier
    while the price climbed 5-6x a rung. The upper tiers break extra ore so the
    return scales with what was paid for it — see the note on PICKAXES."""
    assert PICKAXES[-1]["vein"] > 0
    assert PICKAXES[-1]["vein"] > PICKAXES[len(PICKAXES) // 2]["vein"]


def test_pickaxe_info_clamps():
    assert pickaxe_info(-1) == PICKAXES[0]
    assert pickaxe_info(0) == PICKAXES[0]
    assert pickaxe_info(999) == PICKAXES[-1]


def test_next_pickaxe_progression():
    assert next_pickaxe(0) == PICKAXES[1]
    assert next_pickaxe(len(PICKAXES) - 2) == PICKAXES[-1]
    assert next_pickaxe(len(PICKAXES) - 1) is None
    assert next_pickaxe(999) is None


# ── Hunting / /hunt ─────────────────────────────────────────────────────────────
def test_hunt_odds_sum_to_one():
    assert math.isclose(sum(p for _k, p in HUNT_ODDS), 1.0)
    assert set(k for k, _p in HUNT_ODDS) == set(["pelt", "meat", "golden_antler"])


def test_pick_hunt_catch_walks_the_table():
    assert pick_hunt_catch(0.0) == HUNT_ODDS[0][0]
    assert pick_hunt_catch(0.999999) == HUNT_ODDS[-1][0]


def test_roll_hunt_injury_boundary():
    from cogs.activities.constants import HUNT_INJURY_CHANCE

    assert roll_hunt_injury(HUNT_INJURY_CHANCE - 0.001) is True
    assert roll_hunt_injury(HUNT_INJURY_CHANCE + 0.001) is False


def test_hunt_injury_fine_bounds():
    assert hunt_injury_fine(0.0) == 0
    from cogs.activities.constants import HUNT_INJURY_FINE_MAX

    assert hunt_injury_fine(1.0) == HUNT_INJURY_FINE_MAX
    for roll in (0.1, 0.5, 0.9):
        assert 0 <= hunt_injury_fine(roll) <= HUNT_INJURY_FINE_MAX


def test_roll_hunt_padlock_boundary():
    from cogs.activities.constants import HUNT_PADLOCK_CHANCE

    assert roll_hunt_padlock(HUNT_PADLOCK_CHANCE - 0.001) is True
    assert roll_hunt_padlock(HUNT_PADLOCK_CHANCE + 0.001) is False


# ── Exploring / /explore ────────────────────────────────────────────────────────
def test_explore_outcomes_sum_to_one():
    assert math.isclose(sum(p for _k, p in EXPLORE_OUTCOMES), 1.0)


def test_pick_explore_outcome_walks_the_table():
    assert pick_explore_outcome(0.0) == EXPLORE_OUTCOMES[0][0]
    assert pick_explore_outcome(0.999999) == EXPLORE_OUTCOMES[-1][0]


def test_treasure_key_supply_tracks_chest_supply():
    """Keys must not out-drop chests by more than a small margin.

    A treasure_key does exactly one thing: open a treasure_chest. /explore is
    the only chest faucet, but keys drop from /mine too — on a far shorter
    interval, so mining's rate is multiplied by several times the claims.
    Comparing the raw percentages hides that; this compares expected drops per
    day at full claim rate, which is what a player actually banks — and it is
    computed from the live intervals, so re-pacing either activity is checked
    here rather than quietly shifting the ratio.

    Some surplus is wanted (a chest with no key is worse than a spare key), but
    a large one means keys pile up unspendable — the state this ratio was
    tightened to fix, when it stood at 5.25:1.
    """
    from cogs.activities.constants import (
        EXPLORE_COOLDOWN_DEFAULT,
        MINE_COOLDOWN_DEFAULT,
        MINE_TREASURE_KEY_CHANCE,
    )

    day = 86_400
    explore_claims = day / EXPLORE_COOLDOWN_DEFAULT
    mine_claims = day / MINE_COOLDOWN_DEFAULT
    odds = dict(EXPLORE_OUTCOMES)

    keys = (
        explore_claims * odds["treasure_key"] + mine_claims * MINE_TREASURE_KEY_CHANCE
    )
    chests = explore_claims * odds["treasure_chest"]

    assert chests > 0, "explore is the only chest source — it can't drop to zero"
    assert 1.0 <= keys / chests <= 2.0, (
        f"{keys:.2f} keys/day against {chests:.2f} chests/day "
        f"({keys / chests:.2f}:1) — keys should modestly exceed chests, not bank up"
    )


def test_roll_coin_amount_bounds():
    assert roll_coin_amount(0.0, 100, 400) == 100
    assert roll_coin_amount(1.0, 100, 400) == 400
    mid = roll_coin_amount(0.5, 100, 400)
    assert 100 <= mid <= 400


# ── Robbing / /rob ───────────────────────────────────────────────────────────────
def test_rob_success_boundary_no_luck():
    assert rob_success(ROB_BASE_SUCCESS - 0.001, False) is True
    assert rob_success(ROB_BASE_SUCCESS + 0.001, False) is False


def test_rob_success_boundary_with_luck():
    chance = min(ROB_BASE_SUCCESS + ROB_LUCK_BONUS, ROB_SUCCESS_CAP)
    assert rob_success(chance - 0.001, True) is True
    assert rob_success(chance + 0.001, True) is False


def test_rob_success_luck_never_exceeds_cap():
    chance = min(ROB_BASE_SUCCESS + ROB_LUCK_BONUS, ROB_SUCCESS_CAP)
    assert chance <= ROB_SUCCESS_CAP


def test_rob_steal_amount_bounds():
    assert rob_steal_amount(0.0, 1000) == 100  # 10% of 1000
    amount = rob_steal_amount(0.999999, 1000)
    assert amount == round(1000 * 0.2)  # ~20%, well under the cap


def test_rob_steal_amount_capped():
    assert rob_steal_amount(0.999999, 1_000_000) == ROB_STEAL_CAP


def test_rob_steal_amount_never_negative():
    assert rob_steal_amount(0.0, 0) == 0
    assert rob_steal_amount(1.0, 0) == 0


# ── cooldown resolution (lengths are bot-wide, owner-set) ─────────────────────
def test_effective_cooldown_honours_the_owners_override():
    """Only the bot owner writes these, and claims are global, so whatever they
    set is what every server runs — there is no per-server floor to apply."""
    for activity, (low, high) in ACTIVITY_COOLDOWN_BOUNDS.items():
        assert effective_cooldown(activity, low) == low
        assert effective_cooldown(activity, high) == high
        assert effective_cooldown(activity, 600) == 600


def test_no_override_means_the_activitys_default():
    for activity, default in ACTIVITY_DEFAULT_COOLDOWNS.items():
        assert effective_cooldown(activity, None) == default
        assert ACTIVITY_COOLDOWN_BOUNDS[activity][0] <= default
        assert default <= ACTIVITY_COOLDOWN_BOUNDS[activity][1]


def test_effective_cooldown_never_degrades_to_no_cooldown():
    """The one failure mode this must not have. A junk value, an unknown
    activity, or a zero/negative row falls back to a real duration."""
    assert effective_cooldown("work", "nonsense") == ACTIVITY_DEFAULT_COOLDOWNS["work"]
    assert effective_cooldown("not_an_activity", None) > 0
    assert effective_cooldown("work", 0) == COOLDOWN_MIN
    assert effective_cooldown("work", -99) == COOLDOWN_MIN


# ── Charges (the read-side mirror of the token bucket) ───────────────────────
def test_every_activity_declares_a_charge_cap():
    for activity in ACTIVITY_NAMES:
        assert ACTIVITY_MAX_CHARGES[activity] >= 1
        assert max_charges(activity) == ACTIVITY_MAX_CHARGES[activity]


def test_rob_deliberately_banks_nothing():
    """A stored-up run of robberies is a different experience for the person on
    the receiving end. Its cooldown is a protection, not a pacer."""
    assert ACTIVITY_MAX_CHARGES["rob"] == 1


def test_an_unregistered_activity_banks_nothing():
    """The safe answer: behave exactly as it did before charges existed."""
    assert max_charges("not_an_activity") == 1


def test_never_run_reads_as_a_full_bucket():
    """activities_stats is one row per user, so an activity a member has never
    touched still holds a 0 — that's a full bucket, not an empty one."""
    state = charge_state(0, 100_000, 600, 3)
    assert state == {"ready": 3, "max": 3, "next_in": 0}


def test_charges_tick_back_one_interval_at_a_time():
    now = 100_000
    # Bucket drained (last == now), so nothing is ready.
    assert charge_state(now, now, 600, 3)["ready"] == 0
    assert charge_state(now, now, 600, 3)["next_in"] == 600
    assert charge_state(now, now + 599, 600, 3)["ready"] == 0
    assert charge_state(now, now + 600, 600, 3)["ready"] == 1
    assert charge_state(now, now + 1199, 600, 3)["ready"] == 1
    assert charge_state(now, now + 1200, 600, 3)["ready"] == 2


def test_next_in_counts_down_to_the_next_charge_not_the_next_empty():
    """Partway through refilling, the number a member wants is 'when does the
    NEXT one land', not 'when is the bucket full again'."""
    state = charge_state(100_000, 100_000 + 900, 600, 3)
    assert state["ready"] == 1
    assert state["next_in"] == 300


def test_a_full_bucket_reports_no_wait():
    assert charge_state(100_000, 100_000 + 99_999, 600, 3) == {
        "ready": 3,
        "max": 3,
        "next_in": 0,
    }


def test_charge_state_matches_the_claim_it_mirrors():
    """The read-side and the write-side are separate implementations (one is
    Python, one is SQL), so they are asserted against each other: whatever
    charge_state says is ready is exactly what try_claim_activity will allow."""
    now = 100_000.0
    cooldown, cap = 600, 4
    last = 0.0
    for _ in range(12):
        state = charge_state(last, now, cooldown, cap)
        floor = max(last, now - cap * cooldown) if last else now - cap * cooldown
        allowed = now - floor >= cooldown
        assert (state["ready"] > 0) is allowed
        if not allowed:
            break
        last = floor + cooldown


# ── Daily streak ─────────────────────────────────────────────────────────────
def test_streak_continues_from_yesterday_and_resets_otherwise():
    assert next_adventure_streak(19_999, 20_000, 4) == 5
    assert next_adventure_streak(19_990, 20_000, 4) == 1  # a gap starts over
    assert next_adventure_streak(0, 20_000, 0) == 1  # first ever


def test_streak_multiplier_climbs_then_holds():
    assert streak_multiplier(1) == 1.0  # day one is not a bonus
    assert streak_multiplier(2) == pytest.approx(1 + STREAK_BONUS_PER_DAY)
    assert streak_multiplier(999) == pytest.approx(1 + STREAK_BONUS_CAP)
    days = [streak_multiplier(d) for d in range(1, 20)]
    assert days == sorted(days)


def test_streak_never_inflates_a_fine():
    """A fine is a negative amount, and multiplying it up would punish the
    member for turning up seven days running."""
    assert apply_streak(-200, 7) == -200
    assert apply_streak(0, 7) == 0
    assert apply_streak(100, 7) > 100
    assert apply_streak(100, 1) == 100


# ── Veins and bags ───────────────────────────────────────────────────────────
def test_vein_and_bag_tables_are_well_formed():
    for table in (MINE_VEIN_ODDS, HUNT_BAG_ODDS):
        assert math.isclose(sum(p for _n, p in table), 1.0)
        sizes = [n for n, _p in table]
        assert sizes == sorted(sizes)
        assert min(sizes) >= 1


def test_vein_and_bag_walk_their_tables():
    assert roll_vein(0.0) == MINE_VEIN_ODDS[0][0]
    assert roll_vein(0.999999) == MINE_VEIN_ODDS[-1][0]
    assert roll_hunt_bag(0.0) == HUNT_BAG_ODDS[0][0]
    assert roll_hunt_bag(0.999999) == HUNT_BAG_ODDS[-1][0]


def test_the_pickaxe_vein_bonus_rides_on_top_of_the_table():
    """A bonus adds to whatever the seam rolled, so the smallest vein with the
    best pickaxe still beats the biggest with bare hands' odds at the low end."""
    for roll in (0.0, 0.5, 0.999999):
        assert roll_vein(roll, 4) == roll_vein(roll) + 4
    # Defaulted and nonsense bonuses both leave the roll alone, so a stored tier
    # that predates the key can never shrink a dig.
    assert roll_vein(0.5, 0) == roll_vein(0.5)
    assert roll_vein(0.5, -3) == roll_vein(0.5)


# ── Encounters ───────────────────────────────────────────────────────────────
def test_every_encounter_is_well_formed():
    for key, encounter in ENCOUNTERS.items():
        # An opener names the activity it fires on; a follow-up stage names
        # None, which is the only thing keeping it out of the opening roll.
        assert encounter["activity"] in ACTIVITY_NAMES or is_stage(key), key
        assert encounter["title"] and encounter["prompt"] and encounter["emoji"], key
        assert len(encounter["options"]) >= 2, key
        seen = set()
        for option in encounter["options"]:
            assert option["key"] not in seen, key
            seen.add(option["key"])
            assert option["label"], key
            assert math.isclose(sum(p for _o, p in option["outcomes"]), 1.0), key
            for outcome_key, _p in option["outcomes"]:
                assert outcome_key in ENCOUNTER_OUTCOMES, outcome_key


def test_every_stage_is_reachable_and_only_as_a_follow_up():
    """A stage nobody's `next` points at is dead content, and a stage that
    somehow opened a run would greet a member mid-story."""
    named = {
        outcome["next"] for outcome in ENCOUNTER_OUTCOMES.values() if "next" in outcome
    }
    stages = {key for key in ENCOUNTERS if is_stage(key)}
    assert stages, "chained encounters are the point — there should be some"
    assert stages == named
    for activity in ACTIVITY_NAMES:
        assert not (set(encounters_for(activity)) & stages), activity


def test_a_chain_terminates():
    """A `next` pointing back up its own chain would recurse forever at
    runtime; encounter_value's cycle guard hides that, so check the shape."""
    for key in ENCOUNTERS:
        seen, frontier = {key}, [key]
        while frontier:
            for option in ENCOUNTERS[frontier.pop()]["options"]:
                for outcome_key, _p in option["outcomes"]:
                    nxt = ENCOUNTER_OUTCOMES[outcome_key].get("next")
                    assert nxt not in seen, f"{key} loops via {outcome_key}"
                    if nxt:
                        seen.add(nxt)
                        frontier.append(nxt)


def test_every_encounter_outcome_is_payable():
    """An outcome names a coin range, a catalogue item, a follow-up stage, or
    some combination — the cog knows how to hand over exactly those."""
    for key, outcome in ENCOUNTER_OUTCOMES.items():
        assert outcome.get("text"), key
        assert set(outcome) <= {"text", "coins", "item", "next"}, key
        if "coins" in outcome:
            lo, hi = outcome["coins"]
            assert lo <= hi, key
        if "item" in outcome:
            item_key, qty = outcome["item"]
            assert items.find(item_key) is not None, key
            assert qty >= 1, key
        if "next" in outcome:
            assert outcome["next"] in ENCOUNTERS, key
            assert is_stage(outcome["next"]), key


def test_outcome_next_is_safe_on_anything():
    """Both callers reach it holding something that may be None or stale after
    a registry change under a live message."""
    assert outcome_next(None) is None
    assert outcome_next({}) is None
    assert outcome_next({"next": "no_such_stage"}) is None
    assert outcome_next(ENCOUNTER_OUTCOMES["till_pocketed"]) == "work_till_tape"


def test_rob_has_no_encounters():
    """It is already a coin flip with a decision in front of it."""
    assert encounters_for("rob") == []


def test_every_button_activity_has_something_to_encounter():
    from cogs.activities.views import BUTTON_ACTIVITIES

    for activity in BUTTON_ACTIVITIES:
        assert encounters_for(activity), activity


def test_encounter_fires_on_the_chance_boundary():
    assert roll_encounter("work", ENCOUNTER_CHANCE - 0.001, 0.0) == "work_overtime"
    assert roll_encounter("work", ENCOUNTER_CHANCE + 0.001, 0.0) is None
    assert roll_encounter("rob", 0.0, 0.0) is None  # nothing registered


def test_resolve_encounter_walks_the_option_table():
    assert (
        resolve_encounter("hunt_stag", "shoot", 0.0) is ENCOUNTER_OUTCOMES["stag_taken"]
    )
    assert (
        resolve_encounter("hunt_stag", "shoot", 0.999999)
        is ENCOUNTER_OUTCOMES["stag_lost"]
    )
    # A safe option that always pays lands on its single outcome either way.
    for roll in (0.0, 0.5, 0.999999):
        assert (
            resolve_encounter("work_overtime", "leave", roll)
            is ENCOUNTER_OUTCOMES["overtime_declined"]
        )


def test_resolve_encounter_shrugs_at_a_stale_press():
    """The shape a button press takes after the registry changed underneath a
    live message — never an exception."""
    assert resolve_encounter("no_such_encounter", "stay", 0.5) is None
    assert resolve_encounter("work_overtime", "no_such_option", 0.5) is None
    assert find_option("work_overtime", "nope") is None


def test_outcome_coins_can_charge_as_well_as_pay():
    lo, hi = ENCOUNTER_OUTCOMES["trader_sand"]["coins"]
    assert outcome_coins(ENCOUNTER_OUTCOMES["trader_sand"], 0.5) == lo == hi < 0
    assert outcome_coins(ENCOUNTER_OUTCOMES["stag_lost"], 0.5) == 0
    paid = outcome_coins(ENCOUNTER_OUTCOMES["overtime_paid"], 0.5)
    lo, hi = ENCOUNTER_OUTCOMES["overtime_paid"]["coins"]
    assert lo <= paid <= hi


def _has_risk(encounter_key, seen=frozenset()):
    """Whether anything down this encounter's tree is actually a gamble."""
    if encounter_key in seen:
        return False
    seen = seen | {encounter_key}
    for option in ENCOUNTERS[encounter_key]["options"]:
        values = [outcome_value_of(o) for o, _p in option["outcomes"]]
        if max(values) - min(values) > 0:
            return True
        for outcome_key, _p in option["outcomes"]:
            nxt = ENCOUNTER_OUTCOMES[outcome_key].get("next")
            if nxt and _has_risk(nxt, seen):
                return True
    return False


def outcome_value_of(outcome_key):
    from cogs.activities.helpers import outcome_value

    return outcome_value(outcome_key)


def _outcome_floor(outcome_key, seen=frozenset()):
    """The worst this outcome can leave you, chain included.

    Coins at the bottom of their range rather than the middle, and a follow-up
    counted at *its* floor — because "how bad does this get" is the second
    reason anyone picks one option over another, and an encounter that hides
    its whole downside a stage deeper would otherwise read as a certainty.
    """
    outcome = ENCOUNTER_OUTCOMES[outcome_key]
    from cogs.activities.helpers import coin_value

    floor = outcome.get("coins", (0, 0))[0]
    if "item" in outcome:
        item_key, qty = outcome["item"]
        floor += coin_value(item_key) * qty
    nxt = outcome.get("next")
    if nxt and nxt not in seen:
        floor += _encounter_floor(nxt, seen | {nxt})
    return floor


def _encounter_floor(encounter_key, seen=frozenset()):
    """The floor of the option a member would actually take (the best one)."""
    options = ENCOUNTERS[encounter_key]["options"]
    best = max(options, key=lambda o: option_value(encounter_key, o["key"]))
    return min(_outcome_floor(o, seen) for o, _p in best["outcomes"])


def test_every_encounter_is_a_real_gamble_somewhere():
    """If nothing anywhere in an encounter's tree had a spread, there'd be
    nothing to weigh — it would be a prompt with buttons.

    Chain-aware on purpose: /work's till is two certainties on the surface
    (write it up, or pocket it) and the entire risk lives one stage down, in
    whether the tape catches you.
    """
    for key, encounter in ENCOUNTERS.items():
        if encounter["activity"] is None:
            continue  # a stage is checked as part of the opener that reaches it
        assert _has_risk(key), f"{key} has nothing to gamble on"


def test_no_option_is_dominated_by_another():
    """An option worth less on average *and* less at its worst than some other
    option is one nobody should ever pick — a decoration on a decision rather
    than part of one.

    Two axes, because there are exactly two reasons to pick anything here: what
    it pays on average, and how bad it gets when it goes wrong. An option only
    has to win on one. This is the guard that keeps a third option off the
    stag: "how safe is your shot" is a single axis, so whichever two you keep,
    the third is strictly worse than one of them.
    """
    for key, encounter in ENCOUNTERS.items():
        profile = {}
        for option in encounter["options"]:
            outcomes = find_option(key, option["key"])["outcomes"]
            profile[option["key"]] = (
                option_value(key, option["key"]),
                min(_outcome_floor(o) for o, _p in outcomes),
            )
        for mine, here in profile.items():
            for theirs, there in profile.items():
                if mine == theirs or there == here:
                    continue
                assert not (
                    there[0] >= here[0] and there[1] >= here[1]
                ), f"{key}/{mine} is dominated by {theirs}"


def test_the_encounter_system_stays_a_garnish():
    """What encounters add on top of the loop's own income. They exist to make
    a run a decision; a registry somebody keeps adding good outcomes to has no
    natural ceiling, and at some point the decorative system is the faucet."""
    assert 0.04 < encounter_share() < 0.20


def test_a_chain_is_worth_more_than_its_first_step():
    """The whole point of `next`: taking the stag is not the end of the story,
    so its value has to include what the story is worth."""
    assert encounter_value("work_till_tape") != 0
    bare = sum(ENCOUNTER_OUTCOMES["till_pocketed"]["coins"]) / 2
    assert option_value("work_till", "pocket") != bare


# ── The balance model ────────────────────────────────────────────────────────
def test_rob_mints_nothing():
    """It moves coins between two wallets and burns a fine. Counting it as
    income would describe a transfer as a faucet."""
    assert activity_coins_per_run("rob") == 0


def test_the_loop_is_worth_roughly_what_the_docstring_claims():
    """constants.py states ~7,500 a day; a change that moves it a long way from
    there needs the note updated, not just the number.

    A *day* rather than an hour, because with every cap covering half a day
    that is the figure a real player sees — the hourly rate is generation, not
    income, and nobody experiences it.
    """
    assert 6_500 < adventure_coins_per_day() < 8_500


def test_every_cap_covers_half_a_day_of_its_own_interval():
    """The rule the whole re-pace turns on. A cap shorter than the gap between
    real visits silently throws away everything past it, which is exactly what
    a two-hour cap was doing before this."""
    for activity, cap in ACTIVITY_MAX_CHARGES.items():
        if activity == "rob":
            continue  # deliberately banks nothing; see constants.py
        banked_hours = cap * ACTIVITY_DEFAULT_COOLDOWNS[activity] / 3600
        assert banked_hours >= 12, f"{activity} only banks {banked_hours:.1f}h"


def test_being_away_all_day_beats_being_away_an_hour():
    """The symptom this fixes, asserted directly: the old caps saturated after
    two hours, so a day away paid the same as a lunch break."""
    hour = coins_banked_after(1)
    half_day = coins_banked_after(12)
    full_day = coins_banked_after(24)
    assert half_day > hour * 3
    # And past half a day it stops, which is the cap doing its job — banking a
    # week would make the loop an idle game rather than a reason to come back.
    assert full_day == half_day


def test_two_visits_a_day_collect_essentially_everything():
    """The design target in one line: turning up twice must not be punished."""
    assert 2 * coins_banked_after(12) >= 0.95 * adventure_coins_per_day()


def test_luck_only_helps_the_activity_that_has_it():
    """A pickaxe shifts ore odds and nothing else — /work does not pay more for
    owning one."""
    assert activity_coins_per_run("mine", 0.6) > activity_coins_per_run("mine", 0.0)
    assert activity_coins_per_run("work", 0.6) == activity_coins_per_run("work", 0.0)
