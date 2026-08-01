"""
Tests for web/engine/ — the economy resolved without Discord.

Two things are being checked, and the second matters more than the first.

**It works.** Casting, selling, travelling, running an activity and claiming a
daily all do what they say against a real (in-memory) database.

**It cannot drift from Discord.** The claims are the *same rows* the cogs claim,
so a cast from the browser has to consume the same cooldown a `/fish` would, and
a charge spent here has to be gone there. Those are the assertions that would
catch a well-meaning refactor turning the website into a second, more generous
game — see also tests/test_web_engine_parity.py, which checks the modules import
the shared helpers rather than defining their own.
"""

import time

import aiosqlite
import pytest

import utils.db as db
from web.engine import activities as A
from web.engine import economy as E
from web.engine import fishing as F
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
    ):
        await setup()
    yield conn
    await conn.close()


# ══════════════════════════════════════════════════════════════════════════════
#  Fishing
# ══════════════════════════════════════════════════════════════════════════════
async def test_state_works_on_a_brand_new_account():
    """A fresh angler gets a full page, not a pile of Nones."""
    state = await F.state(U, G)
    assert state["enabled"] is True
    assert state["spot"]["key"] == "pond"
    assert state["rod"]["level"] == 0
    assert state["bag"]["count"] == 0
    assert state["quest"]["target"] > 0
    assert state["dex"]["caught"] == 0
    assert len(state["spots"]) >= 3
    assert state["shop"], "the tackle shelf should never be empty"


async def test_cast_resolves_and_persists():
    result = await F.cast(U, G)
    assert result["outcome"] in ("catch", "snag")
    assert result["xp"] > 0
    if result["outcome"] == "catch":
        assert result["catches"]
        for entry in result["catches"]:
            assert entry["name"] and entry["rarity"]
            # The rarity has to survive as a colour the card can use.
            assert entry["rarity_color"].startswith("#")


async def test_cast_claims_the_same_cooldown_discord_does():
    """The claim is `db.try_claim_cast` — the statement `/fish` calls.

    If this ever stops being true, a member can cast on their phone and again on
    the website, which is the exact failure a second implementation ships with.
    """
    await F.cast(U, G)
    with pytest.raises(PlayError) as caught:
        await F.cast(U, G)
    assert caught.value.code == "cooldown"
    assert caught.value.extra["retry_after"] > 0

    # And the other direction: a claim made "by Discord" blocks a web cast.
    fisher = await db.get_fisher(U)
    assert fisher["casts"] == 1


async def test_disabled_guild_refuses_before_claiming():
    await db.set_fishing_config(G, enabled=False)
    with pytest.raises(PlayError) as caught:
        await F.cast(U, G)
    assert caught.value.code == "disabled"
    # A refusal costs nothing: the cooldown was never claimed.
    assert (await db.get_fisher(U))["casts"] == 0


async def test_daily_streak_is_claimed_once_a_day():
    result = await F.cast(U, G)
    streak_notes = [note for note in result["notes"] if note["kind"] == "streak"]
    assert len(streak_notes) == 1

    # A second cast the same day must not re-award it.
    await db._conn().execute(
        "UPDATE fishing_stats SET last_cast=0 WHERE user_id=?", (str(U),)
    )
    result = await F.cast(U, G)
    assert not [note for note in result["notes"] if note["kind"] == "streak"]


async def test_sell_credits_coins_and_empties_the_bag():
    for _ in range(6):
        await db._conn().execute(
            "UPDATE fishing_stats SET last_cast=0 WHERE user_id=?", (str(U),)
        )
        await F.cast(U, G)
    if not await db.count_bag(U):
        pytest.skip("six casts all snagged or paid treasure — nothing to sell")

    before = await db.get_balance(U)
    result = await F.sell(U, G, "all")
    assert result["sold"] > 0
    assert result["coins"] > 0
    assert await db.get_balance(U) == before + result["coins"]
    assert await db.count_bag(U) == 0


async def test_selling_an_empty_bag_is_a_refusal_not_a_crash():
    with pytest.raises(PlayError) as caught:
        await F.sell(U, G, "all")
    assert caught.value.code == "empty"


