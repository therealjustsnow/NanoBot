"""
Command-level tests for cogs/fishing/ under dpytest (parse → check → DB → reply).
"""

import time
import types

import pytest
from discord.ext import commands
from discord.ext import test as dpytest

import utils.db as db
from cogs.fishing import FISH, RODS
from cogs.fishing.constants import NET_CATCHES, TRAP_SOAK
from cogs.fishing.items import FISH_TRAP
from cogs.fishing.spots import DEFAULT_SPOT, SPOTS, SPOT_ORDER
from tests.conftest import config, grant_perms


@pytest.mark.cogs("cogs.fishing")
async def test_cast_catches_and_blocks_second_cast(bot, monkeypatch):
    from cogs.fishing import cog as fishing

    guild = config().guilds[0]
    author = config().members[0]
    # roll 0.5 → common tier, first species, minimum weight.
    monkeypatch.setattr(fishing.random, "random", lambda: 0.5)

    await dpytest.message("!fish", member=author)
    sent = dpytest.get_message()
    assert sent.embeds
    bag = await db.get_bag(author.id)
    assert len(bag) == 1
    assert FISH[bag[0]["fish_key"]]["rarity"] == "common"

    # A second cast inside the default 60s cooldown is refused, nothing caught.
    await dpytest.message("!fish cast", member=author)
    sent = dpytest.get_message()
    assert "Not Yet" in sent.embeds[0].title
    assert sum(r["qty"] for r in await db.get_bag(author.id)) == 1


