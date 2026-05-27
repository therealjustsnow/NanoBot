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