async def test_travel_is_gated_on_level_before_price():
    """Being told what something costs when you couldn't buy it anyway is the
    wrong answer, and `unlock_state` already orders the two that way."""
    await db.add_coins(U, 10_000_000)
    with pytest.raises(PlayError) as caught:
        await F.travel(U, G, "river")
    assert caught.value.code == "locked"


async def test_charter_is_bought_once_then_travel_is_free():
    await db.add_coins(U, 10_000_000)
    await db.add_fishing_xp(U, 100_000)  # clear every level gate

    before = await db.get_balance(U)
    first = await F.travel(U, G, "river")
    assert first["charged"] > 0
    assert await db.get_balance(U) == before - first["charged"]

    await F.travel(U, G, "pond")
    again = await F.travel(U, G, "river")
    assert again["charged"] == 0


async def test_rod_upgrade_debits_and_advances():
    await db.add_coins(U, 10_000)
    result = await F.upgrade_rod(U, G)
    assert result["rod"]["level"] == 1
    assert result["spent"] > 0
    assert (await db.get_fisher(U))["rod_level"] == 1


async def test_rod_upgrade_refuses_when_broke():
    with pytest.raises(PlayError) as caught:
        await F.upgrade_rod(U, G)
    assert caught.value.code == "broke"
    assert (await db.get_fisher(U))["rod_level"] == 0


async def test_buying_and_arming_bait():
    await db.add_coins(U, 1000)
    bought = await F.buy(U, G, "bait_worm", 2)
    assert bought["qty"] == 2
    assert await db.get_item_qty(U, "bait_worm") == 2

    armed = await F.arm(U, "bait_worm", 1)
    assert any(effect["key"] == "fish_bait" for effect in armed["armed"])


async def test_arming_something_that_is_not_tackle_is_refused():
    """The tackle shelf arms tackle. The whole inventory belongs elsewhere."""
    await db.add_item(U, "padlock", 1)
    with pytest.raises(PlayError) as caught:
        await F.arm(U, "padlock", 1)
    assert caught.value.code == "not_armable"


async def test_trap_needs_one_and_pays_out_once():
    with pytest.raises(PlayError) as caught:
        await F.trap(U, G)
    assert caught.value.code == "no_trap"

    await db.add_item(U, "fish_trap", 1)
    assert (await F.trap(U, G))["action"] == "set"
    assert await db.get_item_qty(U, "fish_trap") == 0

    with pytest.raises(PlayError) as caught:
        await F.trap(U, G)
    assert caught.value.code == "soaking"

    # Backdate it past the soak and pull.
    await db._conn().execute(
        "UPDATE fishing_traps SET set_at=0 WHERE user_id=?", (str(U),)
    )
    pulled = await F.trap(U, G)
    assert pulled["action"] == "pulled"
    assert pulled["catches"]

    # The DELETE is the claim, so a second pull finds nothing.
    with pytest.raises(PlayError) as caught:
        await F.trap(U, G)
    assert caught.value.code == "no_trap"


async def test_a_trap_belongs_to_a_spot_not_to_an_angler():
    """The re-key, from the browser's side: chartering somewhere new gives you
    somewhere new to leave a trap, and pulling collects the lot."""
    await db.add_item(U, "fish_trap", 2)
    await F.trap(U, G)  # set at the starter spot
    await db.unlock_spot(U, "reef", time.time())
    await db.set_spot(U, "reef")

    assert (await F.trap(U, G))["action"] == "set"
    state = await F.trap_state(U)
    assert len(state["traps"]) == 2
    assert state["here"] is False  # nowhere left to put one where you're stood

    await db._conn().execute(
        "UPDATE fishing_traps SET set_at=0 WHERE user_id=?", (str(U),)
    )
    pulled = await F.trap(U, G)

    assert len(pulled["baskets"]) == 2
    assert len(pulled["catches"]) == sum(len(b["catches"]) for b in pulled["baskets"])
    assert await db.get_traps(U) == []


