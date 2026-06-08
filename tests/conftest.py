"""
tests/conftest.py
Shared pytest fixtures for the dpytest-backed command tests.

dpytest stands in for a live Discord connection: it builds a fake guild,
channels, and members, and lets a test dispatch a message and inspect what the
bot sends back — so prefix/slash command flow (argument parsing, permission
checks, DB round-trips, reply embeds) can be exercised without a real gateway.

Pure-logic tests (helpers, config, db, storage) do not need any of this and
should stay free of the `bot` fixture.
"""

import discord
import pytest_asyncio
from discord.ext import test as dpytest
from discord.ext.test import backend as dpy_backend

import main
import utils.db as db


@pytest_asyncio.fixture
async def bot(tmp_path, monkeypatch, request):
    """A NanoBot wired to a dpytest fake guild and a throwaway on-disk DB.

    Request the cogs to load via the `cogs` marker, e.g.:
        @pytest.mark.cogs("cogs.moderation")
    Defaults to loading no cogs.
    """
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "nanobot.db"))
    await db.init()

    bot = main.NanoBot({})
    await bot._async_setup_hook()

    marker = request.node.get_closest_marker("cogs")
    for ext in marker.args if marker else ():
        await bot.load_extension(ext)

    dpytest.configure(bot, members=2)
    try:
        yield bot
    finally:
        # Don't call bot.close(): under dpytest there is no real gateway, so the
        # base Client.close() trips over the mocked websocket. Tear down by hand.
        await dpytest.empty_queue()
        for task in list(bot._bg_tasks):
            task.cancel()
        try:
            await bot.http.close()
        except Exception:
            pass
        await db.close()


async def grant_perms(member: discord.Member, **perms) -> None:
    """Give *member* a role carrying the named permissions (e.g. manage_messages=True)."""
    role = dpy_backend.make_role(
        "granted", member.guild, permissions=discord.Permissions(**perms).value
    )
    await dpytest.add_role(member, role)


def config():
    return dpytest.get_config()
