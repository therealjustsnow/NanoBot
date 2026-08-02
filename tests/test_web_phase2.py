"""
Tests for the Phase 2 engines — casino, crafting, progression, the wardrobe,
analytics — and the moderation framework's decision logic.

Same split as Phase 1: behaviour here, the anti-drift guard in
`tests/test_web_engine_parity.py`. What is being pinned is the stuff that costs
somebody real coins if it's wrong — that a bet is debited before it can be won,
that a blackjack hand settles exactly once, that a failed craft hands the
materials back, and that an achievement can't be awarded twice by refreshing.
"""

import aiosqlite
import pytest

import utils.db as db
from web import moderation as mod
from web.engine import analytics as A
from web.engine import casino as C
from web.engine import crafting as Cr
from web.engine import identity as I
from web.engine import progression as P
from web.engine.fishing import PlayError

G = 4242
U = 7


@pytest.fixture(autouse=True)
async def database(monkeypatch):
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    monkeypatch.setattr(db, "_db", conn)
    for setup in (
        db._ensure_economy_tables,
        db._ensure_fishing_tables,
        db._ensure_items_tables,
        db._ensure_activities_tables,
        db._ensure_identity_tables,
        db._ensure_settings_tables,
        db._ensure_progression_tables,
        db._ensure_casino_tables,
        db._ensure_warnings_tables,
        db._ensure_ticket_tables,
        db._ensure_leveling_tables,
    ):
        await setup()
    yield conn
    await conn.close()


# ══════════════════════════════════════════════════════════════════════════════
#  Casino
# ══════════════════════════════════════════════════════════════════════════════
async def test_the_floor_reads_on_a_fresh_account():
    state = await C.state(U, G)
    assert state["enabled"] is True
    assert len(state["games"]) == 5
    assert state["limits"]["min"] > 0
    assert len(state["challenges"]) == 2
    assert state["open_hand"] is None


async def test_a_bet_is_debited_before_it_can_be_won():
    """The stake leaves the wallet first. Any other order is a free bet."""
    await db.add_coins(U, 1000)
    result = await C.flip(U, G, 100, "heads")
    expected = 1000 - 100 + result["payout"]
    assert await db.get_balance(U) == expected
    assert result["net"] == result["payout"] - 100


async def test_you_cannot_bet_coins_you_do_not_have():
    await db.add_coins(U, 10)
    with pytest.raises(PlayError) as caught:
        await C.flip(U, G, 100, "heads")
    assert caught.value.code == "broke"
    assert await db.get_balance(U) == 10


async def test_bet_limits_are_the_guilds():
    await db.add_coins(U, 1_000_000)
    await db.set_casino_config(G, min_bet=50, max_bet=500)
    with pytest.raises(PlayError) as caught:
        await C.flip(U, G, 10, "heads")
    assert caught.value.code == "bet_too_small"
    with pytest.raises(PlayError) as caught:
        await C.flip(U, G, 5000, "heads")
    assert caught.value.code == "bet_too_big"


async def test_a_disabled_casino_refuses_before_debiting():
    await db.add_coins(U, 1000)
    await db.set_casino_config(G, enabled=False)
    with pytest.raises(PlayError) as caught:
        await C.dice(U, G, 100)
    assert caught.value.code == "disabled"
    assert await db.get_balance(U) == 1000


async def test_a_loss_feeds_the_jackpot():
    """20% of every net loss, which is what makes the pot grow at all."""
    await db.add_coins(U, 100_000)
    before = (await db.get_casino_config(G))["jackpot_pool"]
    for _ in range(40):
        await C.flip(U, G, 100, "heads")
    after = (await db.get_casino_config(G))["jackpot_pool"]
    assert after > before


async def test_every_game_records_a_played_game():
    await db.add_coins(U, 100_000)
    await C.flip(U, G, 50, "tails")
    await C.dice(U, G, 50)
    await C.slots(U, G, 50)
    await C.roulette(U, G, 50, "red")
    assert (await db.get_casino_stats(U))["games"] == 4


async def test_the_casino_rank_is_a_position_not_a_pair():
    """`get_casino_rank` answers (position, net); the stats card wants the
    position. Shipping the pair rendered the player's rank as "#0" — the same
    leak the wallet had."""
    await db.add_coins(U, 10_000)
    await C.flip(U, G, 50, "heads")
    rank = (await C.state(U, G))["stats"]["rank"]
    assert rank == 1
    assert isinstance(rank, int) and not isinstance(rank, (tuple, list))


async def test_a_player_who_has_never_bet_has_no_rank():
    assert (await C.state(U, G))["stats"]["rank"] is None