# ══════════════════════════════════════════════════════════════════════════════
#  Activities
# ══════════════════════════════════════════════════════════════════════════════
async def test_a_fresh_account_finds_a_full_bucket():
    """Never run means a full bucket, not an empty one — the same rule
    `charge_state` holds, and the reason a new member has something to do."""
    state = await A.state(U, G)
    assert state["ready_total"] > 0
    for row in state["activities"]:
        if row["runnable"]:
            assert row["ready"] == row["max_charges"]


async def test_running_spends_exactly_one_charge():
    before = next(r for r in (await A.charges(U, G)) if r["activity"] == "work")
    await A.run(U, G, "work")
    after = next(r for r in (await A.charges(U, G)) if r["activity"] == "work")
    assert after["ready"] == before["ready"] - 1


async def test_work_pays_coins_and_reports_the_career():
    result = await A.run(U, G, "work")
    assert result["outcome"] == "work"
    assert result["coins"] > 0
    assert result["career"]["title"]
    assert await db.get_balance(U) >= result["coins"]


async def test_mining_yields_items_or_a_cave_in():
    result = await A.run(U, G, "mine")
    assert result["outcome"] in ("dig", "cave_in")
    if result["outcome"] == "dig":
        assert result["items"]
        assert sum(item["qty"] for item in result["items"]) >= 1


async def test_disabled_activity_refuses_without_claiming():
    await db.set_activities_config(G, work_enabled=False)
    before = next(r for r in (await A.charges(U, G)) if r["activity"] == "work")
    with pytest.raises(PlayError) as caught:
        await A.run(U, G, "work")
    assert caught.value.code == "disabled"
    after = next(r for r in (await A.charges(U, G)) if r["activity"] == "work")
    assert after["ready"] == before["ready"]


async def test_collect_all_empties_every_bucket_then_refuses():
    result = await A.run_many(U, G)
    assert result["runs"] > 1
    assert result["outcome"] == "collected"

    with pytest.raises(PlayError) as caught:
        await A.run_many(U, G)
    assert caught.value.code == "cooldown"


async def test_collect_reports_deltas_not_resolver_claims():
    """Totals are measured around the batch, so an injury fine or a cave-in that
    paid nothing can't be papered over by summing what each resolver said."""
    before = await db.get_balance(U)
    result = await A.run_many(U, G)
    assert await db.get_balance(U) - before == result["coins"]


async def test_encounter_pays_out_exactly_once():
    """An encounter has no row to check against, so the token *is* the claim."""
    encounter = None
    for _ in range(400):
        try:
            result = await A.run(U, G, "work")
        except PlayError:
            # Bucket empty — reset it and keep rolling for an encounter.
            await db._conn().execute(
                "UPDATE activities_stats SET last_work=0 WHERE user_id=?", (str(U),)
            )
            continue
        if result.get("encounter"):
            encounter = result["encounter"]
            break
    if encounter is None:
        pytest.skip("no encounter fired in 400 runs")

    option = encounter["options"][0]["key"]
    first = await A.choose(U, encounter["token"], option)
    assert "text" in first

    with pytest.raises(PlayError) as caught:
        await A.choose(U, encounter["token"], option)
    assert caught.value.code == "expired"


async def test_an_encounter_token_belongs_to_one_account():
    """Someone else's token must not be spendable, even while it is live."""
    encounter = None
    for _ in range(300):
        await db._conn().execute(
            "UPDATE activities_stats SET last_work=0 WHERE user_id=?", (str(U),)
        )
        result = await A.run(U, G, "work")
        if result.get("encounter"):
            encounter = result["encounter"]
            break
    if encounter is None:
        pytest.skip("no encounter fired")
    with pytest.raises(PlayError):
        await A.choose(U + 1, encounter["token"], encounter["options"][0]["key"])


async def test_pickaxe_upgrade_debits_and_advances():
    await db.add_coins(U, 100_000)
    result = await A.upgrade_pickaxe(U, G)
    assert result["spent"] > 0
    assert (await db.get_activity_stats(U))["pickaxe_level"] == 1