@pytest.mark.cogs("cogs.fishing")
async def test_cast_refused_when_disabled(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.set_fishing_config(guild.id, enabled=False)

    await dpytest.message("!fish", member=author)
    sent = dpytest.get_message()
    assert "disabled" in sent.embeds[0].description
    assert await db.get_bag(author.id) == []


@pytest.mark.cogs("cogs.fishing")
async def test_sell_credits_coins_and_empties_bag(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.record_catch(author.id, "salmon", 5.0, 40)
    await db.record_catch(author.id, "boot", 0.5, 1)

    await dpytest.message("!fish sell", member=author)
    sent = dpytest.get_message()
    assert "Sold" in sent.embeds[0].title
    assert await db.get_balance(author.id) == 41
    assert await db.get_bag(author.id) == []
    assert (await db.get_fisher(author.id))["earned"] == 41


@pytest.mark.cogs("cogs.fishing")
async def test_sell_unknown_fish(bot):
    author = config().members[0]
    await dpytest.message("!fish sell kraken", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title


@pytest.mark.cogs("cogs.fishing")
async def test_upgrade_charges_coins_and_advances_rod(bot):
    guild = config().guilds[0]
    author = config().members[0]
    price = RODS[1]["price"]
    await db.add_coins(author.id, price + 100)

    await dpytest.message("!fish upgrade", member=author)
    sent = dpytest.get_message()
    assert "Upgraded" in sent.embeds[0].title
    assert (await db.get_fisher(author.id))["rod_level"] == 1
    assert await db.get_balance(author.id) == 100


@pytest.mark.cogs("cogs.fishing")
async def test_upgrade_insufficient_funds(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(author.id, 10)

    await dpytest.message("!fish upgrade", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert (await db.get_fisher(author.id))["rod_level"] == 0
    assert await db.get_balance(author.id) == 10


@pytest.mark.cogs("cogs.fishing")
async def test_toggle_denied_without_manage_guild(bot):
    author = config().members[0]
    with pytest.raises(commands.MissingPermissions):
        await dpytest.message("!fish toggle", member=author)


@pytest.mark.cogs("cogs.fishing")
async def test_toggle_flips_config_with_perms(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await grant_perms(author, manage_guild=True)

    await dpytest.message("!fish toggle", member=author)
    sent = dpytest.get_message()
    assert "disabled" in sent.embeds[0].description
    assert (await db.get_fishing_config(guild.id))["enabled"] is False

    await dpytest.message("!fish toggle", member=author)
    sent = dpytest.get_message()
    assert "enabled" in sent.embeds[0].description
    assert (await db.get_fishing_config(guild.id))["enabled"] is True


# ── /fish buy ────────────────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.fishing")
async def test_buy_bait_charges_coins_and_grants_item(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(author.id, 100)

    await dpytest.message("!fish buy Worm", member=author)
    sent = dpytest.get_message()
    assert "Bought" in sent.embeds[0].title
    assert await db.get_balance(author.id) == 75  # 100 - 25
    assert await db.get_item_qty(author.id, "bait_worm") == 1


@pytest.mark.cogs("cogs.fishing")
async def test_buy_bait_quantity_multiplies_cost(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(author.id, 100)

    await dpytest.message("!fish buy Worm 3", member=author)
    sent = dpytest.get_message()
    assert "Bought" in sent.embeds[0].title
    assert await db.get_balance(author.id) == 25  # 100 - 3*25
    assert await db.get_item_qty(author.id, "bait_worm") == 3


@pytest.mark.cogs("cogs.fishing")
async def test_buy_insufficient_funds(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(author.id, 10)

    await dpytest.message("!fish buy Worm", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert await db.get_balance(author.id) == 10
    assert await db.get_item_qty(author.id, "bait_worm") == 0


@pytest.mark.cogs("cogs.fishing")
async def test_buy_rejects_non_shop_item(bot):
    author = config().members[0]
    # A generic item that isn't bait_*/fish_* (or has no price) isn't sold here.
    await dpytest.message("!fish buy treasure_key", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title


@pytest.mark.cogs("cogs.fishing")
async def test_buy_unknown_item(bot):
    author = config().members[0]
    await dpytest.message("!fish buy nonexistent_thing", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title


# ── /fish bait ───────────────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.fishing")
async def test_bait_shows_owned_and_armed(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_item(author.id, "bait_worm", 2)
    await db.grant_effect(author.id, "fish_bait", 0.05, uses=5)

    await dpytest.message("!fish bait", member=author)
    sent = dpytest.get_message()
    embed = sent.embeds[0]
    assert any("Worm" in f.value for f in embed.fields)
    assert any("armed" in f.value.lower() for f in embed.fields)


@pytest.mark.cogs("cogs.fishing")
async def test_bait_empty_state(bot):
    author = config().members[0]
    await dpytest.message("!fish bait", member=author)
    sent = dpytest.get_message()
    assert "don't have any bait" in sent.embeds[0].description


# ── /fish cast bait consumption ──────────────────────────────────────────────────
@pytest.mark.cogs("cogs.fishing")
async def test_cast_consumes_a_bait_charge(bot, monkeypatch):
    from cogs.fishing import cog as fishing

    guild = config().guilds[0]
    author = config().members[0]
    monkeypatch.setattr(fishing.random, "random", lambda: 0.5)
    await db.grant_effect(author.id, "fish_bait", 0.05, uses=2)

    await dpytest.message("!fish", member=author)
    dpytest.get_message()

    effects = await db.get_active_effects(author.id)
    assert effects["fish_bait"]["uses_left"] == 1


# ── /fish quest ──────────────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.fishing")
async def test_quest_shows_todays_quest(bot):
    author = config().members[0]
    await dpytest.message("!fish quest", member=author)
    sent = dpytest.get_message()
    assert "Quest" in sent.embeds[0].title


# ── /fish events ─────────────────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.fishing")
async def test_events_empty_state(bot):
    author = config().members[0]
    await dpytest.message("!fish events", member=author)
    sent = dpytest.get_message()
    assert "No fishing events" in sent.embeds[0].description


@pytest.mark.cogs("cogs.fishing")
async def test_event_force_start_is_owner_only(bot):
    """A frenzy doubles catch *value*, so starting one turns up the coin faucet
    — and those coins spend in every server, not just this one. Manage Server
    isn't enough; mods keep /fish toggle."""
    author = config().members[0]
    await grant_perms(author, manage_guild=True)
    bot.owner_id = author.id + 1
    with pytest.raises(commands.NotOwner):
        await dpytest.message("!fish event frenzy", member=author)
    assert await db.get_active_events(config().guilds[0].id) == []


@pytest.mark.cogs("cogs.fishing")
async def test_event_force_start_and_list(bot):
    guild = config().guilds[0]
    author = config().members[0]
    bot.owner_id = author.id

    await dpytest.message("!fish event frenzy 5", member=author)
    sent = dpytest.get_message()
    assert "Feeding Frenzy" in sent.embeds[0].description

    events = await db.get_active_events(guild.id)
    assert len(events) == 1
    assert events[0]["event_key"] == "frenzy"

    await dpytest.message("!fish events", member=author)
    sent = dpytest.get_message()
    assert "Feeding Frenzy" in sent.embeds[0].description


@pytest.mark.cogs("cogs.fishing")
async def test_event_force_start_unknown_key(bot):
    author = config().members[0]
    bot.owner_id = author.id
    await dpytest.message("!fish event nonsense", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title


# ── /fish global ─────────────────────────────────────────────────────────────────
# Note: no empty-state test here — the dpytest `bot` fixture opens the real
# on-disk DB (only `db._db` is redirected for in-memory db-layer tests;
# `db._DB_PATH` isn't), so a cross-guild aggregate can carry rows left behind
# by any earlier dpytest test in the same pytest session. Per-guild commands
# don't see that (they always filter by this test's freshly-generated guild
# id), but /fish global deliberately spans every guild — so we only assert its
# happy path here, and cover the true empty/whitelist behavior at the db layer
# (test_fishing_db.py, on an isolated in-memory connection) instead.
@pytest.mark.cogs("cogs.fishing")
async def test_global_leaderboard_shows_earner(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.record_catch(author.id, "salmon", 5.0, 40)
    await dpytest.message("!fish sell", member=author)
    dpytest.get_message()

    await dpytest.message("!fish global earned", member=author)
    sent = dpytest.get_message()
    assert "Global" in sent.embeds[0].title


@pytest.mark.cogs("cogs.fishing")
async def test_global_leaderboard_unknown_stat(bot):
    author = config().members[0]
    await dpytest.message("!fish global bogus_stat", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title


# ── /fish stats shows level ───────────────────────────────────────────────────────
@pytest.mark.cogs("cogs.fishing")
async def test_stats_shows_level_field(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_fishing_xp(author.id, 30)

    await dpytest.message("!fish stats", member=author)
    sent = dpytest.get_message()
    embed = sent.embeds[0]
    assert any(f.name == "Level" for f in embed.fields)


# ══════════════════════════════════════════════════════════════════════════════
#  The hub, the map, the shop and traps
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.cogs("cogs.fishing")
async def test_bare_fish_still_casts(bot):
    """Guarded on purpose. /fish is the most-typed command in the bot, and the
    dashboard lives at /fish hub precisely so this could stay a cast."""
    author = config().members[0]
    await dpytest.message("!fish", member=author)
    dpytest.get_message()
    assert (await db.get_fisher(author.id))["casts"] == 1


@pytest.mark.cogs("cogs.fishing")
async def test_hub_shows_the_angler_at_a_glance(bot):
    author = config().members[0]
    await dpytest.message("!fish hub", member=author)
    embed = dpytest.get_message().embeds[0]

    assert "Fishing" in embed.title
    assert "Old Pond" in embed.description  # where you start
    names = {f.name for f in embed.fields}
    assert {"Angler", "Bag", "Streak", "Today's quest"} <= names


@pytest.mark.cogs("cogs.fishing")
async def test_hub_reports_a_soaking_trap(bot):
    author = config().members[0]
    await db.set_trap(author.id, "reef", time.time())

    await dpytest.message("!fish hub", member=author)
    fields = {f.name: f.value for f in dpytest.get_message().embeds[0].fields}

    assert "Trap" in fields
    assert "Coral Reef" in fields["Trap"]


@pytest.mark.cogs("cogs.fishing")
async def test_travel_lists_every_spot_with_what_it_takes(bot):
    author = config().members[0]
    await dpytest.message("!fish travel", member=author)
    embed = dpytest.get_message().embeds[0]

    names = {f.name for f in embed.fields}
    for spot in SPOT_ORDER:
        info = SPOTS[spot]
        assert f"{info['emoji']} {info['name']}" in names
    # A locked spot says what it needs rather than just refusing later.
    fields = {f.name: f.value for f in embed.fields}
    deepest = SPOTS[SPOT_ORDER[-1]]
    assert "🔒" in fields[f"{deepest['emoji']} {deepest['name']}"]


@pytest.mark.cogs("cogs.fishing")
async def test_travel_by_name_charters_and_moves(bot):
    author = config().members[0]
    target = SPOT_ORDER[1]
    await db.add_fishing_xp(author.id, 100_000)
    await db.add_coins(author.id, SPOTS[target]["price"])

    await dpytest.message(f"!fish travel {SPOTS[target]['name']}", member=author)
    embed = dpytest.get_message().embeds[0]

    assert "New Waters" in embed.title
    assert (await db.get_fisher(author.id))["spot"] == target
    assert await db.get_balance(author.id) == 0


@pytest.mark.cogs("cogs.fishing")
async def test_travelling_nowhere_is_refused(bot):
    author = config().members[0]
    await dpytest.message("!fish travel atlantis", member=author)
    assert "nowhere called" in dpytest.get_message().embeds[0].description


@pytest.mark.cogs("cogs.fishing")
async def test_the_travel_picker_shows_live_state(bot):
    guild, author = config().guilds[0], config().members[0]
    cog = bot.get_cog("Fishing")
    interaction = types.SimpleNamespace(guild_id=guild.id, guild=guild, user=author)

    choices = await cog._fish_travel_ac(interaction, "")
    assert [c.value for c in choices] == list(SPOT_ORDER)
    by_value = {c.value: c.name for c in choices}
    assert "📍 here" in by_value[DEFAULT_SPOT]
    assert "🔒" in by_value[SPOT_ORDER[-1]]

    # Typing narrows it.
    assert [c.value for c in await cog._fish_travel_ac(interaction, "reef")] == ["reef"]


@pytest.mark.cogs("cogs.fishing")
async def test_the_shop_lists_bait_and_tackle(bot):
    author = config().members[0]
    await dpytest.message("!fish shop", member=author)
    desc = dpytest.get_message().embeds[0].description

    assert "Worm" in desc
    assert "Cast Net" in desc
    assert "Fish Trap" in desc


@pytest.mark.cogs("cogs.fishing")
async def test_buying_a_trap_points_at_fish_trap_not_inventory_use(bot):
    """A trap is set at a spot rather than armed as an effect, so sending the
    buyer to /inventory use would send them somewhere that can't help."""
    author = config().members[0]
    await db.add_coins(author.id, 1_000)

    await dpytest.message("!fish buy trap", member=author)
    desc = dpytest.get_message().embeds[0].description

    assert "/fish trap" in desc
    assert "/inventory use" not in desc
    assert await db.get_item_qty(author.id, FISH_TRAP) == 1


@pytest.mark.cogs("cogs.fishing")
async def test_trap_command_sets_then_reports_then_pulls(bot):
    author = config().members[0]
    await db.add_item(author.id, FISH_TRAP, 1)

    await dpytest.message("!fish trap", member=author)
    assert "Set" in dpytest.get_message().embeds[0].title

    await dpytest.message("!fish trap", member=author)
    assert "Soaking" in dpytest.get_message().embeds[0].title

    # Wind it back so it's ready.
    await db._conn().execute(
        "UPDATE fishing_traps SET set_at=? WHERE user_id=?",
        (time.time() - TRAP_SOAK - 1, str(author.id)),
    )
    await dpytest.message("!fish trap", member=author)
    assert "Basket" in dpytest.get_message().embeds[0].title
    assert await db.get_traps(author.id) == []


@pytest.mark.cogs("cogs.fishing")
async def test_a_trap_can_be_set_at_every_spot(bot):
    """A trap belongs to the water it sits in, so chartering somewhere new
    gives you somewhere new to leave one — the pond trap is not in the way."""
    author = config().members[0]
    await db.add_item(author.id, FISH_TRAP, 2)
    await db.set_trap(author.id, DEFAULT_SPOT, time.time())
    reef = SPOT_ORDER[1]
    await db.unlock_spot(author.id, reef, time.time())
    await db.set_spot(author.id, reef)

    await dpytest.message("!fish trap", member=author)
    embed = dpytest.get_message().embeds[0]

    assert "Set" in embed.title
    assert SPOTS[reef]["name"] in embed.description
    assert {t["spot"] for t in await db.get_traps(author.id)} == {DEFAULT_SPOT, reef}
    assert await db.get_item_qty(author.id, FISH_TRAP) == 1


@pytest.mark.cogs("cogs.fishing")
async def test_pulling_collects_every_ready_trap_at_once(bot):
    """Travel is free and each basket is rolled at the water its trap sat in,
    so a tour of five places to collect would be busywork rather than a
    decision."""
    author = config().members[0]
    ready = time.time() - TRAP_SOAK - 1
    await db.set_trap(author.id, DEFAULT_SPOT, ready)
    await db.set_trap(author.id, SPOT_ORDER[1], ready)

    await dpytest.message("!fish trap", member=author)
    embed = dpytest.get_message().embeds[0]

    assert "Basket" in embed.title
    assert SPOTS[DEFAULT_SPOT]["name"] in embed.description
    assert SPOTS[SPOT_ORDER[1]]["name"] in embed.description
    assert await db.get_traps(author.id) == []


@pytest.mark.cogs("cogs.fishing")
async def test_a_second_trap_in_one_spot_is_refused_without_eating_it(bot):
    author = config().members[0]
    await db.add_item(author.id, FISH_TRAP, 1)
    await db.set_trap(author.id, DEFAULT_SPOT, time.time())

    await dpytest.message("!fish trap", member=author)

    assert "Soaking" in dpytest.get_message().embeds[0].title
    assert await db.get_item_qty(author.id, FISH_TRAP) == 1


@pytest.mark.cogs("cogs.fishing")
async def test_a_net_pulls_several_fish_from_one_cast(bot, monkeypatch):
    """What the net actually sells is beating the cooldown, so the thing to
    check is that one claim produced more than one fish."""
    from cogs.fishing import cog as fishing

    author = config().members[0]
    await db.grant_effect(author.id, "fish_net", NET_CATCHES, uses=1)
    # 0.5 everywhere: no snag at the pond, a mid-table rarity, no event.
    monkeypatch.setattr(fishing.random, "random", lambda: 0.5)

    await dpytest.message("!fish", member=author)
    dpytest.get_message()

    caught = sum(row["qty"] for row in await db.get_bag(author.id))
    assert caught == NET_CATCHES
    # And the charge is spent, so the next cast is a single again.
    assert "fish_net" not in await db.get_active_effects(author.id)


@pytest.mark.cogs("cogs.fishing")
async def test_a_snag_costs_the_catch_and_only_one_bait_charge(bot, monkeypatch):
    """The rule the hazard was designed around: a bad roll takes the fish and
    the charge already spent on that cast — never the rest of the stack."""
    from cogs.fishing import cog as fishing

    guild, author = config().guilds[0], config().members[0]
    cog = bot.get_cog("Fishing")
    await db.add_fishing_xp(author.id, 100_000)
    await db.add_coins(author.id, SPOTS["deep"]["price"])
    await cog.travel_to(guild, author, "deep")
    await db.grant_effect(author.id, "fish_bait", 0.25, uses=5)
    monkeypatch.setattr(fishing.random, "random", lambda: 0.0)  # always snags

    await dpytest.message("!fish", member=author)
    embed = dpytest.get_message().embeds[0]

    assert "Snapped" in embed.title
    assert await db.get_bag(author.id) == []
    effects = await db.get_active_effects(author.id)
    assert effects["fish_bait"]["uses_left"] == 4  # one charge, no more
