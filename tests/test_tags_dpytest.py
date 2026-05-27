"""
tests/test_tags_dpytest.py
Tag round-trip and the `!<tagname>` shortcut — exercised through dpytest so the
on_message tag-shortcut path (which only runs for non-command messages) is
actually executed.
"""

import pytest
from discord.ext import test as dpytest

import utils.db as db
from tests.conftest import config


@pytest.mark.cogs("cogs.tags")
async def test_tag_create_persists(bot):
    guild, author = config().guilds[0], config().members[0]
    await dpytest.message('!tag create greet "hello there"', member=author)
    created = dpytest.get_message()
    assert created.embeds and "Tag Created" in created.embeds[0].title

    tag = await db.get_tag(guild.id, "greet", author.id)
    assert tag is not None
    assert tag["content"] == "hello there"


@pytest.mark.cogs("cogs.tags")
async def test_tag_shortcut_fires_for_owner(bot):
    author = config().members[0]
    await dpytest.message('!tag create greet "hello there"', member=author)
    dpytest.get_message()

    # A bare `!greet` is not a command — on_message should fire the tag shortcut.
    await dpytest.message("!greet", member=author)
    posted = dpytest.get_message()
    assert posted.embeds
    assert "greet" in (posted.embeds[0].title or "")
    assert "hello there" in (posted.embeds[0].description or "")


@pytest.mark.cogs("cogs.tags")
async def test_tag_delete_removes_it(bot):
    guild, author = config().guilds[0], config().members[0]
    await dpytest.message('!tag create greet "hello there"', member=author)
    dpytest.get_message()

    await dpytest.message("!tag delete greet", member=author)
    dpytest.get_message()
    assert await db.get_tag(guild.id, "greet", author.id) is None
