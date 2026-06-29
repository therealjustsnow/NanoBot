"""utils/db.reminders — one-time reminder.

Part of the utils/db package (split from the former single-file utils/db.py).
Shared connection state + schema machinery live in utils/db/_core.py; this
module holds the one-time reminder accessors and registers its table setup with _core so
db.init() creates them in the right order.
"""

import aiosqlite


from ._core import _conn

# ══════════════════════════════════════════════════════════════════════════════
#  Reminders
# ══════════════════════════════════════════════════════════════════════════════


async def reminder_id_exists(rid: str) -> bool:
    async with _conn().execute(
        "SELECT 1 FROM reminders WHERE id=? LIMIT 1", (rid,)
    ) as cur:
        return await cur.fetchone() is not None


async def set_reminder(info: dict) -> None:
    await _conn().execute(
        """INSERT OR IGNORE INTO reminders
           (id, target_id, set_by_id, guild_id, channel_id, message, due, duration, dm)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            info["id"],
            info["target_id"],
            info["set_by_id"],
            info["guild_id"],
            info["channel_id"],
            info["message"],
            info["due"],
            info.get("duration", 0),
            1 if info.get("dm", True) else 0,
        ),
    )
    await _conn().commit()


async def remove_reminder(rid: str) -> None:
    await _conn().execute("DELETE FROM reminders WHERE id=?", (rid,))
    await _conn().commit()


async def get_all_reminders() -> dict:
    async with _conn().execute(
        "SELECT id, target_id, set_by_id, guild_id, channel_id, message, due, duration, dm "
        "FROM reminders"
    ) as cur:
        rows = await cur.fetchall()
    return {r["id"]: _reminder_row(r) for r in rows}


async def get_user_reminders(user_id: int) -> dict:
    async with _conn().execute(
        "SELECT id, target_id, set_by_id, guild_id, channel_id, message, due, duration, dm "
        "FROM reminders WHERE target_id=?",
        (str(user_id),),
    ) as cur:
        rows = await cur.fetchall()
    return {r["id"]: _reminder_row(r) for r in rows}


async def count_user_reminders(user_id: int) -> int:
    async with _conn().execute(
        "SELECT COUNT(*) FROM reminders WHERE target_id=?", (str(user_id),)
    ) as cur:
        row = await cur.fetchone()
    return row[0]


async def get_sent_reminders(user_id: int) -> dict:
    """Reminders this user set for OTHER people (set_by = user, target != user)."""
    async with _conn().execute(
        "SELECT id, target_id, set_by_id, guild_id, channel_id, message, due, duration, dm "
        "FROM reminders WHERE set_by_id=? AND target_id!=?",
        (str(user_id), str(user_id)),
    ) as cur:
        rows = await cur.fetchall()
    return {r["id"]: _reminder_row(r) for r in rows}


def _reminder_row(r: aiosqlite.Row) -> dict:
    return {
        "id": r["id"],
        "target_id": r["target_id"],
        "set_by_id": r["set_by_id"],
        "guild_id": r["guild_id"],
        "channel_id": r["channel_id"],
        "message": r["message"],
        "due": r["due"],
        "duration": r["duration"],
        "dm": bool(r["dm"]),
    }