async def test_roulette_refuses_a_space_that_is_not_one():
    await db.add_coins(U, 1000)
    with pytest.raises(PlayError) as caught:
        await C.roulette(U, G, 50, "purple")
    assert caught.value.code == "bad_space"
    assert await db.get_balance(U) == 1000


async def test_flip_needs_a_side():
    await db.add_coins(U, 1000)
    with pytest.raises(PlayError) as caught:
        await C.flip(U, G, 50, "sideways")
    assert caught.value.code == "bad_side"


async def test_the_streak_bonus_only_applies_to_a_win():
    """A push must never be boosted — it is a refund, not a payout."""
    await db.add_coins(U, 100_000)
    pushes = 0
    for _ in range(60):
        result = await C.dice(U, G, 100)
        if result["outcome"] == "push":
            pushes += 1
            assert result["payout"] == 100
            assert result["streak_bonus"] == 0
    if not pushes:
        pytest.skip("no push in 60 rounds")


# ── Blackjack ─────────────────────────────────────────────────────────────────
async def test_blackjack_persists_an_open_hand():
    """The hand goes in the same table Discord uses, so it is a *real* hand."""
    await db.add_coins(U, 10_000)
    for _ in range(20):
        result = await C.blackjack(U, G, 100)
        if result["state"] == "playing":
            break
    else:
        pytest.skip("twenty naturals in a row")

    assert result["hand_id"]
    assert len(result["player"]["cards"]) == 2
    # The dealer's hole card is *omitted*, not sent-and-hidden — a card the
    # client is told not to draw is a card anybody can read off the wire.
    assert result["dealer"]["hidden"] is True
    assert any(card.get("hidden") for card in result["dealer"]["cards"])
    assert all("rank" not in c for c in result["dealer"]["cards"] if c.get("hidden"))

    rows = await db.get_open_blackjack_hands()
    assert [r["hand_id"] for r in rows] == [result["hand_id"]]


async def test_a_web_hand_has_no_message_so_the_sweep_can_settle_it():
    """A hand with no message_id is exactly the shape the cog's orphaned-hand
    sweep already knows how to settle on restart — which is why a web hand is
    stored that way rather than needing new recovery code."""
    await db.add_coins(U, 10_000)
    for _ in range(20):
        result = await C.blackjack(U, G, 100)
        if result["state"] == "playing":
            break
    else:
        pytest.skip("twenty naturals in a row")
    row = await db.get_blackjack_hand(result["hand_id"])
    assert row["message_id"] is None


async def test_standing_settles_exactly_once():
    await db.add_coins(U, 10_000)
    for _ in range(20):
        dealt = await C.blackjack(U, G, 100)
        if dealt["state"] == "playing":
            break
    else:
        pytest.skip("twenty naturals in a row")

    done = await C.blackjack_action(U, G, dealt["hand_id"], "stand")
    assert done["state"] == "done"
    assert done["dealer"]["hidden"] is False

    with pytest.raises(PlayError) as caught:
        await C.blackjack_action(U, G, dealt["hand_id"], "stand")
    assert caught.value.code == "gone"


async def test_another_account_cannot_play_your_hand():
    await db.add_coins(U, 10_000)
    for _ in range(20):
        dealt = await C.blackjack(U, G, 100)
        if dealt["state"] == "playing":
            break
    else:
        pytest.skip("twenty naturals in a row")
    with pytest.raises(PlayError) as caught:
        await C.blackjack_action(U + 1, G, dealt["hand_id"], "hit")
    assert caught.value.code == "not_yours"


async def test_hitting_persists_the_new_card():
    """The shoe is re-read and re-written server-side, so replaying a request
    can't re-deal the same card."""
    await db.add_coins(U, 10_000)
    for _ in range(20):
        dealt = await C.blackjack(U, G, 100)
        if dealt["state"] == "playing":
            break
    else:
        pytest.skip("twenty naturals in a row")

    after = await C.blackjack_action(U, G, dealt["hand_id"], "hit")
    if after["state"] == "playing":
        assert len(after["player"]["cards"]) == 3
        row = await db.get_blackjack_hand(dealt["hand_id"])
        assert len(row["player"]) == 3
    else:
        assert after["outcome"] == "bust"


async def test_an_open_hand_is_offered_back_by_state():
    await db.add_coins(U, 10_000)
    for _ in range(20):
        dealt = await C.blackjack(U, G, 100)
        if dealt["state"] == "playing":
            break
    else:
        pytest.skip("twenty naturals in a row")
    state = await C.state(U, G)
    assert state["open_hand"]["hand_id"] == dealt["hand_id"]


