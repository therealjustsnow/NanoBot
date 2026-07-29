"""
Tests for the *shape* of the economy's price curve.

These are not "the constant equals the constant" assertions. They recompute
expected coins-per-cast from the fish catalogue and the rarity table, turn every
ladder price into a time-to-afford, and check that time against the curve the
rebalance deliberately chose when CAST_COOLDOWN dropped 60s -> 20s:

    early game  ~2x faster than the old 60s economy  (a first upgrade fast)
    mid game    unchanged pacing
    late game   slower, so the endgame stays an endgame

A flat multiplier on every price — the thing this rebalance is specifically not
— would fail these. So would tripling income again without revisiting sinks.
"""

import pytest

from cogs.activities.constants import PICKAXES
from cogs.casino.constants import DEFAULT_MAX_BET, DEFAULT_MIN_BET
from cogs.fishing.constants import (
    CAST_COOLDOWN,
    FISH,
    RARITY_ODDS,
    RODS,
)
from cogs.fishing.helpers import rarity_odds
from cogs.fishing.spots import DEFAULT_SPOT, SPOTS, SPOT_ORDER, fish_pool, spot_odds
from cogs.progression.constants import PRESTIGE_COST_BASE, PRESTIGE_MAX
from cogs.progression.helpers import prestige_requirement
from utils import items

# What the ladders cost when a cast was 60 seconds — the baseline every "x faster
# / x slower" claim in the constants files is measured against.
OLD_CAST_COOLDOWN = 60
OLD_ROD_PRICES = [0, 500, 2_500, 8_000, 25_000, 75_000]
OLD_PICKAXE_PRICES = [0, 500, 2_000, 8_000, 25_000]

# Intended time-to-afford ratio (new economy / old economy) per rod tier: the
# first two tiers arrive sooner, the middle holds, the last two take longer.
INTENDED_ROD_PACING = [0.5, 0.667, 1.0, 1.333, 2.0]


def coins_per_cast(luck: float, spot: str = DEFAULT_SPOT) -> float:
    """Expected coins from one cast at `luck`, straight off the drop tables.

    Weight scaling averages out (0.6x–1.4x around the base value) and treasure
    pays its value in coins, so the base value is the right expectation.

    A spot changes both the rarity table and which species each tier averages
    over, so both are applied here — independently of the production helper, so
    the two can be asserted against each other.
    """
    pools = {r: fish_pool(r, spot) for r, _p in RARITY_ODDS}
    return sum(
        chance * (sum(FISH[k]["value"] for k in pools[rarity]) / len(pools[rarity]))
        for rarity, chance in spot_odds(rarity_odds(luck), spot)
        if pools[rarity]
    )


def hours_to_afford(price: int, luck: float, cooldown: int) -> float:
    return price / (coins_per_cast(luck) * (3600 / cooldown))


# ── The shop's pricing yardstick agrees with this model ──────────────────────
def test_the_shop_yardstick_matches_the_balance_model():
    """`/shop` quotes each price as a time to earn, so a mod can price a reward
    by the effort it should cost instead of guessing at a number.

    That estimate is computed in production by cogs/economy/helpers, and this
    file recomputes the same quantity from the raw drop tables. Asserting they
    agree is what stops the two drifting: a change to the catalogue that moved
    income without moving the shop's estimate would show up here.
    """
    from cogs.economy.helpers import (
        coins_per_cast as prod_per_cast,
        coins_per_hour as prod_per_hour,
        seconds_to_afford,
    )

    for luck in (0.0, 0.3, 0.75):
        assert prod_per_cast(luck) == pytest.approx(coins_per_cast(luck))
        assert prod_per_hour(luck) == pytest.approx(
            coins_per_cast(luck) * 3600 / CAST_COOLDOWN
        )

    for price in (100, 5_000, 250_000):
        expected = hours_to_afford(price, 0.0, CAST_COOLDOWN) * 3600
        # The production helper rounds to whole seconds; nothing else may differ.
        assert seconds_to_afford(price) == pytest.approx(expected, abs=1)

    # Degenerate prices answer with a time rather than dividing by anything.
    assert seconds_to_afford(0) == 0
    assert seconds_to_afford(-5) == 0
    assert seconds_to_afford(1) >= 1  # never rounds a real price down to "free"


