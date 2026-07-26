"""utils/db.fishing — fishing minigame storage.

Part of the utils/db package (split from the former single-file utils/db.py).
Shared connection state + schema machinery live in utils/db/_core.py; this
module holds the fishing accessors and registers its table setup with _core so
db.init() creates them in the right order.

Coins themselves live in the economy tables (utils/db/economy.py) — fishing
only records catches, per-species lifetime counts, per-fisher stats, and the
per-guild config. Selling/treasure credits coins via db.add_coins.

Everything a *fisher* owns or has earned is GLOBAL (keyed by user_id alone):
rod tier, XP/level, streak, bag, dex, quests, lifetime earnings. Only
``fishing_config`` (the on/off switch) stays per-guild, because that's a
server's own rule, not the member's progress. The cast cooldown is no longer
stored at all — it's one fixed, bot-wide constant
(``cogs.fishing.constants.CAST_COOLDOWN``), since the claim it gates is per
user and a per-server number stopped meaning anything once a member fished in
two servers.
"""

import time

from . import _cache
from ._core import (
    count_ahead,
    _commit,
    _conn,
    _read_conn,
    _ensure_columns,
    fetch_one_returning,
    register_init,
    rows_for_users,
)


async def _ensure_fishing_tables():
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS fishing_config (
            guild_id  TEXT PRIMARY KEY,
            enabled   INTEGER NOT NULL DEFAULT 1
        )
    """)
    # Per-fisher aggregates. `earned` is lifetime coins from fishing (sales +
    # treasure) and drives the /fish top leaderboard; `best_*` track the
    # heaviest real fish (junk/treasure excluded by the caller).
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS fishing_stats (
            user_id     TEXT PRIMARY KEY,
            rod_level   INTEGER NOT NULL DEFAULT 0,
            last_cast   REAL NOT NULL DEFAULT 0,
            casts       INTEGER NOT NULL DEFAULT 0,
            caught      INTEGER NOT NULL DEFAULT 0,
            earned      INTEGER NOT NULL DEFAULT 0,
            best_key    TEXT NOT NULL DEFAULT '',
            best_weight REAL NOT NULL DEFAULT 0
        )
    """)
    await _conn().execute(
        "CREATE INDEX IF NOT EXISTS fishing_stats_earned "
        "ON fishing_stats (earned DESC)"
    )
    await _commit()
    # Fishing XP/levels and the once-a-day streak stamp — added after the
    # baseline table.
    await _ensure_columns(
        "fishing_stats",
        {
            "xp": "INTEGER NOT NULL DEFAULT 0",
            "streak_days": "INTEGER NOT NULL DEFAULT 0",
            "last_day": "INTEGER NOT NULL DEFAULT 0",
        },
    )
    # Every other GLOBAL_STATS column needs its own index too (after the
    # _ensure_columns above, since two of them are added there): without one,
    # `/fish global <stat>` is a full scan plus a full sort of every angler in
    # the database — measured ~330x slower than the indexed board at 50k rows —
    # and it pays that twice, once for the page and once for the COUNT.
    for _col in ("caught", "casts", "best_weight", "xp", "streak_days"):
        await _conn().execute(
            f"CREATE INDEX IF NOT EXISTS fishing_stats_{_col} "
            f"ON fishing_stats ({_col} DESC)"
        )
    await _commit()
    # One row per unsold catch — the bag. Value is fixed at catch time (weight
    # roll included), so selling just sums and deletes.
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS fishing_catches (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   TEXT NOT NULL,
            fish_key  TEXT NOT NULL,
            weight    REAL NOT NULL DEFAULT 0,
            value     INTEGER NOT NULL DEFAULT 0,
            caught_at REAL NOT NULL DEFAULT 0
        )
    """)
    await _conn().execute(
        "CREATE INDEX IF NOT EXISTS fishing_catches_user "
        "ON fishing_catches (user_id, fish_key)"
    )
    # Lifetime per-species counts — survives selling; drives /fish dex.
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS fishing_species (
            user_id  TEXT NOT NULL,
            fish_key TEXT NOT NULL,
            caught   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, fish_key)
        )
    """)
    # One deterministically-generated quest per member per UTC day (see
    # cogs.fishing.helpers.generate_quest). `target`/`quest_key` are written on
    # first read of the day; `progress` is bumped by casts/sells that match the
    # quest, and `claimed` guards the one-time reward award.
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS fishing_quests (
            user_id   TEXT NOT NULL,
            day       INTEGER NOT NULL,
            quest_key TEXT NOT NULL,
            target    INTEGER NOT NULL DEFAULT 0,
            progress  INTEGER NOT NULL DEFAULT 0,
            claimed   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, day)
        )
    """)
    await _commit()


# ── Config ─────────────────────────────────────────────────────────────────────
async def get_fishing_config(guild_id: int) -> dict:
    cached = _cache.get("fishing_config", guild_id)
    if cached is not None:
        return cached
    async with _conn().execute(
        "SELECT enabled FROM fishing_config WHERE guild_id=?",
        (str(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    enabled = bool(row["enabled"]) if row else True
    return _cache.put("fishing_config", guild_id, {"enabled": enabled})


async def set_fishing_config(guild_id: int, **kwargs) -> None:
    current = await get_fishing_config(guild_id)
    current.update(kwargs)
    await _conn().execute(
        "INSERT INTO fishing_config (guild_id, enabled) VALUES (?,?) "
        "ON CONFLICT(guild_id) DO UPDATE SET enabled=excluded.enabled",
        (str(guild_id), 1 if current["enabled"] else 0),
    )
    await _commit()
    _cache.invalidate("fishing_config", guild_id)


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
        "xp": row["xp"],
        "streak_days": row["streak_days"],
        "last_day": row["last_day"],
    }


async def get_fisher(user_id: int) -> dict:
    async with _conn().execute(
        "SELECT rod_level, last_cast, casts, caught, earned, best_key, best_weight, "
        "xp, streak_days, last_day FROM fishing_stats WHERE user_id=?",
        (str(user_id),),
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
        "xp": 0,
        "streak_days": 0,
        "last_day": 0,
    }


async def try_claim_cast(user_id: int, now: float, cooldown: int) -> int:
    """Atomically claim a cast slot. Returns 0 on success, else seconds left.

    The claim is a single conditional upsert (mirrors try_debit_coins), so two
    concurrent /fish casts can't both pass the cooldown check.
    """
    cur = await _conn().execute(
        "INSERT INTO fishing_stats (user_id, last_cast, casts) VALUES (?,?,1) "
        "ON CONFLICT(user_id) DO UPDATE SET "
        "last_cast=excluded.last_cast, casts=casts+1 "
        "WHERE excluded.last_cast - fishing_stats.last_cast >= ?",
        (str(user_id), float(now), int(cooldown)),
    )
    await _commit()
    if cur.rowcount > 0:
        return 0
    fisher = await get_fisher(user_id)
    return max(1, int(cooldown - (now - fisher["last_cast"])))


async def record_catch(
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
    uid = str(user_id)
    await _conn().execute(
        "INSERT INTO fishing_catches (user_id, fish_key, weight, value, caught_at) "
        "VALUES (?,?,?,?,?)",
        (uid, fish_key, float(weight), int(value), caught_at or time.time()),
    )
    await _conn().execute(
        "INSERT INTO fishing_species (user_id, fish_key, caught) VALUES (?,?,1) "
        "ON CONFLICT(user_id, fish_key) DO UPDATE SET caught=caught+1",
        (uid, fish_key),
    )
    await _conn().execute(
        "INSERT INTO fishing_stats (user_id, caught) VALUES (?,1) "
        "ON CONFLICT(user_id) DO UPDATE SET caught=caught+1",
        (uid,),
    )
    if track_best:
        await _conn().execute(
            "UPDATE fishing_stats SET best_key=?, best_weight=? "
            "WHERE user_id=? AND best_weight < ?",
            (fish_key, float(weight), uid, float(weight)),
        )
    await _commit()


async def add_fishing_earned(user_id: int, amount: int) -> None:
    """Bump the lifetime fishing-earnings stat (sales + treasure)."""
    await _conn().execute(
        "INSERT INTO fishing_stats (user_id, earned) VALUES (?,MAX(0,?)) "
        "ON CONFLICT(user_id) DO UPDATE SET earned=MAX(0, earned + ?)",
        (str(user_id), int(amount), int(amount)),
    )
    await _commit()


async def set_rod_level(user_id: int, new_level: int, *, expected: int) -> bool:
    """Conditionally advance the rod tier (first upgrader wins a race).

    Returns False when the stored level no longer matches `expected` — the
    caller should refund the debited coins.
    """
    if expected == 0:
        # A fisher with no stats row yet is implicitly at level 0.
        await _conn().execute(
            "INSERT OR IGNORE INTO fishing_stats (user_id) VALUES (?)",
            (str(user_id),),
        )
    cur = await _conn().execute(
        "UPDATE fishing_stats SET rod_level=? WHERE user_id=? AND rod_level=?",
        (int(new_level), str(user_id), int(expected)),
    )
    await _commit()
    return cur.rowcount > 0


# ── XP / levels ────────────────────────────────────────────────────────────────
async def add_fishing_xp(user_id: int, amount: int) -> int:
    """Add (fishing) XP atomically. Returns the new lifetime XP total."""
    uid = str(user_id)
    row = await fetch_one_returning(
        "INSERT INTO fishing_stats (user_id, xp) VALUES (?,MAX(0,?)) "
        "ON CONFLICT(user_id) DO UPDATE SET xp=MAX(0, xp + ?)",
        (uid, int(amount), int(amount)),
        returning="xp",
    )
    if row is None:
        async with _conn().execute(
            "SELECT xp FROM fishing_stats WHERE user_id=?", (uid,)
        ) as cur:
            row = await cur.fetchone()
    return row["xp"] if row else 0


# ── Daily streak ─────────────────────────────────────────────────────────────────
async def try_claim_daily_streak(user_id: int, day: int, streak: int) -> bool:
    """Stamp the first-cast-of-the-day streak update (conditional UPDATE so a
    concurrent second cast the same day can't double-claim the streak bonus).

    Requires an existing fishing_stats row (the cast-cooldown claim always
    creates one first). Returns True when this call actually advanced the day.
    """
    cur = await _conn().execute(
        "UPDATE fishing_stats SET last_day=?, streak_days=? "
        "WHERE user_id=? AND last_day<?",
        (int(day), int(streak), str(user_id), int(day)),
    )
    await _commit()
    return cur.rowcount > 0


# ── Bag (unsold catches) ───────────────────────────────────────────────────────
async def get_bag(user_id: int) -> list[dict]:
    """Unsold catches grouped by species: [{fish_key, qty, total_value}, …]."""
    async with _conn().execute(
        "SELECT fish_key, COUNT(*) AS qty, SUM(value) AS total_value "
        "FROM fishing_catches WHERE user_id=? "
        "GROUP BY fish_key ORDER BY total_value DESC, fish_key ASC",
        (str(user_id),),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {"fish_key": r["fish_key"], "qty": r["qty"], "total_value": r["total_value"]}
        for r in rows
    ]


async def count_bag(user_id: int) -> int:
    """How many unsold catches a member is carrying.

    /fish cast only wants the number for its footer; get_bag's GROUP BY builds
    a per-species breakdown over a bag with no upper bound to produce it.
    """
    async with _conn().execute(
        "SELECT COUNT(*) FROM fishing_catches WHERE user_id=?", (str(user_id),)
    ) as cur:
        return (await cur.fetchone())[0]


async def sell_catches(user_id: int, fish_key: str | None = None) -> tuple[int, int]:
    """Delete bag rows (one species, or all) and return (count, total_value).

    Not internally atomic — the cog serialises per-user sells with a lock (the
    /daily pattern) so a double-send can't credit the same fish twice.
    """
    sql = (
        "SELECT COUNT(*), COALESCE(SUM(value), 0) FROM fishing_catches WHERE user_id=?"
    )
    params: list = [str(user_id)]
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
        await _commit()
    return count, total


async def get_species_counts(user_id: int) -> dict[str, int]:
    """Lifetime per-species catch counts (the /fish dex)."""
    async with _conn().execute(
        "SELECT fish_key, caught FROM fishing_species WHERE user_id=?",
        (str(user_id),),
    ) as cur:
        rows = await cur.fetchall()
    return {r["fish_key"]: r["caught"] for r in rows}


# ── Leaderboard (lifetime fishing earnings) ────────────────────────────────────
async def get_fishing_leaderboard(limit: int = 10, offset: int = 0) -> list[dict]:
    """Top anglers by lifetime earnings, across every server."""
    async with _read_conn().execute(
        "SELECT user_id, earned, caught FROM fishing_stats WHERE earned > 0 "
        "ORDER BY earned DESC, user_id ASC LIMIT ? OFFSET ?",
        (int(limit), int(offset)),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {"user_id": int(r["user_id"]), "earned": r["earned"], "caught": r["caught"]}
        for r in rows
    ]


async def get_fishing_leaderboard_for(user_ids) -> list[dict]:
    """The same rows filtered to a set of users (a server-scoped board)."""
    rows = await rows_for_users(
        "SELECT user_id, earned, caught FROM fishing_stats",
        user_ids,
        positive_col="earned",
    )
    return [
        {"user_id": int(r["user_id"]), "earned": r["earned"], "caught": r["caught"]}
        for r in rows
    ]


async def count_fishers() -> int:
    async with _read_conn().execute(
        "SELECT COUNT(*) FROM fishing_stats WHERE earned > 0"
    ) as cur:
        return (await cur.fetchone())[0]


async def get_fishing_rank(user_id: int) -> tuple[int, int] | None:
    """Return (global rank, earned) by lifetime earnings; None if nothing yet."""
    fisher = await get_fisher(user_id)
    if fisher["earned"] <= 0:
        return None
    ahead = await count_ahead(
        "SELECT COUNT(*) FROM fishing_stats WHERE earned > ?", fisher["earned"]
    )
    return ahead + 1, fisher["earned"]


# ── Daily quest ──────────────────────────────────────────────────────────────────
async def get_or_create_quest(
    user_id: int, day: int, quest_key: str, target: int
) -> dict:
    """Fetch today's quest row, creating it (deterministically, per the caller's
    generated quest_key/target) on first read of the day. Idempotent."""
    uid, day = str(user_id), int(day)
    await _conn().execute(
        "INSERT OR IGNORE INTO fishing_quests "
        "(user_id, day, quest_key, target, progress, claimed) VALUES (?,?,?,?,0,0)",
        (uid, day, quest_key, int(target)),
    )
    await _commit()
    async with _conn().execute(
        "SELECT quest_key, target, progress, claimed FROM fishing_quests "
        "WHERE user_id=? AND day=?",
        (uid, day),
    ) as cur:
        row = await cur.fetchone()
    return {
        "quest_key": row["quest_key"],
        "target": row["target"],
        "progress": row["progress"],
        "claimed": bool(row["claimed"]),
    }


async def bump_quest_progress(user_id: int, day: int, amount: int) -> int:
    """Advance today's quest progress (clamped at target). Returns new progress.

    A no-op (returning the current progress unchanged) when amount <= 0 or no
    quest row exists yet for the day — callers create the row first via
    get_or_create_quest.
    """
    uid, day = str(user_id), int(day)
    if amount > 0:
        await _conn().execute(
            "UPDATE fishing_quests SET progress = MIN(target, progress + ?) "
            "WHERE user_id=? AND day=?",
            (int(amount), uid, day),
        )
        await _commit()
    async with _conn().execute(
        "SELECT progress FROM fishing_quests WHERE user_id=? AND day=?",
        (uid, day),
    ) as cur:
        row = await cur.fetchone()
    return row["progress"] if row else 0


async def try_claim_quest_reward(user_id: int, day: int) -> bool:
    """Atomically mark today's completed quest claimed (first claimer wins).

    Returns False if the quest isn't at target yet, or was already claimed.
    """
    cur = await _conn().execute(
        "UPDATE fishing_quests SET claimed=1 "
        "WHERE user_id=? AND day=? AND claimed=0 AND progress>=target",
        (str(user_id), int(day)),
    )
    await _commit()
    return cur.rowcount > 0


# ══════════════════════════════════════════════════════════════════════════════
#  Stat registry for the /fish global board — one entry per rankable stat.
#  `column` is a whitelisted column name (never built from caller input). Since
#  fishing_stats is now one row per user, ranking is a plain ORDER BY: the old
#  GROUP BY user_id aggregation existed only to stitch a member's per-guild rows
#  back together, and merging them is exactly what migration 1 did once.
# ══════════════════════════════════════════════════════════════════════════════
GLOBAL_STATS: dict[str, dict] = {
    "earned": {"label": "Lifetime Earnings", "column": "earned", "order": "DESC"},
    "caught": {"label": "Fish Caught", "column": "caught", "order": "DESC"},
    "casts": {"label": "Casts", "column": "casts", "order": "DESC"},
    "best_weight": {
        "label": "Heaviest Catch",
        "column": "best_weight",
        "order": "DESC",
    },
    "xp": {"label": "Fishing XP", "column": "xp", "order": "DESC"},
    "streak": {"label": "Best Streak", "column": "streak_days", "order": "DESC"},
}


async def get_global_leaderboard(
    stat_key: str, limit: int = 10, offset: int = 0
) -> list[dict]:
    """Top members by `stat_key`, aggregated across every guild. [{user_id, value}]."""
    stat = GLOBAL_STATS.get(stat_key)
    if stat is None:
        raise ValueError(f"unknown global fishing stat {stat_key!r}")
    async with _read_conn().execute(
        f"SELECT user_id, {stat['column']} AS val FROM fishing_stats "
        f"WHERE {stat['column']} > 0 "
        f"ORDER BY val {stat['order']}, user_id ASC LIMIT ? OFFSET ?",
        (int(limit), int(offset)),
    ) as cur:
        rows = await cur.fetchall()
    return [{"user_id": int(r["user_id"]), "value": r["val"]} for r in rows]


async def count_global_leaderboard(stat_key: str) -> int:
    stat = GLOBAL_STATS.get(stat_key)
    if stat is None:
        raise ValueError(f"unknown global fishing stat {stat_key!r}")
    async with _read_conn().execute(
        f"SELECT COUNT(*) FROM fishing_stats WHERE {stat['column']} > 0"
    ) as cur:
        return (await cur.fetchone())[0]


async def get_global_rank(stat_key: str, user_id: int) -> tuple[int, float] | None:
    """(rank, value) for a member across every guild by `stat_key`; None if 0/none."""
    stat = GLOBAL_STATS.get(stat_key)
    if stat is None:
        raise ValueError(f"unknown global fishing stat {stat_key!r}")
    async with _read_conn().execute(
        f"SELECT {stat['column']} AS val FROM fishing_stats WHERE user_id=?",
        (str(user_id),),
    ) as cur:
        row = await cur.fetchone()
    val = row["val"] if row and row["val"] is not None else 0
    if val <= 0:
        return None
    ahead = await count_ahead(
        f"SELECT COUNT(*) FROM fishing_stats WHERE {stat['column']} > ?", val
    )
    return ahead + 1, val


register_init(_ensure_fishing_tables)
