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
