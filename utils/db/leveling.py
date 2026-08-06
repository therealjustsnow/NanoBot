"""utils/db.leveling — leveling XP/reward.

Part of the utils/db package (split from the former single-file utils/db.py).
Shared connection state + schema machinery live in utils/db/_core.py; this
module holds the leveling XP/reward accessors and registers its table setup with _core so
db.init() creates them in the right order.
"""

from . import _cache
from ._core import (
    _commit,
    _conn,
    _ensure_columns,
    _read_conn,
    fetch_one_returning,
    register_init,
)

# ══════════════════════════════════════════════════════════════════════════════
#  Leveling (per-guild XP + level rewards)
# ══════════════════════════════════════════════════════════════════════════════


async def _ensure_leveling_tables():
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS user_levels (
            guild_id  TEXT NOT NULL,
            user_id   TEXT NOT NULL,
            xp        INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )
    """)
    await _conn().execute(
        "CREATE INDEX IF NOT EXISTS user_levels_guild_xp "
        "ON user_levels (guild_id, xp DESC)"
    )
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS level_config (
            guild_id          TEXT PRIMARY KEY,
            enabled           INTEGER NOT NULL DEFAULT 0,
            xp_min            INTEGER NOT NULL DEFAULT 15,
            xp_max            INTEGER NOT NULL DEFAULT 25,
            cooldown          INTEGER NOT NULL DEFAULT 60,
            announce_channel  TEXT,
            announce          INTEGER NOT NULL DEFAULT 1
        )
    """)
    # Where *global* (account-wide) level-ups are announced in this guild. The
    # level itself is global, but a channel isn't — the server owns which of
    # its own channels a stray "level up!" is allowed to land in.
    await _ensure_columns(
        "level_config",
        {
            "global_announce": "INTEGER NOT NULL DEFAULT 1",
            "global_announce_channel": "TEXT",
        },
    )
    # Whether this guild participates in the account-wide level system at
    # all. Default on — most servers want it. Off means messages sent here
    # earn no global XP and a pending global level-up never posts in this
    # guild (still delivered by DM) — for a server where the bot is barely
    # used (e.g. automod/tags only) and a stray "level up!" would be noise.
    await _ensure_columns(
        "level_config",
        {"global_xp_enabled": "INTEGER NOT NULL DEFAULT 1"},
    )
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS level_rewards (
            guild_id  TEXT NOT NULL,
            level     INTEGER NOT NULL,
            role_id   TEXT NOT NULL,
            PRIMARY KEY (guild_id, level)
        )
    """)
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS level_ignored_channels (
            guild_id    TEXT NOT NULL,
            channel_id  TEXT NOT NULL,
            PRIMARY KEY (guild_id, channel_id)
        )
    """)
    await _commit()


# ── XP ───────────────────────────────────────────────────────────────────────
async def add_xp(guild_id: int, user_id: int, amount: int) -> int:
    """Add (or subtract) XP atomically. Clamps at 0. Returns the new XP total.

    One SQL statement, like economy.add_coins: the old read-then-write lost an
    award whenever two of a member's messages were handled concurrently, and
    this is the highest-frequency write in the bot.
    """
    amount = int(amount)
    row = await fetch_one_returning(
        "INSERT INTO user_levels (guild_id, user_id, xp) VALUES (?,?,MAX(0,?)) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET xp=MAX(0, xp + ?)",
        (str(guild_id), str(user_id), amount, amount),
        returning="xp",
    )
    if row is not None:
        return row["xp"]
    return await get_xp(guild_id, user_id)


async def set_xp(guild_id: int, user_id: int, amount: int) -> None:
    """Set a member's XP to an absolute value (clamped at 0)."""
    await _conn().execute(
        "INSERT INTO user_levels (guild_id, user_id, xp) VALUES (?,?,?) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET xp=excluded.xp",
        (str(guild_id), str(user_id), max(0, int(amount))),
    )
    await _commit()


async def get_xp(guild_id: int, user_id: int) -> int:
    async with _conn().execute(
        "SELECT xp FROM user_levels WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id)),
    ) as cur:
        row = await cur.fetchone()
    return row["xp"] if row else 0


async def get_rank(guild_id: int, user_id: int) -> tuple[int, int] | None:
    """Return (rank, xp) for a member; rank is 1-based. None if no XP row."""
    xp = await get_xp(guild_id, user_id)
    if xp <= 0:
        async with _read_conn().execute(
            "SELECT 1 FROM user_levels WHERE guild_id=? AND user_id=?",
            (str(guild_id), str(user_id)),
        ) as cur:
            if not await cur.fetchone():
                return None
    async with _read_conn().execute(
        "SELECT COUNT(*) FROM user_levels WHERE guild_id=? AND xp > ?",
        (str(guild_id), xp),
    ) as cur:
        ahead = (await cur.fetchone())[0]
    return ahead + 1, xp


async def get_leaderboard(
    guild_id: int, limit: int = 10, offset: int = 0
) -> list[dict]:
    async with _read_conn().execute(
        "SELECT user_id, xp FROM user_levels WHERE guild_id=? AND xp > 0 "
        "ORDER BY xp DESC, user_id ASC LIMIT ? OFFSET ?",
        (str(guild_id), int(limit), int(offset)),
    ) as cur:
        rows = await cur.fetchall()
    return [{"user_id": int(r["user_id"]), "xp": r["xp"]} for r in rows]


async def count_ranked(guild_id: int) -> int:
    async with _read_conn().execute(
        "SELECT COUNT(*) FROM user_levels WHERE guild_id=? AND xp > 0",
        (str(guild_id),),
    ) as cur:
        return (await cur.fetchone())[0]


