"""
Command-level tests for cogs/fishing/ under dpytest (parse → check → DB → reply).
"""

import pytest
from discord.ext import commands
from discord.ext import test as dpytest

import utils.db as db
from cogs.fishing import FISH
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
    bag = await db.get_bag(guild.id, author.id)
    assert len(bag) == 1
    assert FISH[bag[0]["fish_key"]]["rarity"] == "common"

    # A second cast inside the default 60s cooldown is refused, nothing caught.
    await dpytest.message("!fish cast", member=author)
    sent = dpytest.get_message()
    assert "Not Yet" in sent.embeds[0].title
    assert sum(r["qty"] for r in await db.get_bag(guild.id, author.id)) == 1


@pytest.mark.cogs("cogs.fishing")
async def test_cast_refused_when_disabled(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.set_fishing_config(guild.id, enabled=False)

    await dpytest.message("!fish", member=author)
    sent = dpytest.get_message()
    assert "disabled" in sent.embeds[0].description
    assert await db.get_bag(guild.id, author.id) == []


@pytest.mark.cogs("cogs.fishing")
async def test_sell_credits_coins_and_empties_bag(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.record_catch(guild.id, author.id, "salmon", 5.0, 40)
    await db.record_catch(guild.id, author.id, "boot", 0.5, 1)

    await dpytest.message("!fish sell", member=author)
    sent = dpytest.get_message()
    assert "Sold" in sent.embeds[0].title
    assert await db.get_balance(guild.id, author.id) == 41
    assert await db.get_bag(guild.id, author.id) == []
    assert (await db.get_fisher(guild.id, author.id))["earned"] == 41


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
    await db.add_coins(guild.id, author.id, 600)

    await dpytest.message("!fish upgrade", member=author)
    sent = dpytest.get_message()
    assert "Upgraded" in sent.embeds[0].title
    assert (await db.get_fisher(guild.id, author.id))["rod_level"] == 1
    assert await db.get_balance(guild.id, author.id) == 100  # 600 - 500


@pytest.mark.cogs("cogs.fishing")
async def test_upgrade_insufficient_funds(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(guild.id, author.id, 10)

    await dpytest.message("!fish upgrade", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert (await db.get_fisher(guild.id, author.id))["rod_level"] == 0
    assert await db.get_balance(guild.id, author.id) == 10


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
    await db.add_coins(guild.id, author.id, 100)

    await dpytest.message("!fish buy Worm", member=author)
    sent = dpytest.get_message()
    assert "Bought" in sent.embeds[0].title
    assert await db.get_balance(guild.id, author.id) == 75  # 100 - 25
    assert await db.get_item_qty(guild.id, author.id, "bait_worm") == 1


@pytest.mark.cogs("cogs.fishing")
async def test_buy_bait_quantity_multiplies_cost(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(guild.id, author.id, 100)

    await dpytest.message("!fish buy Worm 3", member=author)
    sent = dpytest.get_message()
    assert "Bought" in sent.embeds[0].title
    assert await db.get_balance(guild.id, author.id) == 25  # 100 - 3*25
    assert await db.get_item_qty(guild.id, author.id, "bait_worm") == 3


@pytest.mark.cogs("cogs.fishing")
async def test_buy_insufficient_funds(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(guild.id, author.id, 10)

    await dpytest.message("!fish buy Worm", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert await db.get_balance(guild.id, author.id) == 10
    assert await db.get_item_qty(guild.id, author.id, "bait_worm") == 0


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
    await db.add_item(guild.id, author.id, "bait_worm", 2)
    await db.grant_effect(guild.id, author.id, "fish_bait", 0.05, uses=5)

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
    await db.grant_effect(guild.id, author.id, "fish_bait", 0.05, uses=2)

    await dpytest.message("!fish", member=author)
    dpytest.get_message()

    effects = await db.get_active_effects(guild.id, author.id)
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
async def test_event_force_start_requires_manage_guild(bot):
    author = config().members[0]
    with pytest.raises(commands.MissingPermissions):
        await dpytest.message("!fish event frenzy", member=author)


@pytest.mark.cogs("cogs.fishing")
async def test_event_force_start_and_list(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await grant_perms(author, manage_guild=True)

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
    await grant_perms(author, manage_guild=True)
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
    await db.record_catch(guild.id, author.id, "salmon", 5.0, 40)
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
    await db.add_fishing_xp(guild.id, author.id, 30)

    await dpytest.message("!fish stats", member=author)
    sent = dpytest.get_message()
    embed = sent.embeds[0]
    assert any(f.name == "Level" for f in embed.fields)
