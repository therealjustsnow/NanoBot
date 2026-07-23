"""utils/db.fishing — fishing minigame storage.

Part of the utils/db package (split from the former single-file utils/db.py).
Shared connection state + schema machinery live in utils/db/_core.py; this
module holds the fishing accessors and registers its table setup with _core so
db.init() creates them in the right order.

Coins themselves live in the economy tables (utils/db/economy.py) — fishing
only records catches, per-species lifetime counts, per-fisher stats, and the
per-guild config. Selling/treasure credits coins via db.add_coins.
"""

import time

from ._core import _conn, register_init


async def _ensure_fishing_tables():
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS fishing_config (
            guild_id  TEXT PRIMARY KEY,
            enabled   INTEGER NOT NULL DEFAULT 1,
            cooldown  INTEGER NOT NULL DEFAULT 60
        )
    """)
    # Per-fisher aggregates. `earned` is lifetime coins from fishing (sales +
    # treasure) and drives the /fish top leaderboard; `best_*` track the
    # heaviest real fish (junk/treasure excluded by the caller).
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS fishing_stats (
            guild_id    TEXT NOT NULL,
            user_id     TEXT NOT NULL,
            rod_level   INTEGER NOT NULL DEFAULT 0,
            last_cast   REAL NOT NULL DEFAULT 0,
            casts       INTEGER NOT NULL DEFAULT 0,
            caught      INTEGER NOT NULL DEFAULT 0,
            earned      INTEGER NOT NULL DEFAULT 0,
            best_key    TEXT NOT NULL DEFAULT '',
            best_weight REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )
    """)
    await _conn().execute(
        "CREATE INDEX IF NOT EXISTS fishing_stats_earned "
        "ON fishing_stats (guild_id, earned DESC)"
    )
    # One row per unsold catch — the bag. Value is fixed at catch time (weight
    # roll included), so selling just sums and deletes.
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS fishing_catches (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id  TEXT NOT NULL,
            user_id   TEXT NOT NULL,
            fish_key  TEXT NOT NULL,
            weight    REAL NOT NULL DEFAULT 0,
            value     INTEGER NOT NULL DEFAULT 0,
            caught_at REAL NOT NULL DEFAULT 0
        )
    """)
    await _conn().execute(
        "CREATE INDEX IF NOT EXISTS fishing_catches_user "
        "ON fishing_catches (guild_id, user_id, fish_key)"
    )
    # Lifetime per-species counts — survives selling; drives /fish dex.
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS fishing_species (
            guild_id TEXT NOT NULL,
            user_id  TEXT NOT NULL,
            fish_key TEXT NOT NULL,
            caught   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, user_id, fish_key)
        )
    """)
    await _conn().commit()


