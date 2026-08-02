"""utils/db.polls — live Would-You-Rather poll boards.

Part of the utils/db package. One table, `wyr_polls`, holding the polls that
are still open: the two options, who has voted for which, and when voting ends.

It exists for the same reason `economy_raids` does. A `/fun wyr` poll runs for
up to 24 hours, which is far longer than this bot goes between restarts — and
the view behind it was in-memory, with a `discord.ui.View` timeout as the only
thing that ever announced a winner. A restart therefore dropped every vote cast
so far, left the buttons orphaned (Discord's "This interaction failed"), and
guaranteed the poll would never announce anything. A poll that silently stops
counting is worse than one that never started, since the people who voted have
no way to tell.

Scope: this is a *live board*, not a member's progress — the row is deleted the
moment results are announced — so it keeps `guild_id`/`channel_id` alongside the
per-user votes, exactly as the raid and squad boards do. Votes are stored as one
JSON object keyed by user id (`{"123": "A"}`) rather than a row per vote: the
whole tally is read and rewritten in one place by a single event loop, so a
blob costs one write per vote and can't interleave.
"""

import json
import time

from ._core import (
    _commit,
    _conn,
    register_init,
)


async def _ensure_poll_tables():
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS wyr_polls (
            poll_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id   TEXT,
            channel_id TEXT NOT NULL,
            message_id TEXT,
            option_a   TEXT NOT NULL,
            option_b   TEXT NOT NULL,
            votes      TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL DEFAULT 0,
            end_ts     REAL NOT NULL DEFAULT 0
        )
    """)
    await _commit()


register_init(_ensure_poll_tables)


async def create_wyr_poll(
    guild_id: int | None,
    channel_id: int,
    option_a: str,
    option_b: str,
    end_ts: float,
    created_at: float | None = None,
) -> int:
    """Insert an open poll and return its new id (used in the button custom_ids)."""
    async with _conn().execute(
        "INSERT INTO wyr_polls "
        "(guild_id, channel_id, option_a, option_b, votes, created_at, end_ts) "
        "VALUES (?, ?, ?, ?, '{}', ?, ?)",
        (
            str(guild_id) if guild_id is not None else None,
            str(channel_id),
            option_a,
            option_b,
            float(created_at if created_at is not None else time.time()),
            float(end_ts),
        ),
    ) as cur:
        poll_id = cur.lastrowid
    await _commit()
    return int(poll_id)


async def set_wyr_message(poll_id: int, message_id: int) -> None:
    await _conn().execute(
        "UPDATE wyr_polls SET message_id=? WHERE poll_id=?",
        (str(message_id), int(poll_id)),
    )
    await _commit()


async def set_wyr_votes(poll_id: int, votes: dict[int, str]) -> None:
    """Overwrite the tally. The in-memory view is the single writer, so the
    whole dict goes in one statement rather than a row per vote."""
    await _conn().execute(
        "UPDATE wyr_polls SET votes=? WHERE poll_id=?",
        (json.dumps({str(uid): side for uid, side in votes.items()}), int(poll_id)),
    )
    await _commit()


async def delete_wyr_poll(poll_id: int) -> None:
    await _conn().execute("DELETE FROM wyr_polls WHERE poll_id=?", (int(poll_id),))
    await _commit()


async def get_open_wyr_polls() -> list[dict]:
    """Every persisted poll (message_id may be NULL if a crash beat the update)."""
    async with _conn().execute(
        "SELECT poll_id, guild_id, channel_id, message_id, option_a, option_b, "
        "votes, created_at, end_ts FROM wyr_polls"
    ) as cur:
        rows = await cur.fetchall()
    out = []
    for r in rows:
        try:
            raw = json.loads(r["votes"] or "{}")
            votes = {int(uid): side for uid, side in raw.items() if side in ("A", "B")}
        except (ValueError, TypeError, AttributeError):
            # A corrupt blob loses that poll's tally, never the poll — the board
            # still closes and still announces, just from zero.
            votes = {}
        out.append(
            {
                "poll_id": r["poll_id"],
                "guild_id": int(r["guild_id"]) if r["guild_id"] else None,
                "channel_id": int(r["channel_id"]),
                "message_id": int(r["message_id"]) if r["message_id"] else None,
                "option_a": r["option_a"],
                "option_b": r["option_b"],
                "votes": votes,
                "created_at": r["created_at"],
                "end_ts": r["end_ts"],
            }
        )
    return out
