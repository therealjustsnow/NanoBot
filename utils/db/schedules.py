"""utils/db.schedules — timed unban/slowmode schedule.

Part of the utils/db package (split from the former single-file utils/db.py).
Shared connection state + schema machinery live in utils/db/_core.py; this
module holds the timed unban/slowmode schedule accessors and registers its table setup with _core so
db.init() creates them in the right order.
"""

from ._core import _conn

# ══════════════════════════════════════════════════════════════════════════════
#  Unban schedules
# ══════════════════════════════════════════════════════════════════════════════


async def set_unban(key: str, guild_id: int, user_id: int, until: float) -> None:
    await _conn().execute(
        "INSERT INTO unban_schedules (key, guild_id, user_id, until) VALUES (?,?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET until=excluded.until",
        (key, str(guild_id), str(user_id), until),
    )
    await _conn().commit()


async def remove_unban(key: str) -> None:
    await _conn().execute("DELETE FROM unban_schedules WHERE key=?", (key,))
    await _conn().commit()


async def get_all_unbans() -> dict:
    async with _conn().execute(
        "SELECT key, guild_id, user_id, until FROM unban_schedules"
    ) as cur:
        rows = await cur.fetchall()
    return {
        r["key"]: {
            "guild_id": r["guild_id"],
            "user_id": r["user_id"],
            "until": r["until"],
        }
        for r in rows
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Slow schedules
# ══════════════════════════════════════════════════════════════════════════════


async def set_slow(channel_id: int, guild_id: int, until: float) -> None:
    await _conn().execute(
        "INSERT INTO slow_schedules (channel_id, guild_id, until) VALUES (?,?,?) "
        "ON CONFLICT(channel_id) DO UPDATE SET until=excluded.until",
        (str(channel_id), str(guild_id), until),
    )
    await _conn().commit()


async def remove_slow(channel_id: int) -> None:
    await _conn().execute(
        "DELETE FROM slow_schedules WHERE channel_id=?", (str(channel_id),)
    )
    await _conn().commit()


async def get_all_slows() -> dict:
    async with _conn().execute(
        "SELECT channel_id, guild_id, until FROM slow_schedules"
    ) as cur:
        rows = await cur.fetchall()
    return {
        r["channel_id"]: {"guild_id": r["guild_id"], "until": r["until"]} for r in rows
    }
