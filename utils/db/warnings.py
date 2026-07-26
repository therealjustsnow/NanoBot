"""utils/db.warnings — warning + threshold config.

Part of the utils/db package (split from the former single-file utils/db.py).
Shared connection state + schema machinery live in utils/db/_core.py; this
module holds the warning + threshold config accessors and registers its table setup with _core so
db.init() creates them in the right order.
"""

from ._core import _commit, _conn, register_init

# ══════════════════════════════════════════════════════════════════════════════
#  Warnings
# ══════════════════════════════════════════════════════════════════════════════


async def _ensure_warnings_tables():
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS warnings (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id   TEXT NOT NULL,
            user_id    TEXT NOT NULL,
            reason     TEXT NOT NULL,
            by_id      TEXT NOT NULL,
            by_name    TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    await _conn().execute(
        "CREATE INDEX IF NOT EXISTS warnings_guild_user ON warnings (guild_id, user_id)"
    )
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS warn_config (
            guild_id   TEXT PRIMARY KEY,
            kick_at    INTEGER NOT NULL DEFAULT 0,
            ban_at     INTEGER NOT NULL DEFAULT 0,
            dm_user    INTEGER NOT NULL DEFAULT 1
        )
    """)
    await _commit()


async def add_warning(
    guild_id: int,
    user_id: int,
    reason: str,
    by_id: str,
    by_name: str,
    created_at: str,
) -> int:
    """Add a warning. Returns new total warning count for that user."""
    await _conn().execute(
        "INSERT INTO warnings (guild_id, user_id, reason, by_id, by_name, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (str(guild_id), str(user_id), reason, by_id, by_name, created_at),
    )
    await _commit()
    async with _conn().execute(
        "SELECT COUNT(*) FROM warnings WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id)),
    ) as cur:
        row = await cur.fetchone()
    return row[0]


async def get_warnings(guild_id: int, user_id: int) -> list[dict]:
    async with _conn().execute(
        "SELECT id, reason, by_name, created_at FROM warnings "
        "WHERE guild_id=? AND user_id=? ORDER BY id ASC",
        (str(guild_id), str(user_id)),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {
            "id": r["id"],
            "reason": r["reason"],
            "by_name": r["by_name"],
            "at": r["created_at"],
        }
        for r in rows
    ]


async def get_warning_count(guild_id: int, user_id: int) -> int:
    async with _conn().execute(
        "SELECT COUNT(*) FROM warnings WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id)),
    ) as cur:
        row = await cur.fetchone()
    return row[0]


async def clear_warnings(guild_id: int, user_id: int) -> int:
    cur = await _conn().execute(
        "DELETE FROM warnings WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id)),
    )
    await _commit()
    return cur.rowcount


async def get_warn_config(guild_id: int) -> dict:
    async with _conn().execute(
        "SELECT kick_at, ban_at, dm_user FROM warn_config WHERE guild_id=?",
        (str(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    if row:
        return {
            "kick_at": row["kick_at"],
            "ban_at": row["ban_at"],
            "dm_user": bool(row["dm_user"]),
        }
    return {"kick_at": 0, "ban_at": 0, "dm_user": True}


async def set_warn_config(
    guild_id: int, kick_at: int, ban_at: int, dm_user: bool
) -> None:
    await _conn().execute(
        "INSERT INTO warn_config (guild_id, kick_at, ban_at, dm_user) VALUES (?,?,?,?) "
        "ON CONFLICT(guild_id) DO UPDATE SET kick_at=excluded.kick_at, "
        "ban_at=excluded.ban_at, dm_user=excluded.dm_user",
        (str(guild_id), kick_at, ban_at, 1 if dm_user else 0),
    )
    await _commit()


register_init(_ensure_warnings_tables)
