"""utils/db.activities — economy activities storage (/work, /mine, /hunt,
/explore, /rob).

Part of the utils/db package. Coins/items themselves live in the economy and
items tables (utils/db/economy.py, utils/db/items.py) — this module only
tracks per-guild config (the enable flags) and GLOBAL per-user
cooldown/lifetime stats (last-run timestamps, per-activity counts, the /work
career shift tally, and the /mine pickaxe tier).

The stats row is keyed by user_id alone: a pickaxe bought in one server digs in
every server, and a cooldown claimed anywhere applies everywhere (which is also
what stops the same member farming /work once per server). Whether an activity
runs at all is a per-guild setting; how LONG its cooldown lasts is not — that
claim is global, so the length is a bot-wide, owner-only setting kept in
utils/db/settings.py. Migration 4 dropped the old per-guild columns.
"""

from . import _cache
from ._core import _commit, _conn, _ensure_columns, register_init

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
    # /squad and /raid aren't /adventure activities, but their payouts are the
    # same shape — a per-user claim on a global wallet — and this is the one
    # atomic claim in the codebase, so they use it rather than growing a
    # second. They deliberately stay out of cogs.activities.ACTIVITY_NAMES, so
    # /adventure never lists or toggles them.
    "coop": ("last_coop", "coop_count"),
    "raid": ("last_raid", "raid_count"),
}


