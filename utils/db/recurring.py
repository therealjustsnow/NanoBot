"""utils/db.recurring — recurring reminder.

Part of the utils/db package (split from the former single-file utils/db.py).
Shared connection state + schema machinery live in utils/db/_core.py; this
module holds the recurring reminder accessors and registers its table setup with _core so
db.init() creates them in the right order.
"""

import aiosqlite


from ._core import _commit, _conn, register_init

# ══════════════════════════════════════════════════════════════════════════════
#  Recurring Reminders
# ══════════════════════════════════════════════════════════════════════════════


async def _ensure_recurring_table():
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS recurring_reminders (
            id          TEXT PRIMARY KEY,
            target_id   TEXT NOT NULL,
            set_by_id   TEXT NOT NULL,
            guild_id    TEXT NOT NULL,
            channel_id  TEXT NOT NULL,
            message     TEXT NOT NULL,
            interval    REAL NOT NULL,
            next_due    REAL NOT NULL,
            dm          INTEGER NOT NULL DEFAULT 1,
            paused      INTEGER NOT NULL DEFAULT 0,
            fire_count  INTEGER NOT NULL DEFAULT 0,
            label       TEXT
        )
    """)
    await _conn().execute(
        "CREATE INDEX IF NOT EXISTS recurring_target "
        "ON recurring_reminders (target_id)"
    )
    await _commit()


async def recurring_id_exists(rid: str) -> bool:
    async with _conn().execute(
        "SELECT 1 FROM recurring_reminders WHERE id=? LIMIT 1", (rid,)
    ) as cur:
        return await cur.fetchone() is not None


async def set_recurring(info: dict) -> None:
    """Insert a new recurring reminder. Ignores duplicates (idempotent)."""
    await _conn().execute(
        """INSERT OR IGNORE INTO recurring_reminders
           (id, target_id, set_by_id, guild_id, channel_id, message,
            interval, next_due, dm, paused, fire_count, label)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            info["id"],
            info["target_id"],
            info["set_by_id"],
            info["guild_id"],
            info["channel_id"],
            info["message"],
            info["interval"],
            info["next_due"],
            1 if info.get("dm", True) else 0,
            1 if info.get("paused", False) else 0,
            info.get("fire_count", 0),
            info.get("label"),
        ),
    )
    await _commit()


async def update_recurring(info: dict) -> None:
    """Update mutable fields — next_due, fire_count, and paused — after each fire."""
    await _conn().execute(
        """UPDATE recurring_reminders
           SET next_due=?, fire_count=?, paused=?
           WHERE id=?""",
        (
            info["next_due"],
            info.get("fire_count", 0),
            1 if info.get("paused", False) else 0,
            info["id"],
        ),
    )
    await _commit()


async def set_recurring_paused(rid: str, paused: bool) -> None:
    """Flip the paused flag only — used by pause/resume commands."""
    await _conn().execute(
        "UPDATE recurring_reminders SET paused=? WHERE id=?",
        (1 if paused else 0, rid),
    )
    await _commit()


async def remove_recurring(rid: str) -> None:
    """Permanently delete a recurring reminder."""
    await _conn().execute("DELETE FROM recurring_reminders WHERE id=?", (rid,))
    await _commit()


async def get_recurring(rid: str) -> dict | None:
    """Fetch a single recurring reminder by ID. Returns None if not found."""
    async with _conn().execute(
        "SELECT id, target_id, set_by_id, guild_id, channel_id, message, "
        "interval, next_due, dm, paused, fire_count, label "
        "FROM recurring_reminders WHERE id=? LIMIT 1",
        (rid,),
    ) as cur:
        row = await cur.fetchone()
    return _recurring_row(row) if row else None


async def get_user_recurring(user_id: int) -> list[dict]:
    """All recurring reminders for a user, ordered by next_due ascending."""
    async with _conn().execute(
        "SELECT id, target_id, set_by_id, guild_id, channel_id, message, "
        "interval, next_due, dm, paused, fire_count, label "
        "FROM recurring_reminders WHERE target_id=? ORDER BY next_due ASC",
        (str(user_id),),
    ) as cur:
        rows = await cur.fetchall()
    return [_recurring_row(r) for r in rows]


async def get_all_recurring() -> dict:
    """Returns {id: info} for every recurring reminder — used on bot restore."""
    async with _conn().execute(
        "SELECT id, target_id, set_by_id, guild_id, channel_id, message, "
        "interval, next_due, dm, paused, fire_count, label "
        "FROM recurring_reminders"
    ) as cur:
        rows = await cur.fetchall()
    return {r["id"]: _recurring_row(r) for r in rows}


async def count_user_recurring(user_id: int) -> int:
    async with _conn().execute(
        "SELECT COUNT(*) FROM recurring_reminders WHERE target_id=?",
        (str(user_id),),
    ) as cur:
        row = await cur.fetchone()
    return row[0]


def _recurring_row(r: aiosqlite.Row) -> dict:
    return {
        "id": r["id"],
        "target_id": r["target_id"],
        "set_by_id": r["set_by_id"],
        "guild_id": r["guild_id"],
        "channel_id": r["channel_id"],
        "message": r["message"],
        "interval": r["interval"],
        "next_due": r["next_due"],
        "dm": bool(r["dm"]),
        "paused": bool(r["paused"]),
        "fire_count": r["fire_count"],
        "label": r["label"],
    }


register_init(_ensure_recurring_table)
