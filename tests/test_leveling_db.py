"""
Tests for the leveling accessors in utils/db/ — in-memory SQLite.
"""

import asyncio

import aiosqlite
import pytest

import utils.db as db


@pytest.fixture(autouse=True)
async def database(monkeypatch):
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    monkeypatch.setattr(db, "_db", conn)
    await db._ensure_leveling_tables()
    yield conn
    await conn.close()


G = 111
A, B, C = 1, 2, 3


async def test_add_xp_accumulates_and_returns_total():
    assert await db.add_xp(G, A, 30) == 30
    assert await db.add_xp(G, A, 20) == 50
    assert await db.get_xp(G, A) == 50


async def test_add_xp_clamps_at_zero():
    await db.add_xp(G, A, 40)
    assert await db.add_xp(G, A, -100) == 0


async def test_concurrent_add_xp_keeps_every_award():
    """The old read-then-write dropped an award when two messages raced."""
    await asyncio.gather(*(db.add_xp(G, A, 10) for _ in range(20)))
    assert await db.get_xp(G, A) == 200


async def test_add_xp_uses_returning_when_available(monkeypatch):
    """The fixture patches the connection in without init(), so RETURNING is
    off by default — exercise the other branch of fetch_one_returning too."""
    monkeypatch.setattr(db._core, "_RETURNING_OK", True)
    assert await db.add_xp(G, B, 30) == 30
    assert await db.add_xp(G, B, 20) == 50
    assert await db.add_xp(G, B, -500) == 0
    assert await db.get_xp(G, B) == 0


async def test_set_xp_is_absolute():
    await db.add_xp(G, A, 500)
    await db.set_xp(G, A, 10)
    assert await db.get_xp(G, A) == 10


async def test_get_xp_missing_is_zero():
    assert await db.get_xp(G, 999) == 0


async def test_rank_ordering():
    await db.add_xp(G, A, 100)
    await db.add_xp(G, B, 300)
    await db.add_xp(G, C, 200)
    assert await db.get_rank(G, B) == (1, 300)
    assert await db.get_rank(G, C) == (2, 200)
    assert await db.get_rank(G, A) == (3, 100)


async def test_rank_none_when_unranked():
    assert await db.get_rank(G, 42) is None


async def test_leaderboard_excludes_zero_and_paginates():
    await db.add_xp(G, A, 100)
    await db.add_xp(G, B, 300)
    await db.set_xp(G, C, 0)  # zero XP row should not appear
    top = await db.get_leaderboard(G, limit=10, offset=0)
    assert [r["user_id"] for r in top] == [B, A]
    assert await db.count_ranked(G) == 2


async def test_leaderboard_is_guild_scoped():
    await db.add_xp(G, A, 100)
    await db.add_xp(222, B, 999)
    assert await db.count_ranked(G) == 1


async def test_reset_one_and_all():
    await db.add_xp(G, A, 100)
    await db.add_xp(G, B, 100)
    assert await db.reset_levels(G, A) == 1
    assert await db.get_xp(G, A) == 0
    await db.add_xp(G, A, 50)
    removed = await db.reset_levels(G)
    assert removed == 2
    assert await db.count_ranked(G) == 0


async def test_config_defaults_then_partial_update():
    cfg = await db.get_level_config(G)
    assert cfg["enabled"] is False
    assert cfg["xp_min"] == 15 and cfg["xp_max"] == 25 and cfg["cooldown"] == 60

    assert cfg["coin_reward"] == 0

    await db.set_level_config(G, enabled=True, cooldown=30, coin_reward=10)
    cfg = await db.get_level_config(G)
    assert cfg["enabled"] is True
    assert cfg["cooldown"] == 30
    assert cfg["coin_reward"] == 10
    assert cfg["xp_min"] == 15  # untouched keys preserved


async def test_announce_channel_round_trip():
    await db.set_level_config(G, announce_channel=555)
    assert (await db.get_level_config(G))["announce_channel"] == 555
    await db.set_level_config(G, announce_channel=None)
    assert (await db.get_level_config(G))["announce_channel"] is None


async def test_rewards_add_list_remove():
    await db.add_level_reward(G, 5, 900)
    await db.add_level_reward(G, 10, 901)
    assert await db.get_level_rewards(G) == {5: 900, 10: 901}
    # upsert replaces the role at an existing level
    await db.add_level_reward(G, 5, 950)
    assert (await db.get_level_rewards(G))[5] == 950
    assert await db.remove_level_reward(G, 5) is True
    assert await db.remove_level_reward(G, 5) is False
    assert await db.get_level_rewards(G) == {10: 901}


async def test_ignored_channels():
    await db.add_level_ignored_channel(G, 700)
    await db.add_level_ignored_channel(G, 700)  # idempotent
    await db.add_level_ignored_channel(G, 701)
    assert await db.get_level_ignored_channels(G) == {700, 701}
    assert await db.remove_level_ignored_channel(G, 700) is True
    assert await db.remove_level_ignored_channel(G, 700) is False
    assert await db.get_level_ignored_channels(G) == {701}
