"""utils/db.tickets — support ticket system.

Part of the utils/db package (split from the former single-file utils/db.py).
Shared connection state + schema machinery live in utils/db/_core.py; this
module holds the ticket accessors and registers its table setup with _core so
db.init() creates them in the right order.
"""

import time
from typing import Any

import aiosqlite

from ._core import _conn, register_init

# ══════════════════════════════════════════════════════════════════════════════
#  Tickets
# ══════════════════════════════════════════════════════════════════════════════
# `ticket_config` holds per-guild settings: the staff role that can work
# tickets, where transcripts/log events go, where the panel lives, and the
# per-user open-ticket cap.
#
# `tickets` is one row per ticket. `number` is the per-guild human-facing
# counter ("#42"); `id` is the global primary key the views/buttons use.
# `thread_id` is NULL only during the short window between reserving the row
# and Discord creating the thread — a crash in that window leaves a stale row
# that close_stale_tickets() sweeps on startup. `status` is 'open' or 'closed';
# closes are guarded by a conditional UPDATE so two simultaneous closers can't
# both "win" (double transcript / double log post).
_TICKET_DEFAULTS = {
    "enabled": False,
    "staff_role_id": None,
    "log_channel_id": None,
    "panel_channel_id": None,
    "panel_message_id": None,
    "panel_title": None,
    "panel_message": None,
    "max_open": 3,
}
_TICKET_BOOL_COLS = ("enabled",)
_TICKET_CFG_COLS = tuple(_TICKET_DEFAULTS)


async def _ensure_ticket_tables() -> None:
    await _conn().executescript("""
        CREATE TABLE IF NOT EXISTS ticket_config (
            guild_id         TEXT PRIMARY KEY,
            enabled          INTEGER NOT NULL DEFAULT 0,
            staff_role_id    TEXT,
            log_channel_id   TEXT,
            panel_channel_id TEXT,
            panel_message_id TEXT,
            panel_title      TEXT,
            panel_message    TEXT,
            max_open         INTEGER NOT NULL DEFAULT 3
        );

        CREATE TABLE IF NOT EXISTS tickets (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id     TEXT NOT NULL,
            number       INTEGER NOT NULL,
            user_id      TEXT NOT NULL,
            thread_id    TEXT,
            subject      TEXT,
            status       TEXT NOT NULL DEFAULT 'open',
            claimed_by   TEXT,
            created_at   REAL NOT NULL,
            closed_at    REAL,
            closed_by    TEXT,
            close_reason TEXT
        );
        CREATE INDEX IF NOT EXISTS tickets_guild_status
            ON tickets (guild_id, status);
        CREATE INDEX IF NOT EXISTS tickets_thread ON tickets (thread_id);
    """)
    await _conn().commit()


def _ticket_config_row(row: aiosqlite.Row) -> dict:
    out: dict = {}
    for col in _TICKET_CFG_COLS:
        val = row[col]
        out[col] = bool(val) if col in _TICKET_BOOL_COLS else val
    return out


