"""utils/db.progression — achievements, weekly objectives, and prestige storage.

Part of the utils/db package. Three tables, all owned by cogs/progression/:

All three tables are GLOBAL (keyed by user, not guild): an achievement earned
anywhere is earned everywhere, a weekly objective tracks the user's own
lifetime stats wherever they play, and prestige/titles are account-wide.

- ``achievements_earned`` — one row per (user, achievement key) the
  member has crossed the threshold for. Awarding is a plain INSERT OR IGNORE,
  so re-evaluating the same achievement twice is a no-op at the DB layer; the
  *caller* checks the insert's rowcount (try_award_achievement's return value)
  before granting the reward, so a re-evaluation can never double-pay
  (mirrors fishing's try_claim_quest_reward guard).
- ``weekly_objectives`` — up to three deterministically-picked objectives per
  (user, period). ``week`` is really just a period key (an ISO week
  like ``"2026-W30"`` today) — a future "season" mode is just a longer-lived
  period key (e.g. ``"2026-summer"``) slotted into the same column, no schema
  change needed. ``baseline`` snapshots the lifetime stat value the moment the
  period's rows are first created, so ``progress = current_stat - baseline``
  turns an always-increasing lifetime counter into a period-scoped one with no
  cross-cog hooks (no other feature is touched or listened to). ``claimed`` is
  a conditional UPDATE that re-checks completion itself (against a caller-
  supplied current stat reading) inside the same statement, so a double
  /progress weekly can't double-pay even if two calls race on the same stale
  read.
- ``progression`` — one row per user: prestige rank, plus the
  member's chosen cosmetic title (nullable; the cog defaults it to the
  highest-points earned title when unset). Advancing prestige is a
  conditional UPDATE guarded on the expected prior rank (mirrors
  utils.db.fishing.set_rod_level / utils.db.activities.set_pickaxe_level), so
  a double /progress prestige confirm can't double-charge — the caller
  refunds the coin debit when the CAS loses.
"""

import time

from ._core import _commit, _conn, register_init


