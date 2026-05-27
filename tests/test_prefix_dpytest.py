"""
tests/test_prefix_dpytest.py
Per-guild prefix: only admins can change it, and once changed the bot resolves
commands under the new prefix (exercises NanoBot.get_prefix end to end).
"""

import pytest
from discord.ext import test as dpytest

from tests.conftest import config, grant_perms


@pytest.mark.cogs("cogs.utility")
async def test_prefix_change_requires_admin(bot):
    guild, author = config().guilds[0], config().members[0]
    await dpytest.message("!prefix ?", member=author)
    reply = dpytest.get_message()
    assert reply.embeds and "Administrator" in (reply.embeds[0].description or "")
    # Unchanged because the caller is not an admin.
    assert str(guild.id) not in bot.prefixes


@pytest.mark.cogs("cogs.utility", "cogs.tags")
async def test_prefix_change_applies_to_new_prefix(bot):
    guild, author = config().guilds[0], config().members[0]
    await grant_perms(author, administrator=True)

    await dpytest.message("!prefix ?", member=author)
    updated = dpytest.get_message()
    assert updated.embeds and "Prefix Updated" in updated.embeds[0].title
    assert bot.prefixes[str(guild.id)] == "?"

    # The new prefix now resolves a command (tag list needs no permissions).
    await dpytest.message("?tag list", member=author)
    listing = dpytest.get_message()
    assert listing.embeds