# ── Challenges ────────────────────────────────────────────────────────────────
async def test_challenges_advance_and_claim_once():
    await db.add_coins(U, 100_000)
    for _ in range(40):
        await C.flip(U, G, 50, "heads")
    state = await C.state(U, G)
    played = next((c for c in state["challenges"] if c["key"] == "play_games"), None)
    if played is None:
        pytest.skip("today's challenge set doesn't include play_games")
    assert played["progress"] > 0
    if played["claimed"]:
        # Claimed exactly once: playing more can't re-award it.
        before = await db.get_balance(U)
        await C.flip(U, G, 50, "heads")
        after = await db.get_balance(U)
        assert after - before <= 50 * 2  # the flip's own payout, no re-claim


# ══════════════════════════════════════════════════════════════════════════════
#  Crafting
# ══════════════════════════════════════════════════════════════════════════════
async def test_every_recipe_reports_against_your_materials():
    state = await Cr.state(U, G)
    assert state["recipes"]
    for recipe in state["recipes"]:
        assert recipe["inputs"]
        assert recipe["craftable"] is False  # nothing owned yet
        assert recipe["max_craftable"] == 0
        assert all(i["short"] > 0 for i in recipe["inputs"])


async def test_crafting_consumes_the_inputs_and_yields_the_output():
    state = await Cr.state(U, G)
    recipe = state["recipes"][0]
    for item in recipe["inputs"]:
        await db.add_item(U, item["key"], item["need"])

    result = await Cr.craft(U, G, recipe["key"], 1)
    assert result["made"] == recipe["output"]["qty"]
    assert await db.get_item_qty(U, recipe["output"]["key"]) == result["made"]
    for item in recipe["inputs"]:
        assert await db.get_item_qty(U, item["key"]) == 0


async def test_a_short_craft_names_what_is_missing_and_takes_nothing():
    state = await Cr.state(U, G)
    recipe = state["recipes"][0]
    first = recipe["inputs"][0]
    await db.add_item(U, first["key"], first["need"])

    with pytest.raises(PlayError) as caught:
        await Cr.craft(U, G, recipe["key"], 1)
    assert caught.value.code == "short"
    assert caught.value.extra["missing"]
    # Nothing was consumed — the check happens before any spend.
    assert await db.get_item_qty(U, first["key"]) == first["need"]


async def test_max_craftable_never_offers_more_than_the_materials_allow():
    state = await Cr.state(U, G)
    recipe = state["recipes"][0]
    for item in recipe["inputs"]:
        await db.add_item(U, item["key"], item["need"] * 3)
    fresh = await Cr.state(U, G)
    updated = next(r for r in fresh["recipes"] if r["key"] == recipe["key"])
    assert updated["craftable"] is True
    assert updated["max_craftable"] == 3
    # And crafting that many actually works.
    result = await Cr.craft(U, G, recipe["key"], 3)
    assert result["qty"] == 3


async def test_an_unknown_recipe_is_a_refusal():
    with pytest.raises(PlayError) as caught:
        await Cr.craft(U, G, "not_a_recipe", 1)
    assert caught.value.code == "unknown_recipe"


# ══════════════════════════════════════════════════════════════════════════════
#  Progression
# ══════════════════════════════════════════════════════════════════════════════
async def test_progression_reads_on_a_fresh_account():
    state = await P.state(U, G)
    assert state["earned_count"] == 0
    assert state["total_count"] > 0
    assert state["achievements"]
    assert all(0 <= a["progress"] <= 1 for a in state["achievements"])
    assert state["prestige"]["rank"] == 0
    assert len(state["weekly"]["objectives"]) == 3


async def test_an_achievement_is_awarded_once_however_often_you_look():
    """The page evaluates on every load, so the once-only insert is what stops
    a refresh from being a coin printer."""
    await db.add_coins(U, 5_000_000)
    first = await P.state(U, G)
    earned = first["earned_count"]
    if not earned:
        pytest.skip("no wealth achievement crossed at 5M")

    balance = await db.get_balance(U)
    second = await P.state(U, G)
    assert second["earned_count"] == earned
    assert second["newly_earned"] == []
    assert await db.get_balance(U) == balance


async def test_looking_at_someone_else_never_awards_them_anything():
    await db.add_coins(U + 1, 5_000_000)
    state = await P.state(U + 1, G, viewer_id=U)
    assert state["newly_earned"] == []
    assert await db.get_earned_achievements(U + 1) == {}