async def reset_levels(guild_id: int, user_id: int | None = None) -> int:
    """Wipe XP for one member, or the whole guild. Returns rows removed."""
    if user_id is None:
        cur = await _conn().execute(
            "DELETE FROM user_levels WHERE guild_id=?", (str(guild_id),)
        )
    else:
        cur = await _conn().execute(
            "DELETE FROM user_levels WHERE guild_id=? AND user_id=?",
            (str(guild_id), str(user_id)),
        )
    await _commit()
    return cur.rowcount


# ── Config ─────────────────────────────────────────────────────────────────────
async def get_level_config(guild_id: int) -> dict:
    cached = _cache.get("level_config", guild_id)
    if cached is not None:
        return cached
    async with _conn().execute(
        "SELECT enabled, xp_min, xp_max, cooldown, announce_channel, announce, "
        "global_announce, global_announce_channel, global_xp_enabled "
        "FROM level_config WHERE guild_id=?",
        (str(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    if row:
        return _cache.put(
            "level_config",
            guild_id,
            {
                "enabled": bool(row["enabled"]),
                "xp_min": row["xp_min"],
                "xp_max": row["xp_max"],
                "cooldown": row["cooldown"],
                "announce_channel": (
                    int(row["announce_channel"]) if row["announce_channel"] else None
                ),
                "announce": bool(row["announce"]),
                "global_announce": bool(row["global_announce"]),
                "global_announce_channel": (
                    int(row["global_announce_channel"])
                    if row["global_announce_channel"]
                    else None
                ),
                "global_xp_enabled": bool(row["global_xp_enabled"]),
            },
        )
    return _cache.put(
        "level_config",
        guild_id,
        {
            "enabled": False,
            "xp_min": 15,
            "xp_max": 25,
            "cooldown": 60,
            "announce_channel": None,
            "announce": True,
            "global_announce": True,
            "global_announce_channel": None,
            "global_xp_enabled": True,
        },
    )


async def set_level_config(guild_id: int, **kwargs) -> None:
    """Upsert level config. Unspecified columns keep their current/default value."""
    current = await get_level_config(guild_id)
    current.update(kwargs)
    await _conn().execute(
        "INSERT INTO level_config "
        "(guild_id, enabled, xp_min, xp_max, cooldown, announce_channel, announce, "
        "global_announce, global_announce_channel, global_xp_enabled) "
        "VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(guild_id) DO UPDATE SET enabled=excluded.enabled, "
        "xp_min=excluded.xp_min, xp_max=excluded.xp_max, cooldown=excluded.cooldown, "
        "announce_channel=excluded.announce_channel, announce=excluded.announce, "
        "global_announce=excluded.global_announce, "
        "global_announce_channel=excluded.global_announce_channel, "
        "global_xp_enabled=excluded.global_xp_enabled",
        (
            str(guild_id),
            1 if current["enabled"] else 0,
            int(current["xp_min"]),
            int(current["xp_max"]),
            int(current["cooldown"]),
            str(current["announce_channel"]) if current["announce_channel"] else None,
            1 if current["announce"] else 0,
            1 if current["global_announce"] else 0,
            (
                str(current["global_announce_channel"])
                if current["global_announce_channel"]
                else None
            ),
            1 if current["global_xp_enabled"] else 0,
        ),
    )
    await _commit()
    _cache.invalidate("level_config", guild_id)


# ── Role rewards ─────────────────────────────────────────────────────────────
async def add_level_reward(guild_id: int, level: int, role_id: int) -> None:
    await _conn().execute(
        "INSERT INTO level_rewards (guild_id, level, role_id) VALUES (?,?,?) "
        "ON CONFLICT(guild_id, level) DO UPDATE SET role_id=excluded.role_id",
        (str(guild_id), int(level), str(role_id)),
    )
    await _commit()


async def remove_level_reward(guild_id: int, level: int) -> bool:
    cur = await _conn().execute(
        "DELETE FROM level_rewards WHERE guild_id=? AND level=?",
        (str(guild_id), int(level)),
    )
    await _commit()
    return cur.rowcount > 0


async def get_level_rewards(guild_id: int) -> dict[int, int]:
    """Return {level: role_id} for a guild, ascending by level."""
    async with _conn().execute(
        "SELECT level, role_id FROM level_rewards WHERE guild_id=? ORDER BY level ASC",
        (str(guild_id),),
    ) as cur:
        rows = await cur.fetchall()
    return {r["level"]: int(r["role_id"]) for r in rows}


# ── Ignored channels ───────────────────────────────────────────────────────────
async def add_level_ignored_channel(guild_id: int, channel_id: int) -> None:
    await _conn().execute(
        "INSERT OR IGNORE INTO level_ignored_channels (guild_id, channel_id) "
        "VALUES (?,?)",
        (str(guild_id), str(channel_id)),
    )
    await _commit()
    _cache.invalidate("level_ignored_channels", guild_id)


async def remove_level_ignored_channel(guild_id: int, channel_id: int) -> bool:
    cur = await _conn().execute(
        "DELETE FROM level_ignored_channels WHERE guild_id=? AND channel_id=?",
        (str(guild_id), str(channel_id)),
    )
    await _commit()
    _cache.invalidate("level_ignored_channels", guild_id)
    return cur.rowcount > 0


async def get_level_ignored_channels(guild_id: int) -> set[int]:
    cached = _cache.get("level_ignored_channels", guild_id)
    if cached is not None:
        return cached
    async with _conn().execute(
        "SELECT channel_id FROM level_ignored_channels WHERE guild_id=?",
        (str(guild_id),),
    ) as cur:
        rows = await cur.fetchall()
    return _cache.put(
        "level_ignored_channels", guild_id, {int(r["channel_id"]) for r in rows}
    )


register_init(_ensure_leveling_tables)
