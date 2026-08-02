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
import pytest
import pytest_asyncio
from discord.ext import test as dpytest
from discord.ext.test import backend as dpy_backend

import main
import utils.db as db
from cogs.admin.constants import _ALL_COGS
from utils.db import _cache, _core


@pytest.fixture(autouse=True)
def _clear_config_cache():
    """Drop the per-guild config cache around every test.

    In the bot the cache is emptied by db.init() and lives as long as the one
    connection does. Tests swap in a fresh database per test — often reusing the
    same guild ids — so without this a config cached by one test would answer a
    query in the next.
    """
    _cache.clear()
    _core._board_cache.clear()
    _core._rank_cache.clear()
    yield
    _cache.clear()
    _core._board_cache.clear()
    _core._rank_cache.clear()


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

    dpytest.configure(bot, members=3)
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


@pytest_asyncio.fixture
async def tree_bot(tmp_path, monkeypatch):
    """A NanoBot with every cog loaded, but NO dpytest backend.

    We only inspect the static app-command tree, so we deliberately skip
    dpytest.configure() — it mutates a module-global fake guild/backend that
    would bleed permission state into other tests.
    """
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "nanobot.db"))
    await db.init()
    bot = main.NanoBot({})
    await bot._async_setup_hook()
    for ext in _ALL_COGS:
        await bot.load_extension(ext)
    try:
        yield bot
    finally:
        # Unload every extension: a Cog shares class-level command objects, and
        # loading it into a second bot in-process mutates them. Unloading
        # restores that state so this test can't bleed into the dpytest-backed
        # command tests.
        for ext in _ALL_COGS:
            try:
                await bot.unload_extension(ext)
            except Exception:
                pass
        for task in list(bot._bg_tasks):
            task.cancel()
        try:
            await bot.http.close()
        except Exception:
            pass
        await db.close()