async def test_closest_lists_only_unearned_ones_in_reach():
    await db.add_coins(U, 500)
    state = await P.state(U, G)
    for entry in state["closest"]:
        assert entry["earned"] is False
        assert entry["progress"] > 0
    # Sorted by how close they are.
    values = [entry["progress"] for entry in state["closest"]]
    assert values == sorted(values, reverse=True)


async def test_a_title_has_to_be_earned():
    with pytest.raises(PlayError) as caught:
        await P.set_title(U, "Definitely Not Real")
    assert caught.value.code == "not_earned"
    # Clearing is always allowed.
    assert (await P.set_title(U, ""))["title"] is None


async def test_prestige_refuses_until_you_qualify():
    with pytest.raises(PlayError) as caught:
        await P.prestige(U, G)
    assert caught.value.code == "not_ready"
    assert (await db.get_progression(U))["prestige"] == 0


async def test_weekly_progress_is_measured_from_a_baseline():
    """A lifetime counter becomes a weekly one by subtracting where you started
    — which is what lets the objectives exist with no hooks anywhere."""
    await db.add_coins(U, 100_000)
    first = await P.weekly(U)
    # The baseline was snapshotted at the current value, so nothing is done yet
    # purely because the lifetime number is already high.
    assert all(
        obj["progress"] == 0 or obj["done"] is False for obj in first["objectives"]
    )


# ══════════════════════════════════════════════════════════════════════════════
#  The wardrobe
# ══════════════════════════════════════════════════════════════════════════════
async def test_wardrobe_lists_the_whole_catalogue_with_how_to_get_each():
    state = await I.state(U, G)
    assert state["cosmetics"]
    assert state["slots"]
    for row in state["cosmetics"]:
        assert row["unlock"], f"{row['key']} doesn't say how to get it"


async def test_you_cannot_equip_something_you_do_not_own():
    state = await I.state(U, G)
    locked = next(c for c in state["cosmetics"] if not c["owned"])
    result = await I.equip(U, {locked["slot"]: [locked["key"]]})
    assert result["changed"] == []
    assert result["refused"]
    assert (
        locked["key"] in result["refused"][0]["why"]
        or "own" in result["refused"][0]["why"]
    )


async def test_equipping_writes_only_the_slots_that_changed():
    state = await I.state(U, G)
    owned = [c for c in state["cosmetics"] if c["owned"]]
    if not owned:
        pytest.skip("nothing is owned by default")
    pick = owned[0]
    first = await I.equip(U, {pick["slot"]: [pick["key"]]})
    assert pick["slot"] in first["changed"] or not first["changed"]
    # Applying the same loadout again changes nothing.
    again = await I.equip(U, {pick["slot"]: [pick["key"]]})
    assert again["changed"] == []


async def test_a_badge_slot_refuses_more_than_it_holds():
    from utils import cosmetics

    badges = [c for c in cosmetics.COSMETICS.values() if c.slot == "badge"]
    limit = cosmetics.SLOTS["badge"].max_equipped
    if len(badges) <= limit:
        pytest.skip("not enough badges to overfill the slot")
    for badge in badges[: limit + 2]:
        await db.unlock_cosmetic(U, badge.key)

    result = await I.equip(U, {"badge": [b.key for b in badges[: limit + 2]]})
    assert len(result["equipped"]["badge"]) == limit
    assert result["refused"]


async def test_a_cosmetic_in_the_wrong_slot_is_refused():
    from utils import cosmetics

    banner = next(c for c in cosmetics.COSMETICS.values() if c.slot == "banner")
    await db.unlock_cosmetic(U, banner.key)
    result = await I.equip(U, {"badge": [banner.key]})
    assert result["refused"]
    assert not result["equipped"].get("badge")


async def test_an_unknown_slot_is_an_error_not_a_silent_no_op():
    with pytest.raises(Exception):
        await I.equip(U, {"hat": ["anything"]})


async def test_buying_a_cosmetic_charges_once():
    from utils import cosmetics

    purchasable = [
        c
        for c in cosmetics.COSMETICS.values()
        if (c.unlock or {}).get("kind") == "purchase"
    ]
    if not purchasable:
        pytest.skip("nothing is for sale")
    item = purchasable[0]
    await db.add_coins(U, item.price * 2)

    result = await I.unlock(U, G, item.key)
    assert result["spent"] == item.price
    assert await db.get_balance(U) == item.price

    with pytest.raises(PlayError) as caught:
        await I.unlock(U, G, item.key)
    assert caught.value.code == "owned"
    assert await db.get_balance(U) == item.price  # not charged again


