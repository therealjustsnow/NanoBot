"""utils/db.activities — economy activities storage (/work, /mine, /hunt,
/explore, /rob).

Part of the utils/db package. Coins/items themselves live in the economy and
items tables (utils/db/economy.py, utils/db/items.py) — this module only
tracks per-guild config (enable flags + cooldown overrides) and GLOBAL
per-user cooldown/lifetime stats (last-run timestamps, per-activity counts, the
/work career shift tally, and the /mine pickaxe tier).

The stats row is keyed by user_id alone: a pickaxe bought in one server digs in
every server, and a cooldown claimed anywhere applies everywhere (which is also
what stops the same member farming /work once per server). How long that
cooldown lasts, and whether an activity runs at all, stay per-guild settings.
"""

from ._core import _conn, register_init

# Whitelisted activity → (last-run column, lifetime-count column). Never build
# these from user input — activity names are always validated against this
# dict first, so the column names substituted into SQL below are always one of
# these fixed, code-controlled strings.
_ACTIVITY_COLUMNS: dict[str, tuple[str, str]] = {
    "work": ("last_work", "work_shifts"),
    "mine": ("last_mine", "mine_count"),
    "hunt": ("last_hunt", "hunt_count"),
    "explore": ("last_explore", "explore_count"),
    "rob": ("last_rob", "rob_count"),
}


async def _ensure_activities_tables():
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS activities_config (
            guild_id          TEXT PRIMARY KEY,
            work_enabled      INTEGER NOT NULL DEFAULT 1,
            mine_enabled      INTEGER NOT NULL DEFAULT 1,
            hunt_enabled      INTEGER NOT NULL DEFAULT 1,
            explore_enabled   INTEGER NOT NULL DEFAULT 1,
            rob_enabled       INTEGER NOT NULL DEFAULT 1,
            work_cooldown     INTEGER NOT NULL DEFAULT 3600,
            mine_cooldown     INTEGER NOT NULL DEFAULT 1800,
            hunt_cooldown     INTEGER NOT NULL DEFAULT 2700,
            explore_cooldown  INTEGER NOT NULL DEFAULT 10800,
            rob_cooldown      INTEGER NOT NULL DEFAULT 14400
        )
    """)
    # Per-user cooldown claims + lifetime stats. `work_shifts` doubles as the
    # /work career-ladder tally (see cogs.activities.helpers.career_info);
    # `pickaxe_level` is /mine-specific, the rest are generic per-activity
    # last-run/count pairs (see _ACTIVITY_COLUMNS).
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS activities_stats (
            user_id         TEXT PRIMARY KEY,
            last_work       REAL NOT NULL DEFAULT 0,
            work_shifts     INTEGER NOT NULL DEFAULT 0,
            last_mine       REAL NOT NULL DEFAULT 0,
            mine_count      INTEGER NOT NULL DEFAULT 0,
            pickaxe_level   INTEGER NOT NULL DEFAULT 0,
            last_hunt       REAL NOT NULL DEFAULT 0,
            hunt_count      INTEGER NOT NULL DEFAULT 0,
            last_explore    REAL NOT NULL DEFAULT 0,
            explore_count   INTEGER NOT NULL DEFAULT 0,
            last_rob        REAL NOT NULL DEFAULT 0,
            rob_count       INTEGER NOT NULL DEFAULT 0
        )
    """)
    await _conn().commit()


# ── Config ─────────────────────────────────────────────────────────────────────
_CONFIG_DEFAULTS = {
    "work_enabled": True,
    "mine_enabled": True,
    "hunt_enabled": True,
    "explore_enabled": True,
    "rob_enabled": True,
    "work_cooldown": 3600,
    "mine_cooldown": 1800,
    "hunt_cooldown": 2700,
    "explore_cooldown": 10800,
    "rob_cooldown": 14400,
}


