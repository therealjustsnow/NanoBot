"""utils/db.votes — bot-list vote.

Part of the utils/db package (split from the former single-file utils/db.py).
Shared connection state + schema machinery live in utils/db/_core.py; this
module holds the bot-list vote accessors and registers its table setup with _core so
db.init() creates them in the right order.
"""

import time


from ._core import _conn, register_init

# ══════════════════════════════════════════════════════════════════════════════
#  Votes
# ══════════════════════════════════════════════════════════════════════════════


async def _ensure_votes_table():
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS votes (
            user_id    TEXT NOT NULL,
            site       TEXT NOT NULL,   -- "topgg" | "dbl"
            voted_at   REAL NOT NULL,   -- unix timestamp
            streak     INTEGER NOT NULL DEFAULT 1,
            notify     INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (user_id, site)
        )
    """)
    await _conn().commit()


async def record_vote(user_id: int, site: str) -> dict:
    """
    Record a vote from a bot list site. Increments streak if the previous vote
    was within the cooldown window + 2h grace, otherwise resets to 1.

    Returns the updated vote record dict.
    """
    uid = str(user_id)
    now = time.time()

    # Cooldowns: 12h. Grace period = 6h extra.
    cooldown = (12 + 6) * 3600

    async with _conn().execute(
        "SELECT voted_at, streak, notify FROM votes WHERE user_id=? AND site=?",
        (uid, site),
    ) as cur:
        row = await cur.fetchone()

    if row:
        elapsed = now - row["voted_at"]
        streak = (row["streak"] + 1) if elapsed <= cooldown else 1
        notify = bool(row["notify"])
    else:
        streak = 1
        notify = True

    await _conn().execute(
        """INSERT INTO votes (user_id, site, voted_at, streak, notify)
           VALUES (?,?,?,?,?)
           ON CONFLICT(user_id, site) DO UPDATE SET
               voted_at=excluded.voted_at,
               streak=excluded.streak""",
        (uid, site, now, streak, 1 if notify else 0),
    )
    await _conn().commit()

    return {
        "user_id": uid,
        "site": site,
        "voted_at": now,
        "streak": streak,
        "notify": notify,
    }


async def get_vote(user_id: int, site: str) -> dict | None:
    async with _conn().execute(
        "SELECT user_id, site, voted_at, streak, notify FROM votes WHERE user_id=? AND site=?",
        (str(user_id), site),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    return {
        "user_id": row["user_id"],
        "site": row["site"],
        "voted_at": row["voted_at"],
        "streak": row["streak"],
        "notify": bool(row["notify"]),
    }


async def set_vote_notify(user_id: int, site: str, notify: bool) -> None:
    await _conn().execute(
        """INSERT INTO votes (user_id, site, voted_at, streak, notify)
           VALUES (?,?,0,0,?)
           ON CONFLICT(user_id, site) DO UPDATE SET notify=excluded.notify""",
        (str(user_id), site, 1 if notify else 0),
    )
    await _conn().commit()


async def get_all_votes_for_notify() -> list[dict]:
    """Return all vote records where notify is enabled — used by the cooldown DM loop."""
    async with _conn().execute(
        "SELECT user_id, site, voted_at, streak, notify FROM votes WHERE notify=1"
    ) as cur:
        rows = await cur.fetchall()
    return [
        {
            "user_id": r["user_id"],
            "site": r["site"],
            "voted_at": r["voted_at"],
            "streak": r["streak"],
        }
        for r in rows
    ]


async def has_voted_recently(user_id: int, site: str) -> bool:
    """True if the user has an active vote (within the site's cooldown window and our grace period)."""
    row = await get_vote(user_id, site)
    if not row:
        return False
    cooldown = (12 + 6) * 3600
    return (time.time() - row["voted_at"]) < cooldown


register_init(_ensure_votes_table)