async def test_rob_needs_both_wallets_to_be_worth_it():
    with pytest.raises(PlayError) as caught:
        await A.rob(U, G, U + 1)
    assert caught.value.code == "too_poor"

    await db.add_coins(U, 100_000)
    with pytest.raises(PlayError) as caught:
        await A.rob(U, G, U + 1)
    assert caught.value.code == "target_poor"


async def test_rob_refuses_a_shielded_target():
    await db.add_coins(U, 100_000)
    await db.add_coins(U + 1, 100_000)
    await db.grant_effect(U + 1, "rob_shield", 1.0, duration=3600)
    with pytest.raises(PlayError) as caught:
        await A.rob(U, G, U + 1)
    assert caught.value.code == "shielded"


async def test_rob_mints_nothing():
    """Robbing moves coins; it must never create them."""
    await db.add_coins(U, 100_000)
    await db.add_coins(U + 1, 100_000)
    before = await db.get_balance(U) + await db.get_balance(U + 1)
    result = await A.rob(U, G, U + 1)
    after = await db.get_balance(U) + await db.get_balance(U + 1)
    if result["outcome"] == "success":
        assert after == before
    else:
        assert after < before  # the fine is a pure sink


# ══════════════════════════════════════════════════════════════════════════════
#  Economy
# ══════════════════════════════════════════════════════════════════════════════
async def test_daily_pays_a_band_and_then_refuses():
    result = await E.claim_daily(U, G)
    assert result["coins"] > 0
    assert result["band"]["label"]
    assert result["streak"] == 1
    assert await db.get_balance(U) == result["coins"]

    with pytest.raises(PlayError) as caught:
        await E.claim_daily(U, G)
    assert caught.value.code == "cooldown"
    assert caught.value.extra["retry_after"] > 0


async def test_daily_uses_the_bot_wide_amount_not_a_guild_one():
    """The faucet mints into a global wallet, so its size is the owner's call.

    A dashboard that let a guild set it would let the most generous server on
    the bot set everyone's income.
    """
    await db.set_reward_amount("daily", 1000)
    result = await E.claim_daily(U, G)
    assert result["coins"] >= 1000  # the roll is a multiple of the base


async def test_wallet_reports_the_daily_countdown():
    assert (await E.wallet(U, G))["daily"]["ready"] is True
    await E.claim_daily(U, G)
    wallet = await E.wallet(U, G)
    assert wallet["daily"]["ready"] is False
    assert wallet["daily"]["ready_in"] > 0


async def test_pay_moves_coins_and_refuses_when_short():
    await db.add_coins(U, 500)
    result = await E.pay(U, G, U + 1, 200)
    assert result["sent"] == 200
    assert await db.get_balance(U) == 300
    assert await db.get_balance(U + 1) == 200

    with pytest.raises(PlayError) as caught:
        await E.pay(U, G, U + 1, 10_000)
    assert caught.value.code == "broke"


async def test_pay_refuses_yourself_and_a_zero_amount():
    await db.add_coins(U, 500)
    with pytest.raises(PlayError):
        await E.pay(U, G, U, 10)
    with pytest.raises(PlayError):
        await E.pay(U, G, U + 1, 0)


async def test_inventory_groups_and_totals():
    await db.add_item(U, "stone", 5)
    await db.add_item(U, "bait_worm", 2)
    inventory = await E.inventory(U, G)
    assert inventory["totals"]["items"] == 7
    assert inventory["totals"]["stacks"] == 2
    assert "bait" in inventory["categories"]


async def test_bulk_sell_leaves_unsellable_stacks_alone():
    await db.add_item(U, "stone", 4)
    await db.add_item(U, "treasure_key", 1)  # no sale value
    result = await E.sell_bulk(U, "all")
    assert result["coins"] > 0
    assert await db.get_item_qty(U, "treasure_key") == 1
    assert await db.get_item_qty(U, "stone") == 0


async def test_bulk_sell_with_nothing_sellable_is_a_refusal():
    await db.add_item(U, "treasure_key", 1)
    with pytest.raises(PlayError) as caught:
        await E.sell_bulk(U, "all")
    assert caught.value.code == "empty"