async def get_activities_config(guild_id: int) -> dict:
    async with _conn().execute(
        "SELECT work_enabled, mine_enabled, hunt_enabled, explore_enabled, "
        "rob_enabled, work_cooldown, mine_cooldown, hunt_cooldown, "
        "explore_cooldown, rob_cooldown FROM activities_config WHERE guild_id=?",
        (str(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return dict(_CONFIG_DEFAULTS)
    return {
        "work_enabled": bool(row["work_enabled"]),
        "mine_enabled": bool(row["mine_enabled"]),
        "hunt_enabled": bool(row["hunt_enabled"]),
        "explore_enabled": bool(row["explore_enabled"]),
        "rob_enabled": bool(row["rob_enabled"]),
        "work_cooldown": row["work_cooldown"],
        "mine_cooldown": row["mine_cooldown"],
        "hunt_cooldown": row["hunt_cooldown"],
        "explore_cooldown": row["explore_cooldown"],
        "rob_cooldown": row["rob_cooldown"],
    }


async def set_activities_config(guild_id: int, **kwargs) -> None:
    """Partial update — unspecified keys keep their current (or default) value."""
    current = await get_activities_config(guild_id)
    current.update(kwargs)
    await _conn().execute(
        "INSERT INTO activities_config (guild_id, work_enabled, mine_enabled, "
        "hunt_enabled, explore_enabled, rob_enabled, work_cooldown, "
        "mine_cooldown, hunt_cooldown, explore_cooldown, rob_cooldown) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(guild_id) DO UPDATE SET "
        "work_enabled=excluded.work_enabled, mine_enabled=excluded.mine_enabled, "
        "hunt_enabled=excluded.hunt_enabled, explore_enabled=excluded.explore_enabled, "
        "rob_enabled=excluded.rob_enabled, work_cooldown=excluded.work_cooldown, "
        "mine_cooldown=excluded.mine_cooldown, hunt_cooldown=excluded.hunt_cooldown, "
        "explore_cooldown=excluded.explore_cooldown, rob_cooldown=excluded.rob_cooldown",
        (
            str(guild_id),
            1 if current["work_enabled"] else 0,
            1 if current["mine_enabled"] else 0,
            1 if current["hunt_enabled"] else 0,
            1 if current["explore_enabled"] else 0,
            1 if current["rob_enabled"] else 0,
            int(current["work_cooldown"]),
            int(current["mine_cooldown"]),
            int(current["hunt_cooldown"]),
            int(current["explore_cooldown"]),
            int(current["rob_cooldown"]),
        ),
    )
    await _conn().commit()


# ── Stats ──────────────────────────────────────────────────────────────────────
def _stats_row(row) -> dict:
    return {
        "last_work": row["last_work"],
        "work_shifts": row["work_shifts"],
        "last_mine": row["last_mine"],
        "mine_count": row["mine_count"],
        "pickaxe_level": row["pickaxe_level"],
        "last_hunt": row["last_hunt"],
        "hunt_count": row["hunt_count"],
        "last_explore": row["last_explore"],
        "explore_count": row["explore_count"],
        "last_rob": row["last_rob"],
        "rob_count": row["rob_count"],
    }


_STATS_DEFAULTS = {
    "last_work": 0.0,
    "work_shifts": 0,
    "last_mine": 0.0,
    "mine_count": 0,
    "pickaxe_level": 0,
    "last_hunt": 0.0,
    "hunt_count": 0,
    "last_explore": 0.0,
    "explore_count": 0,
    "last_rob": 0.0,
    "rob_count": 0,
}


async def get_activity_stats(user_id: int) -> dict:
    async with _conn().execute(
        "SELECT last_work, work_shifts, last_mine, mine_count, pickaxe_level, "
        "last_hunt, hunt_count, last_explore, explore_count, last_rob, rob_count "
        "FROM activities_stats WHERE user_id=?",
        (str(user_id),),
    ) as cur:
        row = await cur.fetchone()
    if row:
        return _stats_row(row)
    return dict(_STATS_DEFAULTS)


async def try_claim_activity(
    user_id: int, activity: str, now: float, cooldown: int
) -> int:
    """Atomically claim a cooldown slot for one activity. Returns 0 on
    success, else seconds left.

    Mirrors utils.db.fishing.try_claim_cast: a single conditional upsert, so
    two concurrent invocations of the same activity can't both pass the
    cooldown check. `activity` is checked against a fixed whitelist — the
    column names it maps to are the only thing ever substituted into SQL.
    """
    if activity not in _ACTIVITY_COLUMNS:
        raise ValueError(f"unknown activity {activity!r}")
    last_col, count_col = _ACTIVITY_COLUMNS[activity]
    # The row is shared across all five activities (one per user), so a
    # fresh claim for THIS activity can hit the UPDATE branch even though its
    # own last_col is still the untouched 0 default (some other activity
    # created the row first) — guard that case explicitly, or a 0 baseline
    # would wrongly be treated as a real recent timestamp.
    cur = await _conn().execute(
        f"INSERT INTO activities_stats (user_id, {last_col}, {count_col}) "
        f"VALUES (?,?,1) "
        f"ON CONFLICT(user_id) DO UPDATE SET "
        f"{last_col}=excluded.{last_col}, {count_col}={count_col}+1 "
        f"WHERE activities_stats.{last_col} = 0 "
        f"OR excluded.{last_col} - activities_stats.{last_col} >= ?",
        (str(user_id), float(now), int(cooldown)),
    )
    await _conn().commit()
    if cur.rowcount > 0:
        return 0
    stats = await get_activity_stats(user_id)
    return max(1, int(cooldown - (now - stats[last_col])))


async def set_pickaxe_level(user_id: int, new_level: int, *, expected: int) -> bool:
    """Conditionally advance the pickaxe tier (first upgrader wins a race).

    Returns False when the stored level no longer matches `expected` — the
    caller should refund the debited coins. Mirrors
    utils.db.fishing.set_rod_level.
    """
    if expected == 0:
        # A miner with no stats row yet is implicitly at level 0.
        await _conn().execute(
            "INSERT OR IGNORE INTO activities_stats (user_id) VALUES (?)",
            (str(user_id),),
        )
    cur = await _conn().execute(
        "UPDATE activities_stats SET pickaxe_level=? "
        "WHERE user_id=? AND pickaxe_level=?",
        (int(new_level), str(user_id), int(expected)),
    )
    await _conn().commit()
    return cur.rowcount > 0


register_init(_ensure_activities_tables)