async def get_ticket_config(guild_id: int) -> dict:
    """Return the ticket config for a guild, defaults merged in when unset."""
    async with _conn().execute(
        f"SELECT {', '.join(_TICKET_CFG_COLS)} FROM ticket_config "
        "WHERE guild_id=? LIMIT 1",
        (str(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    return _ticket_config_row(row) if row else dict(_TICKET_DEFAULTS)


async def set_ticket_config(guild_id: int, **kwargs: Any) -> None:
    """Merge kwargs into the guild's config row (creating it from defaults)."""
    current = await get_ticket_config(guild_id)
    current.update(kwargs)
    values = [str(guild_id)]
    for col in _TICKET_CFG_COLS:
        val = current[col]
        if col in _TICKET_BOOL_COLS:
            values.append(1 if val else 0)
        else:
            values.append(val)
    placeholders = ", ".join(["?"] * (len(_TICKET_CFG_COLS) + 1))
    updates = ", ".join(f"{c}=excluded.{c}" for c in _TICKET_CFG_COLS)
    await _conn().execute(
        f"INSERT INTO ticket_config (guild_id, {', '.join(_TICKET_CFG_COLS)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT(guild_id) DO UPDATE SET {updates}",
        values,
    )
    await _conn().commit()


async def create_ticket(guild_id: int, user_id: int, subject: str | None) -> dict:
    """Reserve a ticket row (thread not yet created) and return it.

    The per-guild `number` is computed inside the INSERT itself
    (MAX(number)+1) so two near-simultaneous opens can never be handed the
    same ticket number.
    """
    cur = await _conn().execute(
        "INSERT INTO tickets (guild_id, number, user_id, subject, status, created_at) "
        "VALUES (?, "
        "  (SELECT COALESCE(MAX(number), 0) + 1 FROM tickets WHERE guild_id=?), "
        "  ?, ?, 'open', ?)",
        (str(guild_id), str(guild_id), str(user_id), subject, time.time()),
    )
    await _conn().commit()
    ticket = await get_ticket(cur.lastrowid)
    assert ticket is not None
    return ticket


async def get_ticket(ticket_id: int) -> dict | None:
    async with _conn().execute(
        "SELECT * FROM tickets WHERE id=? LIMIT 1", (ticket_id,)
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def get_ticket_by_thread(thread_id: int) -> dict | None:
    async with _conn().execute(
        "SELECT * FROM tickets WHERE thread_id=? LIMIT 1", (str(thread_id),)
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def set_ticket_thread(ticket_id: int, thread_id: int) -> None:
    """Attach the created Discord thread to a reserved ticket row."""
    await _conn().execute(
        "UPDATE tickets SET thread_id=? WHERE id=?", (str(thread_id), ticket_id)
    )
    await _conn().commit()


async def delete_ticket(ticket_id: int) -> None:
    """Drop a reserved row whose thread creation failed (rollback of create)."""
    await _conn().execute("DELETE FROM tickets WHERE id=?", (ticket_id,))
    await _conn().commit()


async def count_open_tickets(guild_id: int, user_id: int) -> int:
    """How many tickets this member currently has open (the per-user cap)."""
    async with _conn().execute(
        "SELECT COUNT(*) AS n FROM tickets "
        "WHERE guild_id=? AND user_id=? AND status='open'",
        (str(guild_id), str(user_id)),
    ) as cur:
        row = await cur.fetchone()
    return int(row["n"]) if row else 0


async def get_open_tickets_for_user(guild_id: int, user_id: int) -> list[dict]:
    async with _conn().execute(
        "SELECT * FROM tickets "
        "WHERE guild_id=? AND user_id=? AND status='open' ORDER BY id",
        (str(guild_id), str(user_id)),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def claim_ticket(ticket_id: int, staff_id: int) -> bool:
    """Claim an open, unclaimed ticket. Returns False if it was already
    claimed or closed (the conditional UPDATE loses the race)."""
    cur = await _conn().execute(
        "UPDATE tickets SET claimed_by=? "
        "WHERE id=? AND status='open' AND claimed_by IS NULL",
        (str(staff_id), ticket_id),
    )
    await _conn().commit()
    return cur.rowcount > 0


async def close_ticket(
    ticket_id: int, closed_by: int | None, reason: str | None
) -> bool:
    """Close an open ticket. Returns False if it was already closed, so two
    simultaneous closers can't both run the transcript/log path."""
    cur = await _conn().execute(
        "UPDATE tickets SET status='closed', closed_at=?, closed_by=?, close_reason=? "
        "WHERE id=? AND status='open'",
        (
            time.time(),
            str(closed_by) if closed_by is not None else None,
            reason,
            ticket_id,
        ),
    )
    await _conn().commit()
    return cur.rowcount > 0


async def close_stale_tickets() -> int:
    """Close open rows that never got a thread (crash between the row reserve
    and Discord's thread create). Run once at startup; returns rows swept."""
    cur = await _conn().execute(
        "UPDATE tickets SET status='closed', closed_at=?, "
        "close_reason='startup cleanup: thread was never created' "
        "WHERE status='open' AND thread_id IS NULL",
        (time.time(),),
    )
    await _conn().commit()
    return cur.rowcount


register_init(_ensure_ticket_tables)
