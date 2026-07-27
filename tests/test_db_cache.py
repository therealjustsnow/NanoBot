"""
Tests for the per-guild config cache in utils/db/_cache.py.

Config rows are read on every chat message (leveling) and at the top of every
economy command, but written almost never — so they're cached in process. What
matters is that the cache can never serve something staler than this process's
last write, and that a caller mutating the dict it got back can't corrupt it.
"""

import aiosqlite
import pytest

import utils.db as db
from utils.db import _cache


@pytest.fixture(autouse=True)
async def database(monkeypatch):
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    monkeypatch.setattr(db, "_db", conn)
    await db._ensure_leveling_tables()
    await db._ensure_economy_tables()
    await db._ensure_fishing_tables()
    await db._ensure_activities_tables()
    _cache.clear()
    yield conn
    _cache.clear()
    await conn.close()


G = 4242


async def test_config_reads_are_served_from_cache():
    """A second read must not touch the database at all."""
    await db.set_level_config(G, enabled=True, xp_min=5)
    assert (await db.get_level_config(G))["xp_min"] == 5
    await db._conn().close()  # any further query would raise
    assert (await db.get_level_config(G))["xp_min"] == 5


async def test_defaults_are_cached_too():
    assert (await db.get_econ_config(G))["daily_amount"] == 100
    await db._conn().close()
    assert (await db.get_econ_config(G))["currency_name"] == "NanoCoin"


@pytest.mark.parametrize(
    "getter,setter,key,value",
    [
        ("get_level_config", "set_level_config", "xp_max", 99),
        ("get_econ_config", "set_econ_config", "daily_amount", 777),
        ("get_fishing_config", "set_fishing_config", "enabled", False),
        ("get_activities_config", "set_activities_config", "work_enabled", False),
    ],
)
async def test_setter_invalidates(getter, setter, key, value):
    before = (await getattr(db, getter)(G))[key]
    assert before != value
    await getattr(db, setter)(G, **{key: value})
    assert (await getattr(db, getter)(G))[key] == value


async def test_caller_cannot_poison_the_cached_row():
    cfg = await db.get_econ_config(G)
    cfg["currency_name"] = "Poison"
    assert (await db.get_econ_config(G))["currency_name"] == "NanoCoin"


async def test_ignored_channels_cached_and_invalidated():
    assert await db.get_level_ignored_channels(G) == set()
    await db.add_level_ignored_channel(G, 5)
    assert await db.get_level_ignored_channels(G) == {5}
    await db.remove_level_ignored_channel(G, 5)
    assert await db.get_level_ignored_channels(G) == set()


async def test_guilds_do_not_share_entries():
    await db.set_fishing_config(G, enabled=False)
    assert (await db.get_fishing_config(G))["enabled"] is False
    assert (await db.get_fishing_config(G + 1))["enabled"] is True
