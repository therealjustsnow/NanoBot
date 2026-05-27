"""
Command-level tests for cogs/economy.py under dpytest (parse → check → DB → reply).
"""

import discord
import pytest
from discord.ext import commands
from discord.ext import test as dpytest

import utils.db as db
from tests.conftest import config, grant_perms


@pytest.mark.cogs("cogs.economy")
async def test_balance_replies(bot):
    author = config().members[0]
    await dpytest.message("!balance", member=author)
    sent = dpytest.get_message()
    assert sent.embeds
    assert author.display_name in sent.embeds[0].title


@pytest.mark.cogs("cogs.economy")
async def test_daily_grants_then_blocks(bot):
    guild = config().guilds[0]
    author = config().members[0]

    await dpytest.message("!daily", member=author)
    dpytest.get_message()
    assert await db.get_balance(guild.id, author.id) == 100  # default daily

    # Second claim same day is on cooldown — balance unchanged.
    await dpytest.message("!daily", member=author)
    dpytest.get_message()
    assert await db.get_balance(guild.id, author.id) == 100


@pytest.mark.cogs("cogs.economy")
async def test_pay_transfers_funds(bot):
    guild = config().guilds[0]
    author, target = config().members[0], config().members[1]
    await db.add_coins(guild.id, author.id, 100)

    await dpytest.message(f"!pay {target.mention} 30", member=author)
    sent = dpytest.get_message()
    assert sent.embeds
    assert await db.get_balance(guild.id, author.id) == 70
    assert await db.get_balance(guild.id, target.id) == 30


@pytest.mark.cogs("cogs.economy")
async def test_pay_insufficient_funds(bot):
    guild = config().guilds[0]
    author, target = config().members[0], config().members[1]
    await db.add_coins(guild.id, author.id, 10)

    await dpytest.message(f"!pay {target.mention} 50", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert await db.get_balance(guild.id, author.id) == 10
    assert await db.get_balance(guild.id, target.id) == 0


@pytest.mark.cogs("cogs.economy")
async def test_grant_denied_without_manage_guild(bot):
    author, target = config().members[0], config().members[1]
    with pytest.raises(commands.MissingPermissions):
        await dpytest.message(f"!coin grant {target.mention} 500", member=author)


@pytest.mark.cogs("cogs.economy")
async def test_grant_credits_with_perms(bot):
    guild = config().guilds[0]
    author, target = config().members[0], config().members[1]
    await grant_perms(author, manage_guild=True)

    await dpytest.message(f"!coin grant {target.mention} 500", member=author)
    sent = dpytest.get_message()
    assert sent.embeds
    assert await db.get_balance(guild.id, target.id) == 500


@pytest.mark.cogs("cogs.economy")
async def test_gamble_win(bot, monkeypatch):
    import cogs.economy as economy

    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(guild.id, author.id, 100)
    monkeypatch.setattr(economy.random, "random", lambda: 0.0)  # force a win

    await dpytest.message("!coin gamble 50", member=author)
    sent = dpytest.get_message()
    assert "Winner" in sent.embeds[0].title
    assert await db.get_balance(guild.id, author.id) == 150


@pytest.mark.cogs("cogs.economy")
async def test_gamble_loss(bot, monkeypatch):
    import cogs.economy as economy

    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(guild.id, author.id, 100)
    monkeypatch.setattr(economy.random, "random", lambda: 0.99)  # force a loss

    await dpytest.message("!coin gamble 50", member=author)
    sent = dpytest.get_message()
    assert "Bust" in sent.embeds[0].title
    assert await db.get_balance(guild.id, author.id) == 50


@pytest.mark.cogs("cogs.economy")
async def test_gamble_insufficient_funds(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await db.add_coins(guild.id, author.id, 10)

    await dpytest.message("!coin gamble 50", member=author)
    sent = dpytest.get_message()
    assert "Error" in sent.embeds[0].title
    assert await db.get_balance(guild.id, author.id) == 10