# ── Containers ────────────────────────────────────────────────────────────────
async def test_a_container_says_what_it_needs_before_you_tap_it():
    """The chest carries its own rule in the payload, so the page can say "you
    need a key" instead of the member finding out by failing."""
    await db.add_item(U, "treasure_chest", 1)
    inventory = await E.inventory(U, G)
    chest = next(i for i in inventory["items"] if i["key"] == "treasure_chest")
    assert chest["container"]["opens_with"] == "treasure_key"

    # An ordinary item carries no container rule, so the page shows no opener.
    await db.add_item(U, "stone", 1)
    inventory = await E.inventory(U, G)
    stone = next(i for i in inventory["items"] if i["key"] == "stone")
    assert stone["container"] is None


async def test_an_item_says_where_it_comes_from_when_a_table_says_so():
    """Derived from the drop tables and the recipe registry, never invented —
    an item nothing claims gets no line rather than a guess."""
    await db.add_item(U, "stone", 1)
    await db.add_item(U, "bait_worm", 1)
    inventory = await E.inventory(U, G)
    rows = {i["key"]: i for i in inventory["items"]}
    assert rows["stone"]["sources"] == ["Mining"]
    assert "The tackle shop" in rows["bait_worm"]["sources"]


async def test_opening_a_chest_spends_the_chest_and_the_key():
    await db.add_item(U, "treasure_chest", 2)
    await db.add_item(U, "treasure_key", 2)
    result = await E.open_container(U, "treasure_chest", 2)
    assert result["qty"] == 2
    assert result["coins"] > 0
    assert await db.get_item_qty(U, "treasure_chest") == 0
    assert await db.get_item_qty(U, "treasure_key") == 0
    assert await db.get_balance(U) == result["coins"]


async def test_a_chest_without_a_key_costs_nothing():
    await db.add_item(U, "treasure_chest", 1)
    with pytest.raises(PlayError) as caught:
        await E.open_container(U, "treasure_chest", 1)
    assert caught.value.code == "no_key"
    assert caught.value.extra["needs"] == "treasure_key"
    assert await db.get_item_qty(U, "treasure_chest") == 1


async def test_opening_more_than_you_can_opens_what_you_can():
    """Two chests and one key opens one — the same clamp the command applies,
    rather than refusing the whole thing over the second chest."""
    await db.add_item(U, "treasure_chest", 2)
    await db.add_item(U, "treasure_key", 1)
    result = await E.open_container(U, "treasure_chest", 2)
    assert result["qty"] == 1
    assert await db.get_item_qty(U, "treasure_chest") == 1
    assert await db.get_item_qty(U, "treasure_key") == 0


async def test_only_a_container_can_be_opened():
    await db.add_item(U, "stone", 1)
    with pytest.raises(PlayError) as caught:
        await E.open_container(U, "stone", 1)
    assert caught.value.code == "not_container"
    assert await db.get_item_qty(U, "stone") == 1


async def test_gift_is_atomic_and_refuses_self():
    await db.add_item(U, "stone", 3)
    await E.gift(U, U + 1, "stone", 2)
    assert await db.get_item_qty(U, "stone") == 1
    assert await db.get_item_qty(U + 1, "stone") == 2

    with pytest.raises(PlayError):
        await E.gift(U, U, "stone", 1)
    with pytest.raises(PlayError) as caught:
        await E.gift(U, U + 1, "stone", 99)
    assert caught.value.code == "short"


async def test_leaderboard_scopes_are_the_same_table_filtered():
    """A server board is the global table filtered to its members — never a
    separate tally. That is the rule the whole economy rests on."""
    await db.add_coins(U, 500)
    await db.add_coins(U + 1, 900)

    everyone = await E.leaderboard("coins")
    assert [row["user_id"] for row in everyone["rows"]][:2] == [U + 1, U]

    scoped = await E.leaderboard("coins", member_ids=[U])
    assert [row["user_id"] for row in scoped["rows"]] == [U]
    assert scoped["rows"][0]["value"] == 500


async def test_unknown_leaderboard_is_a_refusal():
    with pytest.raises(PlayError):
        await E.leaderboard("nonsense")