async def _ensure_activities_tables():
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS activities_config (
            guild_id          TEXT PRIMARY KEY,
            work_enabled      INTEGER NOT NULL DEFAULT 1,
            mine_enabled      INTEGER NOT NULL DEFAULT 1,
            hunt_enabled      INTEGER NOT NULL DEFAULT 1,
            explore_enabled   INTEGER NOT NULL DEFAULT 1,
            rob_enabled       INTEGER NOT NULL DEFAULT 1
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
    # Co-op claim columns — added after the baseline table (see the note on
    # _ACTIVITY_COLUMNS) — plus the adventure daily streak, which is one pair
    # for the whole loop rather than one per activity: the streak asks "did you
    # adventure today", and which of the five you ran is not the question.
    await _ensure_columns(
        "activities_stats",
        {
            "last_coop": "REAL NOT NULL DEFAULT 0",
            "coop_count": "INTEGER NOT NULL DEFAULT 0",
            "last_raid": "REAL NOT NULL DEFAULT 0",
            "raid_count": "INTEGER NOT NULL DEFAULT 0",
            "streak_days": "INTEGER NOT NULL DEFAULT 0",
            "last_day": "INTEGER NOT NULL DEFAULT 0",
        },
    )


# ── Config ─────────────────────────────────────────────────────────────────────
_CONFIG_DEFAULTS = {
    "work_enabled": True,
    "mine_enabled": True,
    "hunt_enabled": True,
    "explore_enabled": True,
    "rob_enabled": True,
}


async def get_activities_config(guild_id: int) -> dict:
    cached = _cache.get("activities_config", guild_id)
    if cached is not None:
        return cached
    async with _conn().execute(
        "SELECT work_enabled, mine_enabled, hunt_enabled, explore_enabled, "
        "rob_enabled FROM activities_config WHERE guild_id=?",
        (str(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return _cache.put("activities_config", guild_id, dict(_CONFIG_DEFAULTS))
    return _cache.put(
        "activities_config",
        guild_id,
        {
            "work_enabled": bool(row["work_enabled"]),
            "mine_enabled": bool(row["mine_enabled"]),
            "hunt_enabled": bool(row["hunt_enabled"]),
            "explore_enabled": bool(row["explore_enabled"]),
            "rob_enabled": bool(row["rob_enabled"]),
        },
    )


async def set_activities_config(guild_id: int, **kwargs) -> None:
    """Partial update — unspecified keys keep their current (or default) value."""
    current = await get_activities_config(guild_id)
    current.update(kwargs)
    await _conn().execute(
        "INSERT INTO activities_config (guild_id, work_enabled, mine_enabled, "
        "hunt_enabled, explore_enabled, rob_enabled) "
        "VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(guild_id) DO UPDATE SET "
        "work_enabled=excluded.work_enabled, mine_enabled=excluded.mine_enabled, "
        "hunt_enabled=excluded.hunt_enabled, explore_enabled=excluded.explore_enabled, "
        "rob_enabled=excluded.rob_enabled",
        (
            str(guild_id),
            1 if current["work_enabled"] else 0,
            1 if current["mine_enabled"] else 0,
            1 if current["hunt_enabled"] else 0,
            1 if current["explore_enabled"] else 0,
            1 if current["rob_enabled"] else 0,
        ),
    )
    await _commit()
    _cache.invalidate("activities_config", guild_id)


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
        "last_coop": row["last_coop"],
        "coop_count": row["coop_count"],
        "last_raid": row["last_raid"],
        "raid_count": row["raid_count"],
        "streak_days": row["streak_days"],
        "last_day": row["last_day"],
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
    "last_coop": 0.0,
    "coop_count": 0,
    "last_raid": 0.0,
    "raid_count": 0,
    "streak_days": 0,
    "last_day": 0,
}


async def get_activity_stats(user_id: int) -> dict:
    async with _conn().execute(
        "SELECT last_work, work_shifts, last_mine, mine_count, pickaxe_level, "
        "last_hunt, hunt_count, last_explore, explore_count, last_rob, rob_count, "
        "last_coop, coop_count, last_raid, raid_count, streak_days, last_day "
        "FROM activities_stats WHERE user_id=?",
        (str(user_id),),
    ) as cur:
        row = await cur.fetchone()
    if row:
        return _stats_row(row)
    return dict(_STATS_DEFAULTS)


async def try_claim_activity(
    user_id: int,
    activity: str,
    now: float,
    cooldown: int,
    max_charges: int = 1,
) -> int:
    """Atomically spend one charge of an activity. Returns 0 on success, else
    seconds until the next charge.

    Mirrors utils.db.fishing.try_claim_cast: a single conditional upsert, so
    two concurrent invocations of the same activity can't both pass the
    cooldown check. `activity` is checked against a fixed whitelist — the
    column names it maps to are the only thing ever substituted into SQL.

    Charges
    ───────
    `last_col` is a *token bucket*, not a "when you last ran it" stamp: it is
    the instant the bucket was last drained, and it is allowed to sit up to
    `max_charges` cooldowns behind the clock. Charges available at `now` are
    `floor((now - last) / cooldown)`, clamped to `max_charges` by lifting the
    floor to `now - max_charges*cooldown`; spending one advances `last` by one
    cooldown instead of jumping it to `now`.

    That is the whole engagement fix in one column. An activity on a 20-minute
    interval used to mean "log in every 20 minutes or lose it", which is the
    thing nobody with a phone and a life actually does; banking three of them
    means an hour away comes back as three taps in a row. The long-run income
    rate is exactly the same either way — only the shape of the visit changes.

    At `max_charges=1` this is arithmetically identical to the plain
    "last = now" claim it replaces (the floor is then `now - cooldown`, so a
    ready activity always lands `last` on `now`), which is why /rob, /squad and
    /raid keep their old behaviour by simply not passing the argument.
    """
    if activity not in _ACTIVITY_COLUMNS:
        raise ValueError(f"unknown activity {activity!r}")
    last_col, count_col = _ACTIVITY_COLUMNS[activity]
    charges = max(1, int(max_charges))
    cooldown = int(cooldown)
    # The oldest `last` still worth honouring: anything older banked its cap
    # long ago. Also what a fresh claim starts from, so a first run leaves the
    # rest of the bucket full rather than empty.
    floor = float(now) - charges * cooldown
    fresh = float(now) - (charges - 1) * cooldown
    # The row is shared across all five activities (one per user), so a
    # fresh claim for THIS activity can hit the UPDATE branch even though its
    # own last_col is still the untouched 0 default (some other activity
    # created the row first) — guard that case explicitly, or a 0 baseline
    # would wrongly be treated as a real recent timestamp (and, with a floor
    # that can be negative under the small synthetic clocks tests use, would
    # otherwise be lifted to a value in the *future*).
    cur = await _conn().execute(
        f"INSERT INTO activities_stats (user_id, {last_col}, {count_col}) "
        f"VALUES (?,?,1) "
        f"ON CONFLICT(user_id) DO UPDATE SET "
        f"{last_col}=CASE WHEN activities_stats.{last_col} = 0 THEN ? "
        f"ELSE MAX(activities_stats.{last_col}, ?) + ? END, "
        f"{count_col}={count_col}+1 "
        f"WHERE activities_stats.{last_col} = 0 "
        f"OR ? - MAX(activities_stats.{last_col}, ?) >= ?",
        (
            str(user_id),
            fresh,
            fresh,
            floor,
            float(cooldown),
            float(now),
            floor,
            float(cooldown),
        ),
    )
    await _commit()
    if cur.rowcount > 0:
        return 0
    stats = await get_activity_stats(user_id)
    # Blocked means `last` is within one cooldown of now, so it is already
    # above the floor — the wait is measured from the stored value directly.
    return max(1, int(cooldown - (now - stats[last_col])))


async def try_claim_adventure_streak(user_id: int, day: int, streak: int) -> bool:
    """Stamp the first-activity-of-the-day streak update.

    One conditional UPDATE, so two activities run in the same second can't both
    decide they were the first of the day and pay the bonus twice — the same
    shape as utils.db.fishing.try_claim_daily_streak. `day` is an epoch day
    number; returns True only when this call is the one that moved the stamp.

    There is no INSERT branch: every caller has just claimed an activity, which
    created the row.
    """
    cur = await _conn().execute(
        "UPDATE activities_stats SET last_day=?, streak_days=? "
        "WHERE user_id=? AND last_day<>?",
        (int(day), int(streak), str(user_id), int(day)),
    )
    await _commit()
    return cur.rowcount > 0


async def all_pickaxe_owners() -> list[tuple[int, int]]:
    """Every miner who has bought at least one pickaxe, as (user_id, level).

    Only the price-refund sweep needs this — a repricing has to reach everyone
    who already paid, and nothing records what they paid, so the tier they own
    is the receipt (see utils/db/refunds.py).
    """
    async with _conn().execute(
        "SELECT user_id, pickaxe_level FROM activities_stats WHERE pickaxe_level > 0"
    ) as cur:
        rows = await cur.fetchall()
    return [(int(r["user_id"]), int(r["pickaxe_level"])) for r in rows]


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
    await _commit()
    return cur.rowcount > 0


register_init(_ensure_activities_tables)