def adventure_coins_per_hour() -> float:
    """The adventure loop's ceiling, recomputed from the raw tables.

    Deliberately a second implementation of cogs.activities.helpers
    .adventure_coins_per_hour — the production one is what /adventure and the
    docs are written against, and asserting the two agree is what stops a tweak
    to an odds table moving the economy without anyone noticing.
    """
    from cogs.activities.constants import (
        ACTIVITY_DEFAULT_COOLDOWNS as CD,
        EXPLORE_COINS_BIG,
        EXPLORE_COINS_SMALL,
        EXPLORE_OUTCOMES,
        HUNT_BAG_ODDS,
        HUNT_CATCHES,
        HUNT_INJURY_CHANCE,
        HUNT_INJURY_FINE_MAX,
        HUNT_ODDS,
        MINE_CAVE_IN_CHANCE,
        MINE_VEIN_ODDS,
        ORES,
        ORE_ODDS,
        WORK_PAY_MAX,
        WORK_PAY_MIN,
    )

    work = (WORK_PAY_MIN + WORK_PAY_MAX) / 2
    ore = sum(p * ORES[k]["value"] for k, p in ORE_ODDS)
    vein = sum(p * n for n, p in MINE_VEIN_ODDS)
    mine = (1 - MINE_CAVE_IN_CHANCE) * vein * ore
    catch = sum(p * HUNT_CATCHES[k]["value"] for k, p in HUNT_ODDS)
    bag = sum(p * n for n, p in HUNT_BAG_ODDS)
    hunt = bag * catch - HUNT_INJURY_CHANCE * (HUNT_INJURY_FINE_MAX / 2)
    odds = dict(EXPLORE_OUTCOMES)
    explore = odds["coins_small"] * (sum(EXPLORE_COINS_SMALL) / 2) + odds[
        "coins_big"
    ] * (sum(EXPLORE_COINS_BIG) / 2)

    return sum(
        run * 3600 / CD[activity]
        for activity, run in (
            ("work", work),
            ("mine", mine),
            ("hunt", hunt),
            ("explore", explore),
        )
    )


def test_the_adventure_loop_agrees_with_its_own_balance_model():
    """cogs/activities publishes what its tables are worth; this recomputes it."""
    from cogs.activities.helpers import adventure_coins_per_hour as prod

    assert prod(0.0) == pytest.approx(adventure_coins_per_hour())


def test_adventure_is_a_real_second_route_to_the_shop():
    """The rebalance's actual claim, in one number.

    Before it, the whole loop paid ~220 coins/hour against fishing's ~5,100 —
    a twentieth — which is why a member who preferred adventuring to fishing
    could never afford anything in the shop. Multiplying the payouts would have
    made an activity better per *action* than a cast; the fix was to hand the
    loop more actions (shorter intervals, banked charges, veins and bags), and
    this is the bound on how far that went.
    """
    from cogs.economy.helpers import coins_per_hour

    assert adventure_coins_per_hour() > 4 * 220  # ~5x the loop it replaced


def test_fishing_is_still_the_right_denominator_for_the_yardstick():
    """The yardstick quotes fishing time, and only fishing.

    Adventure is a quarter of it now rather than a twentieth, which is the
    point — but fishing is still the fastest way to earn by a clear margin, so
    "how long would you have to fish for this" remains both the honest floor and
    the easy one to explain. If this ever inverts, /shop's estimate is quoting
    the wrong faucet and cogs/economy/helpers.py needs revisiting, not this
    assertion relaxing.
    """
    from cogs.economy.constants import REWARD_DEFAULTS
    from cogs.economy.helpers import coins_per_hour

    daily_hourly = REWARD_DEFAULTS["daily"] / 24
    assert coins_per_hour(0.0) > 3 * (adventure_coins_per_hour() + daily_hourly)


# ── Fishing spots: a paid step up, not a free one ────────────────────────────
def effective_per_cast(spot: str, luck: float = 0.0) -> float:
    """Coins per cast at a spot, net of its snag rate.

    A hazard costs the whole catch, so it scales the expectation directly —
    which is the only reason the rich spots aren't strictly better.
    """
    return coins_per_cast(luck, spot) * (1 - SPOTS[spot]["hazard"])


