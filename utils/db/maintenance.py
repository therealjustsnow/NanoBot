"""utils/db.maintenance — retention sweep, WAL checkpoint, and VACUUM.

Part of the utils/db package. No tables of its own: this module is the janitor
for everyone else's.

Three jobs, all driven by the daily maintenance loop in main.py:

1. **Retention.** Three tables grow with the calendar rather than with play —
   a member who fishes every day for a year leaves 365 `fishing_quests` rows
   behind, and only today's is ever read. Same for `casino_challenges` (two a
   day) and `weekly_objectives`. Old rows are dead weight: the day/period is
   past, any reward was claimed at the time, and nothing reads them again.
2. **WAL checkpoint.** WAL is never checkpointed at runtime by SQLite alone
   when a connection stays open forever, which is exactly this bot's shape —
   one connection, opened at startup, never closed. Left alone the `-wal` file
   grows without bound on a write-heavy long-uptime bot.
3. **VACUUM.** Deletes (a sold bag, an expired event, this module's own prune)
   free pages inside the file but never return them to the OS. VACUUM rewrites
   the database to reclaim them — expensive, so it only runs when there is
   enough free space to be worth it.

Deliberately NOT pruned:

* ``shop_purchases`` — it *is* the per-user-limit and cooldown ledger.
  Deleting an old row would silently let someone re-buy a limit-1 item.
* ``fishing_catches`` — that's a member's bag; it's theirs until they sell it.
* ``achievements_earned`` / ``cosmetic_unlocks`` — permanent by design, and
  bounded by the catalogue size anyway.
"""

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from ._core import _conn

log = logging.getLogger("NanoBot.db")


class Policy(NamedTuple):
    """One retention rule: keep `keep_days` of `table`, judged by `column`.

    `kind` says how to turn a cutoff date into a value comparable with the
    column: ``epoch_day`` for the integer day counters (``time.time() // 86400``)
    and ``iso_week`` for the ``YYYY-Www`` period keys, which sort lexically.
    """

    table: str
    column: str
    kind: str
    keep_days: int


RETENTION: tuple[Policy, ...] = (
    # A fortnight is far more than the one day anything reads, and leaves room
    # to look at yesterday when debugging a reward complaint.
    Policy("fishing_quests", "day", "epoch_day", 14),
    Policy("casino_challenges", "day", "epoch_day", 14),
    # Eight weeks of objective history — the current period plus a season's
    # worth of context.
    Policy("weekly_objectives", "week", "iso_week", 56),
)

# VACUUM is only worth its cost when there's a real amount to reclaim: at least
# this many free pages, and at least this fraction of the file.
_VACUUM_MIN_FREE_PAGES = 2_000
_VACUUM_MIN_FREE_RATIO = 0.15


def _cutoff(policy: Policy, now: float) -> int | str:
    """The oldest value of `policy.column` worth keeping."""
    moment = datetime.fromtimestamp(now, tz=timezone.utc) - timedelta(
        days=policy.keep_days
    )
    if policy.kind == "epoch_day":
        # keep_days *inclusive* of today: with keep_days=14 the oldest day kept
        # is today-13, so today-14 and older go.
        return int(moment.timestamp() // 86400) + 1
    if policy.kind == "iso_week":
        iso_year, iso_week, _weekday = moment.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    raise ValueError(f"unknown retention kind: {policy.kind}")


async def prune_history(now: float | None = None) -> dict[str, int]:
    """Apply every retention policy. Returns {table: rows removed}.

    One statement and one commit per policy; cheap enough to run daily even on
    a large database, since each column is the leading part of its table's
    primary key or a plain integer scan over rows that are about to go away.
    """
    now = time.time() if now is None else now
    removed: dict[str, int] = {}
    for policy in RETENTION:
        cur = await _conn().execute(
            f"DELETE FROM {policy.table} WHERE {policy.column} < ?",
            (_cutoff(policy, now),),
        )
        await _conn().commit()
        if cur.rowcount > 0:
            removed[policy.table] = cur.rowcount
    return removed


async def checkpoint_wal() -> int:
    """Fold the WAL back into the main database. Returns pages checkpointed.

    TRUNCATE mode also resets the `-wal` file to zero length, which is the
    whole point — a connection that never closes never truncates it otherwise.
    """
    async with _conn().execute("PRAGMA wal_checkpoint(TRUNCATE)") as cur:
        row = await cur.fetchone()
    if not row:
        return 0
    # (busy, wal_pages, checkpointed_pages). SQLite reports -1 when the database
    # isn't in WAL mode at all (an in-memory test DB, say) or the checkpoint was
    # blocked; either way nothing was folded back.
    return max(0, int(row[2]))


async def free_space() -> tuple[int, int]:
    """(free pages, total pages) in the database file."""
    async with _conn().execute("PRAGMA freelist_count") as cur:
        free = (await cur.fetchone())[0]
    async with _conn().execute("PRAGMA page_count") as cur:
        total = (await cur.fetchone())[0]
    return int(free), int(total)


async def vacuum() -> None:
    """Rewrite the database, returning free pages to the filesystem.

    VACUUM cannot run inside a transaction, and this package leaves implicit
    ones open (every accessor commits, but a caller mid-flight may not have
    yet), so commit first.
    """
    await _conn().commit()
    await _conn().execute("VACUUM")


async def sweep(now: float | None = None, *, allow_vacuum: bool = True) -> dict:
    """The daily maintenance pass: prune, checkpoint, and VACUUM if it's worth it.

    Returns a summary dict for logging. Never raises for a single failing step —
    housekeeping must not take the bot down — but each failure is logged.
    """
    summary: dict = {"pruned": {}, "checkpointed": 0, "vacuumed": False}
    try:
        summary["pruned"] = await prune_history(now)
    except Exception:
        log.exception("Retention sweep failed")
    try:
        summary["checkpointed"] = await checkpoint_wal()
    except Exception:
        log.exception("WAL checkpoint failed")
    if allow_vacuum:
        try:
            free, total = await free_space()
            summary["free_pages"], summary["total_pages"] = free, total
            if (
                free >= _VACUUM_MIN_FREE_PAGES
                and free >= total * _VACUUM_MIN_FREE_RATIO
            ):
                await vacuum()
                summary["vacuumed"] = True
        except Exception:
            log.exception("VACUUM failed")
    return summary
