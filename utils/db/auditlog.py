"""utils/db.auditlog — audit-log config.

Part of the utils/db package (split from the former single-file utils/db.py).
Shared connection state + schema machinery live in utils/db/_core.py; this
module holds the audit-log config accessors and registers its table setup with _core so
db.init() creates them in the right order.
"""

import json


from ._core import _commit, _conn, log, register_init

# ══════════════════════════════════════════════════════════════════════════════
#  Audit Log
# ══════════════════════════════════════════════════════════════════════════════
# Config is stored as a single row per guild.
# `events` is a JSON-encoded list of enabled event keys.
# An absent row means "not configured".

# Full list of supported audit event keys — kept here so the migration and
# get function can default to "all events" without importing from auditlog.py.
_AUDIT_ALL_EVENTS: list[str] = [
    "msg_delete",
    "msg_edit",
    "member_join",
    "member_leave",
    "member_ban",
    "member_unban",
    "nick_change",
    "role_update",
    "channel_create",
    "channel_delete",
    "role_create",
    "role_delete",
    "automod_action",
]

_AUDIT_ALL_EVENTS_JSON: str = json.dumps(_AUDIT_ALL_EVENTS)


async def _ensure_auditlog_tables() -> None:
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS auditlog_config (
            guild_id    TEXT PRIMARY KEY,
            enabled     INTEGER NOT NULL DEFAULT 0,
            channel_id  TEXT,
            events      TEXT NOT NULL DEFAULT '[]'
        )
    """)
    await _commit()


async def _migrate_auditlog_null_events() -> None:
    """
    One-time migration: rows created via partial INSERTs (set_auditlog_channel /
    set_auditlog_enabled) before the DEFAULT was in place may have events=NULL.
    Backfill them to all-events so logging isn't silently broken.

    Safe to run on every startup — the WHERE clause makes it a no-op when there
    is nothing to fix.
    """
    async with _conn().execute(
        "SELECT COUNT(*) FROM auditlog_config WHERE events IS NULL"
    ) as cur:
        row = await cur.fetchone()
    null_count = row[0]

    if null_count == 0:
        return

    await _conn().execute(
        "UPDATE auditlog_config SET events=? WHERE events IS NULL",
        (_AUDIT_ALL_EVENTS_JSON,),
    )
    await _commit()
    log.info(
        f"DB migration: backfilled auditlog events for {null_count} guild(s) "
        f"(was NULL, now all-events)"
    )


async def get_auditlog_config(guild_id: int) -> dict | None:
    """Return the audit log config for a guild, or None if not yet set up."""
    async with _conn().execute(
        "SELECT enabled, channel_id, events FROM auditlog_config WHERE guild_id=? LIMIT 1",
        (str(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    # events may still be NULL on very old rows that slipped past the migration;
    # default to all events so logging isn't silently disabled.
    raw_events = row["events"]
    events = json.loads(raw_events) if raw_events is not None else _AUDIT_ALL_EVENTS
    return {
        "enabled": bool(row["enabled"]),
        "channel_id": row["channel_id"],
        "events": events,
    }


async def set_auditlog_channel(guild_id: int, channel_id: int) -> None:
    """Set (or update) the log channel for a guild. Creates the row if absent."""
    await _conn().execute(
        """INSERT INTO auditlog_config (guild_id, channel_id, events)
           VALUES (?, ?, ?)
           ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id""",
        (str(guild_id), str(channel_id), _AUDIT_ALL_EVENTS_JSON),
    )
    await _commit()


async def set_auditlog_enabled(guild_id: int, enabled: bool) -> None:
    """Enable or disable audit logging for a guild. Creates the row if absent."""
    await _conn().execute(
        """INSERT INTO auditlog_config (guild_id, enabled, events)
           VALUES (?, ?, ?)
           ON CONFLICT(guild_id) DO UPDATE SET enabled=excluded.enabled""",
        (str(guild_id), 1 if enabled else 0, _AUDIT_ALL_EVENTS_JSON),
    )
    await _commit()


async def set_auditlog_events(guild_id: int, events: set[str]) -> None:
    """Replace the full set of enabled events for a guild. Creates row if absent."""
    await _conn().execute(
        """INSERT INTO auditlog_config (guild_id, events)
           VALUES (?, ?)
           ON CONFLICT(guild_id) DO UPDATE SET events=excluded.events""",
        (str(guild_id), json.dumps(sorted(events))),
    )
    await _commit()


async def _init_auditlog():
    await _ensure_auditlog_tables()
    await _migrate_auditlog_null_events()


register_init(_init_auditlog)