def test_every_spot_is_a_step_up_once_the_snags_are_paid_for():
    """A charter has to be worth buying. If a spot's snag rate ate its odds
    shift, the honest thing would be to price it at zero, not to sell it."""
    rates = [effective_per_cast(spot, 0.45) for spot in SPOT_ORDER]
    assert rates == sorted(rates)
    assert rates[0] < rates[-1]


def test_the_hazard_is_a_real_cost_not_a_rounding_error():
    """The snag has to bite, or the risk half of the risk/reward is decoration."""
    for spot in SPOT_ORDER[1:]:
        raw = coins_per_cast(0.45, spot)
        assert effective_per_cast(spot, 0.45) < raw
    assert SPOTS[SPOT_ORDER[0]]["hazard"] == 0  # the starter spot never snags


def test_the_deepest_water_does_not_out_earn_the_pond_by_more_than_3x():
    """The bound on the whole feature. Spots are meant to be a progression, not
    a second economy: past ~3x, the starter spot stops being somewhere anyone
    would fish and every shop price quoted against it becomes a fiction."""
    assert effective_per_cast(SPOT_ORDER[-1], 0.45) < 3 * coins_per_cast(0.45)


@pytest.mark.parametrize("spot", [s for s in SPOT_ORDER if SPOTS[s]["price"]])
def test_a_charter_pays_itself_back_over_hours_not_minutes(spot):
    """The charter is the sink that keeps spots from being pure inflation.

    Measured in casts at mid-game luck against staying put: under a thousand
    and the sink is decorative, over twenty thousand and nobody would ever buy
    it. Both ends are asserted so a price edit in either direction is caught.
    """
    gain = effective_per_cast(spot, 0.45) - coins_per_cast(0.45)
    assert gain > 0
    casts = SPOTS[spot]["price"] / gain
    assert 1_000 < casts < 20_000, f"{spot} pays back in {casts:,.0f} casts"


def test_charter_prices_and_level_gates_both_ascend():
    prices = [SPOTS[s]["price"] for s in SPOT_ORDER]
    levels = [SPOTS[s]["level"] for s in SPOT_ORDER]
    hazards = [SPOTS[s]["hazard"] for s in SPOT_ORDER]
    assert prices == sorted(prices)
    assert levels == sorted(levels)
    assert hazards == sorted(hazards)  # more reward, more risk, in step
    assert prices[0] == 0 and levels[0] == 0


def test_the_shop_yardstick_ignores_spots_on_purpose():
    """`/shop` quotes the starter spot so its estimate describes someone with
    nothing. A member who charters better water finds everything *cheaper* than
    quoted, which is the right direction for a floor to be wrong in."""
    from cogs.economy.helpers import seconds_to_afford

    quoted = seconds_to_afford(10_000)
    real_at_the_trench = (
        10_000 / effective_per_cast(SPOT_ORDER[-1], 0.0) * CAST_COOLDOWN
    )
    assert real_at_the_trench < quoted


def test_no_single_activity_out_earns_a_cast():
    """Per action the loop was always generous — a shift paid 100 against a
    cast's 28. Keeping the *rate* fix from turning into a payout fix is what
    stops an angler being the mug: an activity's run may beat a cast, but not
    by so much that casting looks foolish."""
    from cogs.activities.constants import ACTIVITY_MAX_CHARGES
    from cogs.activities.helpers import activity_coins_per_run

    for activity in ACTIVITY_MAX_CHARGES:
        assert activity_coins_per_run(activity) < 10 * coins_per_cast(0.0), activity


# ── The faucet itself ────────────────────────────────────────────────────────
def test_cast_cooldown_is_the_rebalanced_value():
    """Every price below was chosen against this number; they move together."""
    assert CAST_COOLDOWN == 20


def test_income_per_cast_did_not_change():
    """The rebalance changed the *rate*, not the reward. Bait, crafting and
    every other per-use price is priced off this and so needed no edit."""
    assert coins_per_cast(0.0) == pytest.approx(28.3, abs=0.5)


# ── Rod ladder: the staged curve ─────────────────────────────────────────────
@pytest.mark.parametrize("tier", range(1, len(RODS)))
def test_rod_pacing_follows_the_intended_curve(tier):
    luck = RODS[tier - 1]["luck"]
    old = hours_to_afford(OLD_ROD_PRICES[tier], luck, OLD_CAST_COOLDOWN)
    new = hours_to_afford(RODS[tier]["price"], luck, CAST_COOLDOWN)
    assert new / old == pytest.approx(INTENDED_ROD_PACING[tier - 1], rel=0.05)