async def test_an_earned_cosmetic_can_never_be_bought():
    from utils import cosmetics

    earned = next(
        c
        for c in cosmetics.COSMETICS.values()
        if (c.unlock or {}).get("kind") != "purchase"
    )
    await db.add_coins(U, 10_000_000)
    with pytest.raises(PlayError) as caught:
        await I.unlock(U, G, earned.key)
    assert caught.value.code == "not_for_sale"


# ══════════════════════════════════════════════════════════════════════════════
#  Analytics
# ══════════════════════════════════════════════════════════════════════════════
async def test_analytics_reads_on_an_empty_server():
    data = await A.overview(G, [], "30d")
    assert data["economy"]["total_coins"] == 0
    assert data["economy"]["holders"] == 0
    assert data["charts"]["shop_spend"]["points"]
    assert data["notes"]


async def test_wealth_distribution_counts_every_holder_once():
    for i, coins in enumerate((50, 500, 5_000, 50_000)):
        await db.add_coins(U + i, coins)
    data = await A.overview(G, [U, U + 1, U + 2, U + 3], "30d")
    assert data["economy"]["holders"] == 4
    assert sum(band["value"] for band in data["economy"]["distribution"]) == 4


async def test_every_range_produces_the_right_number_of_buckets():
    for key, spec in A.RANGES.items():
        data = await A.overview(G, [], key)
        assert len(data["charts"]["shop_spend"]["points"]) == spec["buckets"]
        assert data["range"]["key"] == key


async def test_an_unknown_range_falls_back_rather_than_failing():
    data = await A.overview(G, [], "nonsense")
    assert data["range"]["key"] == A.DEFAULT_RANGE


async def test_the_faucet_figures_come_from_the_balance_model():
    """If these ever stop agreeing with the game, the shop advice they inform
    is wrong — so they're recomputed, never typed."""
    from cogs.activities.helpers import adventure_coins_per_day
    from cogs.economy.helpers import coins_per_hour

    data = await A.overview(G, [], "7d")
    values = {f["key"]: f["value"] for f in data["economy"]["faucets"]}
    assert values["fishing"] == round(coins_per_hour())
    assert values["adventure"] == round(adventure_coins_per_day())


# ══════════════════════════════════════════════════════════════════════════════
#  Moderation framework
# ══════════════════════════════════════════════════════════════════════════════
def test_every_action_names_a_permission_that_exists():
    from web import permissions as perms

    for key, spec in mod.ACTIONS.items():
        assert spec["permission"] in perms.PERMISSION_LABELS, key
        assert spec["permission_name"] == perms.PERMISSION_LABELS[spec["permission"]]
        assert spec["blurb"]


def test_describe_reports_both_halves_of_the_check():
    from web import permissions as perms

    # The admin can, the bot can't.
    rows = {r["key"]: r for r in mod.describe(0, perms.ADMINISTRATOR)}
    assert rows["ban"]["you_can"] is True
    assert rows["ban"]["bot_can"] is False
    assert rows["ban"]["available"] is False
    assert "NanoBot" in rows["ban"]["why_not"]

    # And the other way around.
    rows = {r["key"]: r for r in mod.describe(perms.ADMINISTRATOR, 0)}
    assert rows["ban"]["you_can"] is False
    assert "You need" in rows["ban"]["why_not"]


def test_a_reason_is_required_and_bounded():
    with pytest.raises(Exception):
        mod.check_reason("")
    with pytest.raises(Exception):
        mod.check_reason("no")
    with pytest.raises(Exception):
        mod.check_reason("x" * (mod.REASON_MAX + 1))
    assert mod.check_reason("  spamming  ") == "spamming"


def test_the_audit_reason_says_where_it_came_from():
    """During an incident, *where* an action came from is a different question
    from who took it, and the audit log has to answer both."""
    line = mod.audit_reason("Ada#0001", "spamming")
    assert "dashboard" in line.lower()
    assert "Ada" in line and "spamming" in line
    assert len(mod.audit_reason("A" * 600, "B" * 600)) <= 512


def test_destructive_actions_are_marked_as_such():
    assert mod.ACTIONS["ban"]["destructive"] is True
    assert mod.ACTIONS["kick"]["destructive"] is True
    assert mod.ACTIONS["purge"]["destructive"] is True
    assert mod.ACTIONS["purge"]["bulk"] is True
    # Warn isn't: it records something, it doesn't remove anybody.
    assert mod.ACTIONS["warn"]["destructive"] is False
    assert mod.ACTIONS["unban"]["destructive"] is False


def test_the_limits_are_discords_own():
    assert mod.MAX_PURGE == 100
    assert mod.MAX_TIMEOUT_SECONDS == 28 * 86400
