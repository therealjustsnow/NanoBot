"""
Command-level tests for cogs/leveling.py under dpytest (parse → check → DB → reply).
"""

import discord
import pytest
from discord.ext import commands
from discord.ext import test as dpytest

import utils.db as db
from tests.conftest import config, grant_perms


@pytest.mark.cogs("cogs.leveling")
async def test_rank_replies_for_self(bot):
    author = config().members[0]
    await dpytest.message("!rank", member=author)
    sent = dpytest.get_message()
    assert sent.embeds
    assert author.display_name in sent.embeds[0].title


@pytest.mark.cogs("cogs.leveling")
async def test_level_set_denied_without_manage_guild(bot):
    author, target = config().members[0], config().members[1]
    with pytest.raises(commands.MissingPermissions):
        await dpytest.message(f"!level set {target.mention} 500", member=author)


@pytest.mark.cogs("cogs.leveling")
async def test_level_set_persists_with_perms(bot):
    guild = config().guilds[0]
    author, target = config().members[0], config().members[1]
    await grant_perms(author, manage_guild=True)

    await dpytest.message(f"!level set {target.mention} 500", member=author)
    sent = dpytest.get_message()
    assert sent.embeds
    assert await db.get_xp(guild.id, target.id) == 500


@pytest.mark.cogs("cogs.leveling")
async def test_level_toggle_updates_config(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await grant_perms(author, manage_guild=True)

    await dpytest.message("!level toggle on", member=author)
    dpytest.get_message()
    assert (await db.get_level_config(guild.id))["enabled"] is True


@pytest.mark.cogs("cogs.leveling")
async def test_coinreward_sets_config(bot):
    guild = config().guilds[0]
    author = config().members[0]
    await grant_perms(author, manage_guild=True)

    await dpytest.message("!level coinreward 10", member=author)
    dpytest.get_message()
    assert (await db.get_level_config(guild.id))["coin_reward"] == 10


@pytest.mark.cogs("cogs.leveling")
async def test_level_up_awards_coins(bot):
    """A message that crosses a level boundary grants coin_reward × new level."""
    guild = config().guilds[0]
    author = config().members[0]
    # Deterministic gain, no cooldown, coins on; start just below level 1.
    await db.set_level_config(
        guild.id,
        enabled=True,
        xp_min=200,
        xp_max=200,
        cooldown=0,
        coin_reward=10,
    )
    await db.set_xp(guild.id, author.id, 90)

    await dpytest.message("just chatting", member=author)

    assert await db.get_xp(guild.id, author.id) == 290  # 90 + 200
    # 290 XP → level 2, so reward = 10 × 2 = 20 coins.
    assert await db.get_balance(guild.id, author.id) == 20