async def _ensure_progression_tables():
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS achievements_earned (
            user_id   TEXT NOT NULL,
            key       TEXT NOT NULL,
            earned_at REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, key)
        )
    """)
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS weekly_objectives (
            user_id   TEXT NOT NULL,
            week      TEXT NOT NULL,
            obj_key   TEXT NOT NULL,
            target    REAL NOT NULL DEFAULT 0,
            baseline  REAL NOT NULL DEFAULT 0,
            claimed   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, week, obj_key)
        )
    """)
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS progression (
            user_id        TEXT PRIMARY KEY,
            prestige       INTEGER NOT NULL DEFAULT 0,
            selected_title TEXT NOT NULL DEFAULT ''
        )
    """)
    await _commit()


# ── Achievements ───────────────────────────────────────────────────────────────
async def get_earned_achievements(user_id: int) -> dict[str, float]:
    """Earned achievement keys → their earned_at epoch, for this user."""
    async with _conn().execute(
        "SELECT key, earned_at FROM achievements_earned WHERE user_id=?",
        (str(user_id),),
    ) as cur:
        rows = await cur.fetchall()
    return {r["key"]: r["earned_at"] for r in rows}


async def try_award_achievement(
    user_id: int, key: str, *, earned_at: float | None = None
) -> bool:
    """Atomically record an achievement as earned.

    Returns True the first (and only the first) time for a given key — a
    re-evaluation that finds the threshold already crossed sees rowcount 0 and
    the caller must not grant the reward again.
    """
    cur = await _conn().execute(
        "INSERT OR IGNORE INTO achievements_earned (user_id, key, earned_at) "
        "VALUES (?,?,?)",
        (
            str(user_id),
            key,
            float(earned_at if earned_at is not None else time.time()),
        ),
    )
    await _commit()
    return cur.rowcount > 0


# ── Weekly (period) objectives ──────────────────────────────────────────────────
async def get_period_objectives(user_id: int, period: str) -> list[dict]:
    async with _conn().execute(
        "SELECT obj_key, target, baseline, claimed FROM weekly_objectives "
        "WHERE user_id=? AND week=? ORDER BY obj_key ASC",
        (str(user_id), period),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {
            "obj_key": r["obj_key"],
            "target": r["target"],
            "baseline": r["baseline"],
            "claimed": bool(r["claimed"]),
        }
        for r in rows
    ]


async def get_or_create_objective(
    user_id: int,
    period: str,
    obj_key: str,
    target: float,
    baseline: float,
) -> dict:
    """Fetch a period's objective row, creating it (with `baseline` snapshotted
    at *this* moment) on first read. Idempotent — a later call for the same
    (user, period, obj_key) with a different target/baseline is a
    no-op; the row this period already exists."""
    uid = str(user_id)
    await _conn().execute(
        "INSERT OR IGNORE INTO weekly_objectives "
        "(user_id, week, obj_key, target, baseline, claimed) VALUES (?,?,?,?,?,0)",
        (uid, period, obj_key, float(target), float(baseline)),
    )
    await _commit()
    async with _conn().execute(
        "SELECT obj_key, target, baseline, claimed FROM weekly_objectives "
        "WHERE user_id=? AND week=? AND obj_key=?",
        (uid, period, obj_key),
    ) as cur:
        row = await cur.fetchone()
    return {
        "obj_key": row["obj_key"],
        "target": row["target"],
        "baseline": row["baseline"],
        "claimed": bool(row["claimed"]),
    }


async def try_claim_objective(
    user_id: int, period: str, obj_key: str, current_value: float
) -> bool:
    """Atomically claim a completed objective's reward (first claimer wins).

    The completion check itself (`current_value - baseline >= target`) lives
    in the UPDATE's WHERE clause, not a separate read beforehand, so a stale
    `current_value` reading can't claim a not-actually-complete objective, and
    two racing calls can't both claim the same one.
    """
    cur = await _conn().execute(
        "UPDATE weekly_objectives SET claimed=1 "
        "WHERE user_id=? AND week=? AND obj_key=? AND claimed=0 "
        "AND (? - baseline) >= target",
        (str(user_id), period, obj_key, float(current_value)),
    )
    await _commit()
    return cur.rowcount > 0


# ── Prestige / profile ──────────────────────────────────────────────────────────
async def get_progression(user_id: int) -> dict:
    async with _conn().execute(
        "SELECT prestige, selected_title FROM progression WHERE user_id=?",
        (str(user_id),),
    ) as cur:
        row = await cur.fetchone()
    if row:
        return {"prestige": row["prestige"], "selected_title": row["selected_title"]}
    return {"prestige": 0, "selected_title": ""}


async def try_advance_prestige(user_id: int, new_rank: int, *, expected: int) -> bool:
    """Conditionally advance the prestige rank (first confirm wins a race).

    Returns False when the stored rank no longer matches `expected` — the
    caller should refund the coins it already debited. Mirrors
    utils.db.fishing.set_rod_level / utils.db.activities.set_pickaxe_level.
    """
    if expected == 0:
        # A member with no progression row yet is implicitly at rank 0.
        await _conn().execute(
            "INSERT OR IGNORE INTO progression (user_id) VALUES (?)",
            (str(user_id),),
        )
    cur = await _conn().execute(
        "UPDATE progression SET prestige=? WHERE user_id=? AND prestige=?",
        (int(new_rank), str(user_id), int(expected)),
    )
    await _commit()
    return cur.rowcount > 0


async def set_selected_title(user_id: int, title: str) -> None:
    """Set (or, with an empty string, clear) the user's displayed title."""
    await _conn().execute(
        "INSERT INTO progression (user_id, selected_title) VALUES (?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET selected_title=excluded.selected_title",
        (str(user_id), title),
    )
    await _commit()


register_init(_ensure_progression_tables)
