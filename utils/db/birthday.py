"""utils/db.birthday — birthday tracker.

Part of the utils/db package (split from the former single-file utils/db.py).
Shared connection state + schema machinery live in utils/db/_core.py; this
module holds the birthday tracker accessors and registers its table setup with _core so
db.init() creates them in the right order.
"""

import time
from typing import Any

import aiosqlite


from ._core import _commit, _conn, register_init

# ══════════════════════════════════════════════════════════════════════════════
#  Birthdays
# ══════════════════════════════════════════════════════════════════════════════
# `birthdays` holds each member's registered birthday (per guild). `month`/`day`
# always set; `year` is optional (drives the "turns N" line when known).
# `last_announced` is the local date string ("YYYY-MM-DD") the birthday was last
# celebrated — the once-per-year/once-per-day guard so the daily check fires each
# birthday exactly once.
#
# `birthday_config` holds per-guild settings: where to announce, the local
# timezone + hour the announcement fires, and the gif / voice-channel toggles.
_BD_DEFAULTS = {
    "enabled": False,
    "channel_id": None,
    "message": None,
    "timezone": "UTC",
    "hour": 9,  # local hour (0-23) the announcement fires
    "gif_enabled": True,
    "vc_enabled": True,  # join the member's voice channel and play the song once
    "ping_enabled": True,
    "song": None,  # optional path/URL override for the voice-channel song
}
_BD_BOOL_COLS = ("enabled", "gif_enabled", "vc_enabled", "ping_enabled")
_BD_COLS = (
    "enabled",
    "channel_id",
    "message",
    "timezone",
    "hour",
    "gif_enabled",
    "vc_enabled",
    "ping_enabled",
    "song",
)


async def _ensure_birthday_tables() -> None:
    await _conn().executescript("""
        CREATE TABLE IF NOT EXISTS birthdays (
            guild_id       TEXT NOT NULL,
            user_id        TEXT NOT NULL,
            month          INTEGER NOT NULL,
            day            INTEGER NOT NULL,
            year           INTEGER,
            last_announced TEXT,
            created_at     REAL NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        );
        CREATE INDEX IF NOT EXISTS birthdays_guild ON birthdays (guild_id);

        CREATE TABLE IF NOT EXISTS birthday_config (
            guild_id      TEXT PRIMARY KEY,
            enabled       INTEGER NOT NULL DEFAULT 0,
            channel_id    TEXT,
            message       TEXT,
            timezone      TEXT NOT NULL DEFAULT 'UTC',
            hour          INTEGER NOT NULL DEFAULT 9,
            gif_enabled   INTEGER NOT NULL DEFAULT 1,
            vc_enabled    INTEGER NOT NULL DEFAULT 1,
            ping_enabled  INTEGER NOT NULL DEFAULT 1,
            song          TEXT
        );
    """)
    await _commit()


def _birthday_config_row(row: aiosqlite.Row) -> dict:
    out: dict = {}
    for col in _BD_COLS:
        val = row[col]
        out[col] = bool(val) if col in _BD_BOOL_COLS else val
    return out


async def get_birthday_config(guild_id: int) -> dict:
    """Return the birthday config for a guild, defaults merged in when unset."""
    async with _conn().execute(
        f"SELECT {', '.join(_BD_COLS)} FROM birthday_config WHERE guild_id=? LIMIT 1",
        (str(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    return _birthday_config_row(row) if row else dict(_BD_DEFAULTS)


async def set_birthday_config(guild_id: int, **kwargs: Any) -> None:
    """Merge kwargs into the guild's config row (creating it from defaults)."""
    current = await get_birthday_config(guild_id)
    current.update(kwargs)
    values = [str(guild_id)]
    for col in _BD_COLS:
        val = current[col]
        if col in _BD_BOOL_COLS:
            values.append(1 if val else 0)
        else:
            values.append(val)
    placeholders = ", ".join(["?"] * (len(_BD_COLS) + 1))
    updates = ", ".join(f"{c}=excluded.{c}" for c in _BD_COLS)
    await _conn().execute(
        f"INSERT INTO birthday_config (guild_id, {', '.join(_BD_COLS)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT(guild_id) DO UPDATE SET {updates}",
        values,
    )
    await _commit()


async def get_enabled_birthday_configs() -> dict:
    """Return {guild_id(int): config} for every guild with announcements on."""
    async with _conn().execute(
        f"SELECT guild_id, {', '.join(_BD_COLS)} FROM birthday_config "
        "WHERE enabled=1 AND channel_id IS NOT NULL"
    ) as cur:
        rows = await cur.fetchall()
    return {int(r["guild_id"]): _birthday_config_row(r) for r in rows}


async def set_birthday(
    guild_id: int, user_id: int, month: int, day: int, year: int | None
) -> None:
    """Register (or update) a member's birthday. Resets the announced marker so a
    corrected date can still fire today."""
    await _conn().execute(
        "INSERT INTO birthdays "
        "(guild_id, user_id, month, day, year, last_announced, created_at) "
        "VALUES (?,?,?,?,?,NULL,?) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET "
        "month=excluded.month, day=excluded.day, year=excluded.year, "
        "last_announced=NULL",
        (str(guild_id), str(user_id), int(month), int(day), year, time.time()),
    )
    await _commit()


async def get_birthday(guild_id: int, user_id: int) -> dict | None:
    async with _conn().execute(
        "SELECT guild_id, user_id, month, day, year, last_announced "
        "FROM birthdays WHERE guild_id=? AND user_id=? LIMIT 1",
        (str(guild_id), str(user_id)),
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def remove_birthday(guild_id: int, user_id: int) -> bool:
    """Delete a member's birthday. Returns True if a row was removed."""
    cur = await _conn().execute(
        "DELETE FROM birthdays WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id)),
    )
    await _commit()
    return cur.rowcount > 0


async def get_guild_birthdays(guild_id: int) -> list[dict]:
    """Every registered birthday in a guild (for the daily check + list command)."""
    async with _conn().execute(
        "SELECT guild_id, user_id, month, day, year, last_announced "
        "FROM birthdays WHERE guild_id=? ORDER BY month, day",
        (str(guild_id),),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def set_birthday_announced(guild_id: int, user_id: int, date_str: str) -> None:
    """Stamp the local date a birthday was last celebrated (once-per-year guard)."""
    await _conn().execute(
        "UPDATE birthdays SET last_announced=? WHERE guild_id=? AND user_id=?",
        (date_str, str(guild_id), str(user_id)),
    )
    await _commit()


register_init(_ensure_birthday_tables)
