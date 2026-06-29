"""
Tests for the birthday DB accessors in utils/db/ (in-memory SQLite).
"""

import aiosqlite
import pytest

import utils.db as db


@pytest.fixture(autouse=True)
async def database(monkeypatch):
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    monkeypatch.setattr(db, "_db", conn)
    await db._ensure_birthday_tables()
    yield conn
    await conn.close()
    monkeypatch.setattr(db, "_db", None)


# ── config ─────────────────────────────────────────────────────────────────────


async def test_config_defaults_when_unset():
    cfg = await db.get_birthday_config(1)
    assert cfg["enabled"] is False
    assert cfg["channel_id"] is None
    assert cfg["timezone"] == "UTC"
    assert cfg["hour"] == 9
    assert cfg["gif_enabled"] is True
    assert cfg["vc_enabled"] is True
    assert cfg["ping_enabled"] is True


async def test_config_set_and_roundtrip():
    await db.set_birthday_config(
        1, enabled=True, channel_id="42", timezone="Europe/London", hour=8
    )
    cfg = await db.get_birthday_config(1)
    assert cfg["enabled"] is True
    assert cfg["channel_id"] == "42"
    assert cfg["timezone"] == "Europe/London"
    assert cfg["hour"] == 8


async def test_config_partial_update_preserves_other_fields():
    await db.set_birthday_config(1, channel_id="42", vc_enabled=False)
    await db.set_birthday_config(1, enabled=True)
    cfg = await db.get_birthday_config(1)
    assert cfg["enabled"] is True
    assert cfg["channel_id"] == "42"  # preserved
    assert cfg["vc_enabled"] is False  # preserved


async def test_get_enabled_configs_filters_disabled_and_channelless():
    await db.set_birthday_config(1, enabled=True, channel_id="10")
    await db.set_birthday_config(2, enabled=False, channel_id="20")
    await db.set_birthday_config(3, enabled=True, channel_id=None)
    enabled = await db.get_enabled_birthday_configs()
    assert set(enabled.keys()) == {1}
    assert enabled[1]["channel_id"] == "10"


# ── birthdays ──────────────────────────────────────────────────────────────────


async def test_set_get_remove_birthday():
    await db.set_birthday(1, 100, 3, 5, 1998)
    bd = await db.get_birthday(1, 100)
    assert (bd["month"], bd["day"], bd["year"]) == (3, 5, 1998)
    assert bd["last_announced"] is None

    assert await db.remove_birthday(1, 100) is True
    assert await db.get_birthday(1, 100) is None
    assert await db.remove_birthday(1, 100) is False  # already gone


async def test_set_birthday_overwrites_and_clears_announced():
    await db.set_birthday(1, 100, 3, 5, 1998)
    await db.set_birthday_announced(1, 100, "2026-03-05")
    assert (await db.get_birthday(1, 100))["last_announced"] == "2026-03-05"

    # Re-registering a corrected date clears the announced marker.
    await db.set_birthday(1, 100, 4, 6, None)
    bd = await db.get_birthday(1, 100)
    assert (bd["month"], bd["day"], bd["year"]) == (4, 6, None)
    assert bd["last_announced"] is None


async def test_announced_marker_roundtrip():
    await db.set_birthday(1, 100, 3, 5, None)
    await db.set_birthday_announced(1, 100, "2026-03-05")
    assert (await db.get_birthday(1, 100))["last_announced"] == "2026-03-05"


async def test_guild_birthdays_scoped_and_ordered():
    await db.set_birthday(1, 100, 12, 25, None)
    await db.set_birthday(1, 101, 1, 5, None)
    await db.set_birthday(2, 200, 6, 6, None)  # other guild
    rows = await db.get_guild_birthdays(1)
    assert [int(r["user_id"]) for r in rows] == [101, 100]  # ordered by month, day
