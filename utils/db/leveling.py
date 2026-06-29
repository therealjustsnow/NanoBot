"""utils/db.leveling — leveling XP/reward.

Part of the utils/db package (split from the former single-file utils/db.py).
Shared connection state + schema machinery live in utils/db/_core.py; this
module holds the leveling XP/reward accessors and registers its table setup with _core so
db.init() creates them in the right order.
"""

from ._core import _conn, _ensure_columns, register_init

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
            announce          INTEGER NOT NULL DEFAULT 1,
            coin_reward       INTEGER NOT NULL DEFAULT 0
        )
    """)
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
    await _conn().commit()
    # Upgrade path for guilds whose level_config predates coin_reward.
    await _ensure_columns("level_config", {"coin_reward": "INTEGER NOT NULL DEFAULT 0"})


# ── XP ───────────────────────────────────────────────────────────────────────
async def add_xp(guild_id: int, user_id: int, amount: int) -> int:
    """Add (or subtract) XP. Clamps at 0. Returns the new XP total."""
    new_xp = max(0, await get_xp(guild_id, user_id) + int(amount))
    await set_xp(guild_id, user_id, new_xp)
    return new_xp


async def set_xp(guild_id: int, user_id: int, amount: int) -> None:
    """Set a member's XP to an absolute value (clamped at 0)."""
    await _conn().execute(
        "INSERT INTO user_levels (guild_id, user_id, xp) VALUES (?,?,?) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET xp=excluded.xp",
        (str(guild_id), str(user_id), max(0, int(amount))),
    )
    await _conn().commit()


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
        async with _conn().execute(
            "SELECT 1 FROM user_levels WHERE guild_id=? AND user_id=?",
            (str(guild_id), str(user_id)),
        ) as cur:
            if not await cur.fetchone():
                return None
    async with _conn().execute(
        "SELECT COUNT(*) FROM user_levels WHERE guild_id=? AND xp > ?",
        (str(guild_id), xp),
    ) as cur:
        ahead = (await cur.fetchone())[0]
    return ahead + 1, xp


async def get_leaderboard(
    guild_id: int, limit: int = 10, offset: int = 0
) -> list[dict]:
    async with _conn().execute(
        "SELECT user_id, xp FROM user_levels WHERE guild_id=? AND xp > 0 "
        "ORDER BY xp DESC, user_id ASC LIMIT ? OFFSET ?",
        (str(guild_id), int(limit), int(offset)),
    ) as cur:
        rows = await cur.fetchall()
    return [{"user_id": int(r["user_id"]), "xp": r["xp"]} for r in rows]


async def count_ranked(guild_id: int) -> int:
    async with _conn().execute(
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
    await _conn().commit()
    return cur.rowcount


# ── Config ─────────────────────────────────────────────────────────────────────
async def get_level_config(guild_id: int) -> dict:
    async with _conn().execute(
        "SELECT enabled, xp_min, xp_max, cooldown, announce_channel, announce, "
        "coin_reward FROM level_config WHERE guild_id=?",
        (str(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    if row:
        return {
            "enabled": bool(row["enabled"]),
            "xp_min": row["xp_min"],
            "xp_max": row["xp_max"],
            "cooldown": row["cooldown"],
            "announce_channel": (
                int(row["announce_channel"]) if row["announce_channel"] else None
            ),
            "announce": bool(row["announce"]),
            "coin_reward": row["coin_reward"],
        }
    return {
        "enabled": False,
        "xp_min": 15,
        "xp_max": 25,
        "cooldown": 60,
        "announce_channel": None,
        "announce": True,
        "coin_reward": 0,
    }


async def set_level_config(guild_id: int, **kwargs) -> None:
    """Upsert level config. Unspecified columns keep their current/default value."""
    current = await get_level_config(guild_id)
    current.update(kwargs)
    await _conn().execute(
        "INSERT INTO level_config "
        "(guild_id, enabled, xp_min, xp_max, cooldown, announce_channel, announce, "
        "coin_reward) "
        "VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(guild_id) DO UPDATE SET enabled=excluded.enabled, "
        "xp_min=excluded.xp_min, xp_max=excluded.xp_max, cooldown=excluded.cooldown, "
        "announce_channel=excluded.announce_channel, announce=excluded.announce, "
        "coin_reward=excluded.coin_reward",
        (
            str(guild_id),
            1 if current["enabled"] else 0,
            int(current["xp_min"]),
            int(current["xp_max"]),
            int(current["cooldown"]),
            str(current["announce_channel"]) if current["announce_channel"] else None,
            1 if current["announce"] else 0,
            int(current["coin_reward"]),
        ),
    )
    await _conn().commit()


# ── Role rewards ─────────────────────────────────────────────────────────────
async def add_level_reward(guild_id: int, level: int, role_id: int) -> None:
    await _conn().execute(
        "INSERT INTO level_rewards (guild_id, level, role_id) VALUES (?,?,?) "
        "ON CONFLICT(guild_id, level) DO UPDATE SET role_id=excluded.role_id",
        (str(guild_id), int(level), str(role_id)),
    )
    await _conn().commit()


async def remove_level_reward(guild_id: int, level: int) -> bool:
    cur = await _conn().execute(
        "DELETE FROM level_rewards WHERE guild_id=? AND level=?",
        (str(guild_id), int(level)),
    )
    await _conn().commit()
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
    await _conn().commit()


async def remove_level_ignored_channel(guild_id: int, channel_id: int) -> bool:
    cur = await _conn().execute(
        "DELETE FROM level_ignored_channels WHERE guild_id=? AND channel_id=?",
        (str(guild_id), str(channel_id)),
    )
    await _conn().commit()
    return cur.rowcount > 0


async def get_level_ignored_channels(guild_id: int) -> set[int]:
    async with _conn().execute(
        "SELECT channel_id FROM level_ignored_channels WHERE guild_id=?",
        (str(guild_id),),
    ) as cur:
        rows = await cur.fetchall()
    return {int(r["channel_id"]) for r in rows}


register_init(_ensure_leveling_tables)
