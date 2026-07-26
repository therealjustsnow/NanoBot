"""utils/db.liverole — live-role config.

Part of the utils/db package (split from the former single-file utils/db.py).
Shared connection state + schema machinery live in utils/db/_core.py; this
module holds the live-role config accessors and registers its table setup with _core so
db.init() creates them in the right order.
"""

from ._core import _commit, _conn, register_init

# ══════════════════════════════════════════════════════════════════════════════
#  Live role (streaming presence)
# ══════════════════════════════════════════════════════════════════════════════
# Per-guild config for the streaming auto-role + go-live notifications. Both are
# driven by PRESENCE_UPDATE (requires the privileged presences intent).


async def _ensure_liverole_tables() -> None:
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS liverole_config (
            guild_id    TEXT PRIMARY KEY,
            enabled     INTEGER NOT NULL DEFAULT 0,
            role_id     TEXT,
            channel_id  TEXT,
            announce    INTEGER NOT NULL DEFAULT 1,
            message     TEXT
        )
    """)
    await _commit()


async def get_liverole_config(guild_id: int) -> dict | None:
    """Return the live-role config for a guild, or None if never set up."""
    async with _conn().execute(
        "SELECT enabled, role_id, channel_id, announce, message "
        "FROM liverole_config WHERE guild_id=? LIMIT 1",
        (str(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    return {
        "enabled": bool(row["enabled"]),
        "role_id": row["role_id"],
        "channel_id": row["channel_id"],
        "announce": bool(row["announce"]),
        "message": row["message"],
    }


async def set_liverole_enabled(guild_id: int, enabled: bool) -> None:
    await _conn().execute(
        """INSERT INTO liverole_config (guild_id, enabled)
           VALUES (?, ?)
           ON CONFLICT(guild_id) DO UPDATE SET enabled=excluded.enabled""",
        (str(guild_id), 1 if enabled else 0),
    )
    await _commit()


async def set_liverole_role(guild_id: int, role_id: int | None) -> None:
    await _conn().execute(
        """INSERT INTO liverole_config (guild_id, role_id)
           VALUES (?, ?)
           ON CONFLICT(guild_id) DO UPDATE SET role_id=excluded.role_id""",
        (str(guild_id), str(role_id) if role_id else None),
    )
    await _commit()


async def set_liverole_channel(guild_id: int, channel_id: int | None) -> None:
    await _conn().execute(
        """INSERT INTO liverole_config (guild_id, channel_id)
           VALUES (?, ?)
           ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id""",
        (str(guild_id), str(channel_id) if channel_id else None),
    )
    await _commit()


async def set_liverole_announce(guild_id: int, announce: bool) -> None:
    await _conn().execute(
        """INSERT INTO liverole_config (guild_id, announce)
           VALUES (?, ?)
           ON CONFLICT(guild_id) DO UPDATE SET announce=excluded.announce""",
        (str(guild_id), 1 if announce else 0),
    )
    await _commit()


async def set_liverole_message(guild_id: int, message: str | None) -> None:
    await _conn().execute(
        """INSERT INTO liverole_config (guild_id, message)
           VALUES (?, ?)
           ON CONFLICT(guild_id) DO UPDATE SET message=excluded.message""",
        (str(guild_id), message),
    )
    await _commit()


register_init(_ensure_liverole_tables)