def test_rod_curve_is_monotonic_and_accelerating():
    """Each tier costs more than the last, and each takes longer to reach than
    the one before it — no tier is a shortcut past its predecessor."""
    prices = [r["price"] for r in RODS]
    assert prices == sorted(prices)
    times = [
        hours_to_afford(RODS[t]["price"], RODS[t - 1]["luck"], CAST_COOLDOWN)
        for t in range(1, len(RODS))
    ]
    assert times == sorted(times)


def test_full_rod_ladder_takes_longer_than_it_used_to():
    """Income tripled; the whole ladder must still not get cheaper in time."""
    old = sum(
        hours_to_afford(OLD_ROD_PRICES[t], RODS[t - 1]["luck"], OLD_CAST_COOLDOWN)
        for t in range(1, len(RODS))
    )
    new = sum(
        hours_to_afford(RODS[t]["price"], RODS[t - 1]["luck"], CAST_COOLDOWN)
        for t in range(1, len(RODS))
    )
    assert new > old * 1.5


def test_first_upgrade_stays_within_a_first_session():
    """The early-game promise: something to buy inside ~15 minutes."""
    assert hours_to_afford(RODS[1]["price"], 0.0, CAST_COOLDOWN) < 0.25


# ── Pickaxe ladder mirrors the rod curve ─────────────────────────────────────
def test_pickaxes_scale_with_the_rods():
    """Mining income didn't change, but coins are fungible — fishing pays for
    these, so they ride the same curve rather than staying 3x cheap."""
    ratios = [
        PICKAXES[t]["price"] / OLD_PICKAXE_PRICES[t] for t in range(1, len(PICKAXES))
    ]
    assert ratios == sorted(ratios)
    assert ratios[0] == pytest.approx(1.5)
    assert ratios[-1] >= 4


# ── Per-use prices are cooldown-neutral and were left alone ──────────────────
@pytest.mark.parametrize(
    "key,expected_price",
    [("bait_worm", 25), ("bait_shrimp", 100), ("bait_glowgrub", 300)],
)
def test_bait_prices_unchanged(key, expected_price):
    """Bait is spent per *cast* and income per cast didn't move, so its real
    cost is untouched by the cooldown. Repricing it would have been a nerf."""
    assert items.find(key).price == expected_price


def test_bait_costs_a_sane_slice_of_a_cast():
    per_cast = items.find("bait_worm").price / 5  # 5 casts per worm
    assert 0.1 < per_cast / coins_per_cast(0.0) < 0.3


# ── Prestige: quadratic, and slower than before ──────────────────────────────
def test_prestige_cost_is_quadratic():
    costs = [prestige_requirement(r)[1] for r in range(PRESTIGE_MAX)]
    assert costs[0] == PRESTIGE_COST_BASE
    # Second differences of a quadratic are constant.
    firsts = [b - a for a, b in zip(costs, costs[1:])]
    seconds = {b - a for a, b in zip(firsts, firsts[1:])}
    assert len(seconds) == 1


def test_first_prestige_got_cheaper_and_the_ladder_got_dearer():
    """Exactly the intended shape: reachable sooner, finished much later."""
    assert prestige_requirement(0)[1] < 25_000  # the old flat rank-1 cost
    old_total = sum(25_000 * (r + 1) for r in range(PRESTIGE_MAX))
    new_total = sum(prestige_requirement(r)[1] for r in range(PRESTIGE_MAX))
    # Income tripled, so the sink has to grow by more than 3x to take longer.
    assert new_total > old_total * 3


def test_prestige_points_stay_linear():
    """Points come from achievements, which the cast cooldown doesn't speed up
    — only the coin half of the requirement was rebalanced."""
    points = [prestige_requirement(r)[0] for r in range(PRESTIGE_MAX)]
    assert {b - a for a, b in zip(points, points[1:])} == {points[0]}


# ── Casino stakes track income ───────────────────────────────────────────────
def test_default_bet_band_scaled_with_income():
    assert DEFAULT_MAX_BET == pytest.approx(1000 * 3, rel=0.01)
    assert DEFAULT_MIN_BET > 10
    # A max bet is still well under an hour of flat-out fishing.
    assert DEFAULT_MAX_BET < coins_per_cast(0.0) * (3600 / CAST_COOLDOWN)