# ── Config ─────────────────────────────────────────────────────────────────────
async def get_fishing_config(guild_id: int) -> dict:
    async with _conn().execute(
        "SELECT enabled, cooldown FROM fishing_config WHERE guild_id=?",
        (str(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    if row:
        return {"enabled": bool(row["enabled"]), "cooldown": row["cooldown"]}
    return {"enabled": True, "cooldown": 60}


async def set_fishing_config(guild_id: int, **kwargs) -> None:
    current = await get_fishing_config(guild_id)
    current.update(kwargs)
    await _conn().execute(
        "INSERT INTO fishing_config (guild_id, enabled, cooldown) VALUES (?,?,?) "
        "ON CONFLICT(guild_id) DO UPDATE SET enabled=excluded.enabled, "
        "cooldown=excluded.cooldown",
        (str(guild_id), 1 if current["enabled"] else 0, int(current["cooldown"])),
    )
    await _conn().commit()


# ── Fisher stats ───────────────────────────────────────────────────────────────
def _fisher_row(row) -> dict:
    return {
        "rod_level": row["rod_level"],
        "last_cast": row["last_cast"],
        "casts": row["casts"],
        "caught": row["caught"],
        "earned": row["earned"],
        "best_key": row["best_key"],
        "best_weight": row["best_weight"],
    }


async def get_fisher(guild_id: int, user_id: int) -> dict:
    async with _conn().execute(
        "SELECT rod_level, last_cast, casts, caught, earned, best_key, best_weight "
        "FROM fishing_stats WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id)),
    ) as cur:
        row = await cur.fetchone()
    if row:
        return _fisher_row(row)
    return {
        "rod_level": 0,
        "last_cast": 0.0,
        "casts": 0,
        "caught": 0,
        "earned": 0,
        "best_key": "",
        "best_weight": 0.0,
    }


async def try_claim_cast(guild_id: int, user_id: int, now: float, cooldown: int) -> int:
    """Atomically claim a cast slot. Returns 0 on success, else seconds left.

    The claim is a single conditional upsert (mirrors try_debit_coins), so two
    concurrent /fish casts can't both pass the cooldown check.
    """
    cur = await _conn().execute(
        "INSERT INTO fishing_stats (guild_id, user_id, last_cast, casts) "
        "VALUES (?,?,?,1) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET "
        "last_cast=excluded.last_cast, casts=casts+1 "
        "WHERE excluded.last_cast - fishing_stats.last_cast >= ?",
        (str(guild_id), str(user_id), float(now), int(cooldown)),
    )
    await _conn().commit()
    if cur.rowcount > 0:
        return 0
    fisher = await get_fisher(guild_id, user_id)
    return max(1, int(cooldown - (now - fisher["last_cast"])))


async def record_catch(
    guild_id: int,
    user_id: int,
    fish_key: str,
    weight: float,
    value: int,
    *,
    track_best: bool = True,
    caught_at: float | None = None,
) -> None:
    """Store a catch in the bag, bump lifetime counts, and update the best fish.

    `track_best=False` skips the personal-best check (junk isn't a trophy).
    """
    gid, uid = str(guild_id), str(user_id)
    await _conn().execute(
        "INSERT INTO fishing_catches (guild_id, user_id, fish_key, weight, value, "
        "caught_at) VALUES (?,?,?,?,?,?)",
        (gid, uid, fish_key, float(weight), int(value), caught_at or time.time()),
    )
    await _conn().execute(
        "INSERT INTO fishing_species (guild_id, user_id, fish_key, caught) "
        "VALUES (?,?,?,1) ON CONFLICT(guild_id, user_id, fish_key) "
        "DO UPDATE SET caught=caught+1",
        (gid, uid, fish_key),
    )
    await _conn().execute(
        "INSERT INTO fishing_stats (guild_id, user_id, caught) VALUES (?,?,1) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET caught=caught+1",
        (gid, uid),
    )
    if track_best:
        await _conn().execute(
            "UPDATE fishing_stats SET best_key=?, best_weight=? "
            "WHERE guild_id=? AND user_id=? AND best_weight < ?",
            (fish_key, float(weight), gid, uid, float(weight)),
        )
    await _conn().commit()


async def add_fishing_earned(guild_id: int, user_id: int, amount: int) -> None:
    """Bump the lifetime fishing-earnings stat (sales + treasure)."""
    await _conn().execute(
        "INSERT INTO fishing_stats (guild_id, user_id, earned) VALUES (?,?,MAX(0,?)) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET earned=MAX(0, earned + ?)",
        (str(guild_id), str(user_id), int(amount), int(amount)),
    )
    await _conn().commit()


async def set_rod_level(
    guild_id: int, user_id: int, new_level: int, *, expected: int
) -> bool:
    """Conditionally advance the rod tier (first upgrader wins a race).

    Returns False when the stored level no longer matches `expected` — the
    caller should refund the debited coins.
    """
    if expected == 0:
        # A fisher with no stats row yet is implicitly at level 0.
        await _conn().execute(
            "INSERT OR IGNORE INTO fishing_stats (guild_id, user_id) VALUES (?,?)",
            (str(guild_id), str(user_id)),
        )
    cur = await _conn().execute(
        "UPDATE fishing_stats SET rod_level=? "
        "WHERE guild_id=? AND user_id=? AND rod_level=?",
        (int(new_level), str(guild_id), str(user_id), int(expected)),
    )
    await _conn().commit()
    return cur.rowcount > 0


# ── Bag (unsold catches) ───────────────────────────────────────────────────────
async def get_bag(guild_id: int, user_id: int) -> list[dict]:
    """Unsold catches grouped by species: [{fish_key, qty, total_value}, …]."""
    async with _conn().execute(
        "SELECT fish_key, COUNT(*) AS qty, SUM(value) AS total_value "
        "FROM fishing_catches WHERE guild_id=? AND user_id=? "
        "GROUP BY fish_key ORDER BY total_value DESC, fish_key ASC",
        (str(guild_id), str(user_id)),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {"fish_key": r["fish_key"], "qty": r["qty"], "total_value": r["total_value"]}
        for r in rows
    ]


async def sell_catches(
    guild_id: int, user_id: int, fish_key: str | None = None
) -> tuple[int, int]:
    """Delete bag rows (one species, or all) and return (count, total_value).

    Not internally atomic — the cog serialises per-user sells with a lock (the
    /daily pattern) so a double-send can't credit the same fish twice.
    """
    gid, uid = str(guild_id), str(user_id)
    sql = "SELECT COUNT(*), COALESCE(SUM(value), 0) FROM fishing_catches WHERE guild_id=? AND user_id=?"
    params: list = [gid, uid]
    if fish_key is not None:
        sql += " AND fish_key=?"
        params.append(fish_key)
    async with _conn().execute(sql, params) as cur:
        count, total = await cur.fetchone()
    if count:
        await _conn().execute(
            sql.replace("SELECT COUNT(*), COALESCE(SUM(value), 0) FROM", "DELETE FROM"),
            params,
        )
        await _conn().commit()
    return count, total


async def get_species_counts(guild_id: int, user_id: int) -> dict[str, int]:
    """Lifetime per-species catch counts (the /fish dex)."""
    async with _conn().execute(
        "SELECT fish_key, caught FROM fishing_species WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id)),
    ) as cur:
        rows = await cur.fetchall()
    return {r["fish_key"]: r["caught"] for r in rows}


# ── Leaderboard (lifetime fishing earnings) ────────────────────────────────────
async def get_fishing_leaderboard(
    guild_id: int, limit: int = 10, offset: int = 0
) -> list[dict]:
    async with _conn().execute(
        "SELECT user_id, earned, caught FROM fishing_stats "
        "WHERE guild_id=? AND earned > 0 "
        "ORDER BY earned DESC, user_id ASC LIMIT ? OFFSET ?",
        (str(guild_id), int(limit), int(offset)),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {"user_id": int(r["user_id"]), "earned": r["earned"], "caught": r["caught"]}
        for r in rows
    ]


async def count_fishers(guild_id: int) -> int:
    async with _conn().execute(
        "SELECT COUNT(*) FROM fishing_stats WHERE guild_id=? AND earned > 0",
        (str(guild_id),),
    ) as cur:
        return (await cur.fetchone())[0]


async def get_fishing_rank(guild_id: int, user_id: int) -> tuple[int, int] | None:
    """Return (rank, earned) by lifetime earnings; None if nothing earned yet."""
    fisher = await get_fisher(guild_id, user_id)
    if fisher["earned"] <= 0:
        return None
    async with _conn().execute(
        "SELECT COUNT(*) FROM fishing_stats WHERE guild_id=? AND earned > ?",
        (str(guild_id), fisher["earned"]),
    ) as cur:
        ahead = (await cur.fetchone())[0]
    return ahead + 1, fisher["earned"]


register_init(_ensure_fishing_tables)
