"""
utils/db.py
Async SQLite storage — replaces utils/storage.py for all cog code.

Single file: data/nanobot.db
All tables created on first run via init().
Call await db.init() once in NanoBot.setup_hook().

──────────────────────────────────────────────────────
Tables
──────────────────────────────────────────────────────
  tags              (guild_id, scope, name) PK
                    scope = "global" | user_id string
  notes             rows per note, auto-id
  prefixes          (guild_id) PK
  unban_schedules   (key) PK  e.g. "guild_id:user_id"
  slow_schedules    (channel_id) PK
  reminders         (id) PK  — 6-char alphanumeric
"""

import json
import logging
import os
from typing import Any

import aiosqlite

log = logging.getLogger("NanoBot.db")

_DB_PATH = os.path.join("data", "nanobot.db")

# Module-level connection — opened once in init(), shared for the bot's lifetime
_db: aiosqlite.Connection | None = None


async def init() -> None:
    """Open the database and create all tables. Call once at bot startup."""
    global _db
    os.makedirs("data", exist_ok=True)
    _db = await aiosqlite.connect(_DB_PATH)
    _db.row_factory = aiosqlite.Row

    # WAL mode: readers never block writers, writers never block readers
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA foreign_keys=ON")

    await _db.executescript("""
        CREATE TABLE IF NOT EXISTS tags (
            guild_id  TEXT NOT NULL,
            scope     TEXT NOT NULL,   -- "global" or user_id
            name      TEXT NOT NULL,
            content   TEXT,
            image_url TEXT,
            by_id     TEXT,
            by_name   TEXT,
            PRIMARY KEY (guild_id, scope, name)
        );

        CREATE TABLE IF NOT EXISTS notes (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id  TEXT NOT NULL,
            user_id   TEXT NOT NULL,
            content   TEXT NOT NULL,
            by_id     TEXT NOT NULL,
            by_name   TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS notes_guild_user ON notes (guild_id, user_id);

        CREATE TABLE IF NOT EXISTS prefixes (
            guild_id  TEXT PRIMARY KEY,
            prefix    TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS unban_schedules (
            key       TEXT PRIMARY KEY,   -- "guild_id:user_id"
            guild_id  TEXT NOT NULL,
            user_id   TEXT NOT NULL,
            until     REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS slow_schedules (
            channel_id  TEXT PRIMARY KEY,
            guild_id    TEXT NOT NULL,
            until       REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS reminders (
            id          TEXT PRIMARY KEY,
            target_id   TEXT NOT NULL,
            set_by_id   TEXT NOT NULL,
            guild_id    TEXT NOT NULL,
            channel_id  TEXT NOT NULL,
            message     TEXT NOT NULL,
            due         REAL NOT NULL,
            duration    REAL NOT NULL,
            dm          INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS reminders_target ON reminders (target_id);
    """)

    await _db.commit()
    await _ensure_warnings_tables()
    await _ensure_welcome_tables()
    await _ensure_votes_table()
    await _ensure_auditlog_table()
    await _ensure_automod_tables()
    await _ensure_role_panels_table()
    log.info(f"Database ready: {_DB_PATH}")


async def close() -> None:
    """Close the database connection cleanly."""
    global _db
    if _db:
        await _db.close()
        _db = None


def _conn() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("db.init() has not been called")
    return _db


# ══════════════════════════════════════════════════════════════════════════════
#  Tags
# ══════════════════════════════════════════════════════════════════════════════

async def get_tag(guild_id: int, name: str, user_id: int) -> dict | None:
    """Personal tag first, then global. Returns dict or None."""
    async with _conn().execute(
        "SELECT content, image_url FROM tags "
        "WHERE guild_id=? AND name=? AND scope=? LIMIT 1",
        (str(guild_id), name, str(user_id)),
    ) as cur:
        row = await cur.fetchone()
    if row:
        return {"content": row["content"], "image_url": row["image_url"]}

    async with _conn().execute(
        "SELECT content, image_url FROM tags "
        "WHERE guild_id=? AND name=? AND scope='global' LIMIT 1",
        (str(guild_id), name),
    ) as cur:
        row = await cur.fetchone()
    if row:
        return {"content": row["content"], "image_url": row["image_url"]}
    return None


async def get_personal_tags(guild_id: int, user_id: int) -> dict:
    """All personal tags for a user in a guild. Returns {name: {content, image_url}}."""
    async with _conn().execute(
        "SELECT name, content, image_url FROM tags WHERE guild_id=? AND scope=?",
        (str(guild_id), str(user_id)),
    ) as cur:
        rows = await cur.fetchall()
    return {r["name"]: {"content": r["content"], "image_url": r["image_url"]} for r in rows}


async def get_global_tags(guild_id: int) -> dict:
    """All global tags for a guild. Returns {name: {content, image_url, by_id, by_name}}."""
    async with _conn().execute(
        "SELECT name, content, image_url, by_id, by_name FROM tags "
        "WHERE guild_id=? AND scope='global'",
        (str(guild_id),),
    ) as cur:
        rows = await cur.fetchall()
    return {
        r["name"]: {
            "content": r["content"], "image_url": r["image_url"],
            "by_id": r["by_id"],    "by_name": r["by_name"],
        }
        for r in rows
    }


async def tag_exists(guild_id: int, scope: str, name: str) -> bool:
    async with _conn().execute(
        "SELECT 1 FROM tags WHERE guild_id=? AND scope=? AND name=? LIMIT 1",
        (str(guild_id), scope, name),
    ) as cur:
        return await cur.fetchone() is not None


async def set_tag(
    guild_id:  int,
    scope:     str,     # "global" or str(user_id)
    name:      str,
    content:   str | None,
    image_url: str | None,
    by_id:     str | None = None,
    by_name:   str | None = None,
) -> None:
    """Insert or replace a tag."""
    await _conn().execute(
        """INSERT INTO tags (guild_id, scope, name, content, image_url, by_id, by_name)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(guild_id, scope, name) DO UPDATE SET
               content=excluded.content,
               image_url=excluded.image_url,
               by_id=excluded.by_id,
               by_name=excluded.by_name""",
        (str(guild_id), scope, name, content, image_url, by_id, by_name),
    )
    await _conn().commit()


async def update_tag_image(guild_id: int, scope: str, name: str, image_url: str | None) -> None:
    await _conn().execute(
        "UPDATE tags SET image_url=? WHERE guild_id=? AND scope=? AND name=?",
        (image_url, str(guild_id), scope, name),
    )
    await _conn().commit()


async def update_tag_content(guild_id: int, scope: str, name: str, content: str) -> None:
    await _conn().execute(
        "UPDATE tags SET content=? WHERE guild_id=? AND scope=? AND name=?",
        (content, str(guild_id), scope, name),
    )
    await _conn().commit()


async def delete_tag(guild_id: int, scope: str, name: str) -> bool:
    """Returns True if a row was deleted."""
    cur = await _conn().execute(
        "DELETE FROM tags WHERE guild_id=? AND scope=? AND name=?",
        (str(guild_id), scope, name),
    )
    await _conn().commit()
    return cur.rowcount > 0


async def find_tag_scope(guild_id: int, name: str, user_id: int) -> str | None:
    """
    Return the scope string if the user can edit/delete the tag, else None.
    Personal takes priority. Requires manage_messages for global (checked by caller).
    """
    async with _conn().execute(
        "SELECT scope FROM tags WHERE guild_id=? AND name=? AND scope=? LIMIT 1",
        (str(guild_id), name, str(user_id)),
    ) as cur:
        row = await cur.fetchone()
    if row:
        return row["scope"]
    async with _conn().execute(
        "SELECT scope FROM tags WHERE guild_id=? AND name=? AND scope='global' LIMIT 1",
        (str(guild_id), name),
    ) as cur:
        row = await cur.fetchone()
    return row["scope"] if row else None


# ══════════════════════════════════════════════════════════════════════════════
#  Notes
# ══════════════════════════════════════════════════════════════════════════════

async def add_note(
    guild_id: int, user_id: int, content: str,
    by_id: str, by_name: str, created_at: str,
) -> int:
    """Add a note. Returns total note count for that user in that guild."""
    await _conn().execute(
        "INSERT INTO notes (guild_id, user_id, content, by_id, by_name, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (str(guild_id), str(user_id), content, by_id, by_name, created_at),
    )
    await _conn().commit()
    async with _conn().execute(
        "SELECT COUNT(*) FROM notes WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id)),
    ) as cur:
        row = await cur.fetchone()
    return row[0]


async def get_notes(guild_id: int, user_id: int) -> list[dict]:
    """All notes for a user, oldest first."""
    async with _conn().execute(
        "SELECT content, by_id, by_name, created_at FROM notes "
        "WHERE guild_id=? AND user_id=? ORDER BY id ASC",
        (str(guild_id), str(user_id)),
    ) as cur:
        rows = await cur.fetchall()
    return [{"note": r["content"], "by_id": r["by_id"],
              "by_name": r["by_name"], "at": r["created_at"]} for r in rows]


async def get_note_count(guild_id: int, user_id: int) -> int:
    async with _conn().execute(
        "SELECT COUNT(*) FROM notes WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id)),
    ) as cur:
        row = await cur.fetchone()
    return row[0]


async def clear_notes(guild_id: int, user_id: int) -> int:
    """Delete all notes for a user. Returns count deleted."""
    cur = await _conn().execute(
        "DELETE FROM notes WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id)),
    )
    await _conn().commit()
    return cur.rowcount


# ══════════════════════════════════════════════════════════════════════════════
#  Prefixes
# ══════════════════════════════════════════════════════════════════════════════

async def get_prefix(guild_id: int) -> str | None:
    async with _conn().execute(
        "SELECT prefix FROM prefixes WHERE guild_id=?", (str(guild_id),)
    ) as cur:
        row = await cur.fetchone()
    return row["prefix"] if row else None


async def set_prefix(guild_id: int, prefix: str) -> None:
    await _conn().execute(
        "INSERT INTO prefixes (guild_id, prefix) VALUES (?,?) "
        "ON CONFLICT(guild_id) DO UPDATE SET prefix=excluded.prefix",
        (str(guild_id), prefix),
    )
    await _conn().commit()


async def get_all_prefixes() -> dict[str, str]:
    """Returns {guild_id_str: prefix} for all guilds."""
    async with _conn().execute("SELECT guild_id, prefix FROM prefixes") as cur:
        rows = await cur.fetchall()
    return {r["guild_id"]: r["prefix"] for r in rows}


# ══════════════════════════════════════════════════════════════════════════════
#  Unban schedules
# ══════════════════════════════════════════════════════════════════════════════

async def set_unban(key: str, guild_id: int, user_id: int, until: float) -> None:
    await _conn().execute(
        "INSERT INTO unban_schedules (key, guild_id, user_id, until) VALUES (?,?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET until=excluded.until",
        (key, str(guild_id), str(user_id), until),
    )
    await _conn().commit()


async def remove_unban(key: str) -> None:
    await _conn().execute("DELETE FROM unban_schedules WHERE key=?", (key,))
    await _conn().commit()


async def get_all_unbans() -> dict:
    async with _conn().execute(
        "SELECT key, guild_id, user_id, until FROM unban_schedules"
    ) as cur:
        rows = await cur.fetchall()
    return {r["key"]: {"guild_id": r["guild_id"], "user_id": r["user_id"], "until": r["until"]}
            for r in rows}


# ══════════════════════════════════════════════════════════════════════════════
#  Slow schedules
# ══════════════════════════════════════════════════════════════════════════════

async def set_slow(channel_id: int, guild_id: int, until: float) -> None:
    await _conn().execute(
        "INSERT INTO slow_schedules (channel_id, guild_id, until) VALUES (?,?,?) "
        "ON CONFLICT(channel_id) DO UPDATE SET until=excluded.until",
        (str(channel_id), str(guild_id), until),
    )
    await _conn().commit()


async def remove_slow(channel_id: int) -> None:
    await _conn().execute(
        "DELETE FROM slow_schedules WHERE channel_id=?", (str(channel_id),)
    )
    await _conn().commit()


async def get_all_slows() -> dict:
    async with _conn().execute(
        "SELECT channel_id, guild_id, until FROM slow_schedules"
    ) as cur:
        rows = await cur.fetchall()
    return {r["channel_id"]: {"guild_id": r["guild_id"], "until": r["until"]}
            for r in rows}


# ══════════════════════════════════════════════════════════════════════════════
#  Reminders
# ══════════════════════════════════════════════════════════════════════════════

async def reminder_id_exists(rid: str) -> bool:
    async with _conn().execute(
        "SELECT 1 FROM reminders WHERE id=? LIMIT 1", (rid,)
    ) as cur:
        return await cur.fetchone() is not None


async def set_reminder(info: dict) -> None:
    await _conn().execute(
        """INSERT OR IGNORE INTO reminders
           (id, target_id, set_by_id, guild_id, channel_id, message, due, duration, dm)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            info["id"], info["target_id"], info["set_by_id"],
            info["guild_id"], info["channel_id"], info["message"],
            info["due"], info.get("duration", 0),
            1 if info.get("dm", True) else 0,
        ),
    )
    await _conn().commit()


async def remove_reminder(rid: str) -> None:
    await _conn().execute("DELETE FROM reminders WHERE id=?", (rid,))
    await _conn().commit()


async def get_all_reminders() -> dict:
    async with _conn().execute(
        "SELECT id, target_id, set_by_id, guild_id, channel_id, message, due, duration, dm "
        "FROM reminders"
    ) as cur:
        rows = await cur.fetchall()
    return {r["id"]: _reminder_row(r) for r in rows}


async def get_user_reminders(user_id: int) -> dict:
    async with _conn().execute(
        "SELECT id, target_id, set_by_id, guild_id, channel_id, message, due, duration, dm "
        "FROM reminders WHERE target_id=?",
        (str(user_id),),
    ) as cur:
        rows = await cur.fetchall()
    return {r["id"]: _reminder_row(r) for r in rows}


async def count_user_reminders(user_id: int) -> int:
    async with _conn().execute(
        "SELECT COUNT(*) FROM reminders WHERE target_id=?", (str(user_id),)
    ) as cur:
        row = await cur.fetchone()
    return row[0]


def _reminder_row(r: aiosqlite.Row) -> dict:
    return {
        "id":         r["id"],
        "target_id":  r["target_id"],
        "set_by_id":  r["set_by_id"],
        "guild_id":   r["guild_id"],
        "channel_id": r["channel_id"],
        "message":    r["message"],
        "due":        r["due"],
        "duration":   r["duration"],
        "dm":         bool(r["dm"]),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Warnings
# ══════════════════════════════════════════════════════════════════════════════

async def _ensure_warnings_tables():
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS warnings (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id   TEXT NOT NULL,
            user_id    TEXT NOT NULL,
            reason     TEXT NOT NULL,
            by_id      TEXT NOT NULL,
            by_name    TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    await _conn().execute(
        "CREATE INDEX IF NOT EXISTS warnings_guild_user ON warnings (guild_id, user_id)"
    )
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS warn_config (
            guild_id   TEXT PRIMARY KEY,
            kick_at    INTEGER NOT NULL DEFAULT 0,
            ban_at     INTEGER NOT NULL DEFAULT 0,
            dm_user    INTEGER NOT NULL DEFAULT 1
        )
    """)
    await _conn().commit()


async def add_warning(
    guild_id: int, user_id: int, reason: str,
    by_id: str, by_name: str, created_at: str,
) -> int:
    """Add a warning. Returns new total warning count for that user."""
    await _conn().execute(
        "INSERT INTO warnings (guild_id, user_id, reason, by_id, by_name, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (str(guild_id), str(user_id), reason, by_id, by_name, created_at),
    )
    await _conn().commit()
    async with _conn().execute(
        "SELECT COUNT(*) FROM warnings WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id)),
    ) as cur:
        row = await cur.fetchone()
    return row[0]


async def get_warnings(guild_id: int, user_id: int) -> list[dict]:
    async with _conn().execute(
        "SELECT id, reason, by_name, created_at FROM warnings "
        "WHERE guild_id=? AND user_id=? ORDER BY id ASC",
        (str(guild_id), str(user_id)),
    ) as cur:
        rows = await cur.fetchall()
    return [{"id": r["id"], "reason": r["reason"],
              "by_name": r["by_name"], "at": r["created_at"]} for r in rows]


async def get_warning_count(guild_id: int, user_id: int) -> int:
    async with _conn().execute(
        "SELECT COUNT(*) FROM warnings WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id)),
    ) as cur:
        row = await cur.fetchone()
    return row[0]


async def clear_warnings(guild_id: int, user_id: int) -> int:
    cur = await _conn().execute(
        "DELETE FROM warnings WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id)),
    )
    await _conn().commit()
    return cur.rowcount


async def get_warn_config(guild_id: int) -> dict:
    async with _conn().execute(
        "SELECT kick_at, ban_at, dm_user FROM warn_config WHERE guild_id=?",
        (str(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    if row:
        return {"kick_at": row["kick_at"], "ban_at": row["ban_at"], "dm_user": bool(row["dm_user"])}
    return {"kick_at": 0, "ban_at": 0, "dm_user": True}


async def set_warn_config(guild_id: int, kick_at: int, ban_at: int, dm_user: bool) -> None:
    await _conn().execute(
        "INSERT INTO warn_config (guild_id, kick_at, ban_at, dm_user) VALUES (?,?,?,?) "
        "ON CONFLICT(guild_id) DO UPDATE SET kick_at=excluded.kick_at, "
        "ban_at=excluded.ban_at, dm_user=excluded.dm_user",
        (str(guild_id), kick_at, ban_at, 1 if dm_user else 0),
    )
    await _conn().commit()


# ══════════════════════════════════════════════════════════════════════════════
#  Welcome / Leave
# ══════════════════════════════════════════════════════════════════════════════

async def _ensure_welcome_tables():
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS welcome_config (
            guild_id   TEXT PRIMARY KEY,
            enabled    INTEGER NOT NULL DEFAULT 0,
            channel_id TEXT,
            title      TEXT,
            content    TEXT,
            image_url  TEXT,
            dm         INTEGER NOT NULL DEFAULT 0
        )
    """)
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS leave_config (
            guild_id   TEXT PRIMARY KEY,
            enabled    INTEGER NOT NULL DEFAULT 0,
            channel_id TEXT,
            title      TEXT,
            content    TEXT,
            image_url  TEXT,
            dm         INTEGER NOT NULL DEFAULT 0
        )
    """)
    await _conn().commit()


async def _get_event_config(table: str, guild_id: int) -> dict | None:
    async with _conn().execute(
        f"SELECT enabled, channel_id, title, content, image_url, dm FROM {table} WHERE guild_id=?",
        (str(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    return {
        "enabled":    bool(row["enabled"]),
        "channel_id": row["channel_id"],
        "title":      row["title"],
        "content":    row["content"],
        "image_url":  row["image_url"],
        "dm":         bool(row["dm"]),
    }


async def _set_event_config(table: str, guild_id: int, **kwargs) -> None:
    await _conn().execute(
        f"INSERT INTO {table} (guild_id, enabled, channel_id, title, content, image_url, dm) "
        "VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(guild_id) DO UPDATE SET "
        "enabled=excluded.enabled, channel_id=excluded.channel_id, "
        "title=excluded.title, content=excluded.content, "
        "image_url=excluded.image_url, dm=excluded.dm",
        (
            str(guild_id),
            1 if kwargs.get("enabled", False) else 0,
            kwargs.get("channel_id"),
            kwargs.get("title"),
            kwargs.get("content"),
            kwargs.get("image_url"),
            1 if kwargs.get("dm", False) else 0,
        ),
    )
    await _conn().commit()


async def get_welcome_config(guild_id: int) -> dict | None:
    return await _get_event_config("welcome_config", guild_id)


async def set_welcome_config(guild_id: int, **kwargs) -> None:
    await _set_event_config("welcome_config", guild_id, **kwargs)


async def get_leave_config(guild_id: int) -> dict | None:
    return await _get_event_config("leave_config", guild_id)


async def set_leave_config(guild_id: int, **kwargs) -> None:
    await _set_event_config("leave_config", guild_id, **kwargs)


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
    now = __import__("time").time()

    # Cooldowns: top.gg = 12h, DBL = 24h. Grace period = 2h extra.
    cooldown = 14 * 3600 if site == "topgg" else 26 * 3600

    async with _conn().execute(
        "SELECT voted_at, streak, notify FROM votes WHERE user_id=? AND site=?",
        (uid, site),
    ) as cur:
        row = await cur.fetchone()

    if row:
        elapsed = now - row["voted_at"]
        streak  = (row["streak"] + 1) if elapsed <= cooldown else 1
        notify  = bool(row["notify"])
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

    return {"user_id": uid, "site": site, "voted_at": now, "streak": streak, "notify": notify}


async def get_vote(user_id: int, site: str) -> dict | None:
    async with _conn().execute(
        "SELECT user_id, site, voted_at, streak, notify FROM votes WHERE user_id=? AND site=?",
        (str(user_id), site),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    return {
        "user_id":  row["user_id"],
        "site":     row["site"],
        "voted_at": row["voted_at"],
        "streak":   row["streak"],
        "notify":   bool(row["notify"]),
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
        {"user_id": r["user_id"], "site": r["site"],
         "voted_at": r["voted_at"], "streak": r["streak"]}
        for r in rows
    ]


async def has_voted_recently(user_id: int, site: str) -> bool:
    """True if the user has an active vote (within the site's cooldown window)."""
    import time

    row = await get_vote(user_id, site)
    if not row:
        return False
    cooldown = 12 * 3600 if site == "topgg" else 24 * 3600
    return (time.time() - row["voted_at"]) < cooldown


# ══════════════════════════════════════════════════════════════════════════════
#  Audit Log
# ══════════════════════════════════════════════════════════════════════════════

import json as _json

_ALL_AUDIT_EVENTS: list[str] = [
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
]


async def _ensure_auditlog_table() -> None:
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS auditlog_config (
            guild_id    TEXT PRIMARY KEY,
            channel_id  TEXT,
            enabled     INTEGER NOT NULL DEFAULT 0,
            events      TEXT    -- JSON array of enabled event keys; NULL = all
        )
    """)
    await _conn().commit()


async def get_auditlog_config(guild_id: int) -> dict | None:
    """Return the audit log config for a guild, or None if never configured."""
    async with _conn().execute(
        "SELECT channel_id, enabled, events FROM auditlog_config WHERE guild_id=?",
        (str(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    raw_events = row["events"]
    events = _json.loads(raw_events) if raw_events else list(_ALL_AUDIT_EVENTS)
    return {
        "channel_id": row["channel_id"],
        "enabled": bool(row["enabled"]),
        "events": events,
    }


async def set_auditlog_channel(guild_id: int, channel_id: int) -> None:
    """Set (or update) the log channel for a guild."""
    await _conn().execute(
        """INSERT INTO auditlog_config (guild_id, channel_id, enabled, events)
           VALUES (?, ?, 0, NULL)
           ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id""",
        (str(guild_id), str(channel_id)),
    )
    await _conn().commit()


async def set_auditlog_enabled(guild_id: int, enabled: bool) -> None:
    """Enable or disable audit logging for a guild."""
    await _conn().execute(
        """INSERT INTO auditlog_config (guild_id, channel_id, enabled, events)
           VALUES (?, NULL, ?, NULL)
           ON CONFLICT(guild_id) DO UPDATE SET enabled=excluded.enabled""",
        (str(guild_id), 1 if enabled else 0),
    )
    await _conn().commit()


async def set_auditlog_events(guild_id: int, events: set[str]) -> None:
    """Persist the set of enabled event keys for a guild."""
    await _conn().execute(
        """INSERT INTO auditlog_config (guild_id, channel_id, enabled, events)
           VALUES (?, NULL, 0, ?)
           ON CONFLICT(guild_id) DO UPDATE SET events=excluded.events""",
        (str(guild_id), _json.dumps(sorted(events))),
    )
    await _conn().commit()


# ══════════════════════════════════════════════════════════════════════════════
#  AutoMod
# ══════════════════════════════════════════════════════════════════════════════

_DEFAULT_RULES: dict = {
    "spam":     {"enabled": False, "count": 5, "seconds": 5, "action": "warn"},
    "invites":  {"enabled": False, "action": "delete"},
    "links":    {"enabled": False, "action": "delete"},
    "caps":     {"enabled": False, "percent": 70, "min_length": 10, "action": "warn"},
    "mentions": {"enabled": False, "limit": 5, "action": "warn"},
    "badwords": {"enabled": False, "action": "delete"},
}


async def _ensure_automod_tables() -> None:
    await _conn().executescript("""
        CREATE TABLE IF NOT EXISTS automod_config (
            guild_id        TEXT PRIMARY KEY,
            enabled         INTEGER NOT NULL DEFAULT 0,
            rules           TEXT NOT NULL DEFAULT '{}',
            ignore_channels TEXT NOT NULL DEFAULT '[]',
            ignore_roles    TEXT NOT NULL DEFAULT '[]'
        );

        CREATE TABLE IF NOT EXISTS automod_badwords (
            guild_id TEXT NOT NULL,
            word     TEXT NOT NULL,
            PRIMARY KEY (guild_id, word)
        );
    """)
    await _conn().commit()


def _parse_automod_row(row: aiosqlite.Row) -> dict:
    rules = _json.loads(row["rules"]) if row["rules"] else {}
    # Merge with defaults so new rule keys are always present
    merged = {}
    for key, defaults in _DEFAULT_RULES.items():
        merged[key] = {**defaults, **rules.get(key, {})}
    return {
        "enabled":         bool(row["enabled"]),
        "rules":           merged,
        "ignore_channels": _json.loads(row["ignore_channels"] or "[]"),
        "ignore_roles":    _json.loads(row["ignore_roles"] or "[]"),
    }


async def get_automod_config(guild_id: int) -> dict | None:
    """Return the automod config for a guild, or None if never configured."""
    async with _conn().execute(
        "SELECT enabled, rules, ignore_channels, ignore_roles "
        "FROM automod_config WHERE guild_id=?",
        (str(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    return _parse_automod_row(row) if row else None


async def _upsert_automod(guild_id: int) -> dict:
    """Ensure a row exists for the guild and return the current raw rules dict."""
    async with _conn().execute(
        "SELECT rules FROM automod_config WHERE guild_id=?", (str(guild_id),)
    ) as cur:
        row = await cur.fetchone()
    if row:
        return _json.loads(row["rules"]) if row["rules"] else {}
    await _conn().execute(
        "INSERT INTO automod_config (guild_id, enabled, rules, ignore_channels, ignore_roles) "
        "VALUES (?, 0, '{}', '[]', '[]')",
        (str(guild_id),),
    )
    await _conn().commit()
    return {}


async def set_automod_enabled(guild_id: int, enabled: bool) -> None:
    await _upsert_automod(guild_id)
    await _conn().execute(
        "UPDATE automod_config SET enabled=? WHERE guild_id=?",
        (1 if enabled else 0, str(guild_id)),
    )
    await _conn().commit()


async def set_automod_rule(guild_id: int, rule: str, **kwargs) -> None:
    """
    Update fields for a single rule. Only provided kwargs are changed;
    existing fields are preserved.
    """
    rules = await _upsert_automod(guild_id)
    current = rules.get(rule, dict(_DEFAULT_RULES.get(rule, {})))
    current.update(kwargs)
    rules[rule] = current
    await _conn().execute(
        "UPDATE automod_config SET rules=? WHERE guild_id=?",
        (_json.dumps(rules), str(guild_id)),
    )
    await _conn().commit()


async def toggle_automod_ignore(
    guild_id: int, kind: str, target_id: int
) -> str:
    """
    Toggle a channel or role exemption. kind = "channel" | "role".
    Returns "added" or "removed".
    """
    await _upsert_automod(guild_id)
    col = "ignore_channels" if kind == "channel" else "ignore_roles"
    async with _conn().execute(
        f"SELECT {col} FROM automod_config WHERE guild_id=?", (str(guild_id),)
    ) as cur:
        row = await cur.fetchone()
    lst = _json.loads(row[col] or "[]")
    sid = str(target_id)
    if sid in lst:
        lst.remove(sid)
        result = "removed"
    else:
        lst.append(sid)
        result = "added"
    await _conn().execute(
        f"UPDATE automod_config SET {col}=? WHERE guild_id=?",
        (_json.dumps(lst), str(guild_id)),
    )
    await _conn().commit()
    return result


async def add_automod_badword(guild_id: int, word: str) -> bool:
    """Add a word to the filter. Returns True if inserted, False if already exists."""
    try:
        await _conn().execute(
            "INSERT INTO automod_badwords (guild_id, word) VALUES (?, ?)",
            (str(guild_id), word.lower()),
        )
        await _conn().commit()
        return True
    except aiosqlite.IntegrityError:
        return False


async def remove_automod_badword(guild_id: int, word: str) -> bool:
    """Remove a word from the filter. Returns True if a row was deleted."""
    cur = await _conn().execute(
        "DELETE FROM automod_badwords WHERE guild_id=? AND word=?",
        (str(guild_id), word.lower()),
    )
    await _conn().commit()
    return cur.rowcount > 0


async def get_automod_badwords(guild_id: int) -> list[str]:
    """Return all bad words for a guild."""
    async with _conn().execute(
        "SELECT word FROM automod_badwords WHERE guild_id=? ORDER BY word ASC",
        (str(guild_id),),
    ) as cur:
        rows = await cur.fetchall()
    return [r["word"] for r in rows]


# ══════════════════════════════════════════════════════════════════════════════
#  Role Panels
# ══════════════════════════════════════════════════════════════════════════════


async def _ensure_role_panels_table() -> None:
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS role_panels (
            id          TEXT PRIMARY KEY,
            guild_id    TEXT NOT NULL,
            channel_id  TEXT,
            message_id  TEXT,
            title       TEXT NOT NULL,
            description TEXT,
            mode        TEXT NOT NULL DEFAULT 'toggle',
            entries     TEXT NOT NULL DEFAULT '[]'
        )
    """)
    await _conn().execute(
        "CREATE INDEX IF NOT EXISTS role_panels_guild ON role_panels (guild_id)"
    )
    await _conn().commit()


def _panel_row(row: aiosqlite.Row) -> dict:
    return {
        "id":          row["id"],
        "guild_id":    row["guild_id"],
        "channel_id":  row["channel_id"],
        "message_id":  row["message_id"],
        "title":       row["title"],
        "description": row["description"],
        "mode":        row["mode"],
        "entries":     _json.loads(row["entries"] or "[]"),
    }


async def create_role_panel(
    *,
    panel_id:    str,
    guild_id:    int,
    title:       str,
    description: str | None = None,
    mode:        str = "toggle",
) -> None:
    await _conn().execute(
        "INSERT INTO role_panels (id, guild_id, title, description, mode, entries) "
        "VALUES (?, ?, ?, ?, ?, '[]')",
        (panel_id, str(guild_id), title, description, mode),
    )
    await _conn().commit()


async def get_role_panel(panel_id: str) -> dict | None:
    async with _conn().execute(
        "SELECT id, guild_id, channel_id, message_id, title, description, mode, entries "
        "FROM role_panels WHERE id=?",
        (panel_id,),
    ) as cur:
        row = await cur.fetchone()
    return _panel_row(row) if row else None


async def get_role_panels_for_guild(guild_id: int) -> list[dict]:
    async with _conn().execute(
        "SELECT id, guild_id, channel_id, message_id, title, description, mode, entries "
        "FROM role_panels WHERE guild_id=? ORDER BY rowid ASC",
        (str(guild_id),),
    ) as cur:
        rows = await cur.fetchall()
    return [_panel_row(r) for r in rows]


async def get_all_role_panels() -> list[dict]:
    """Return all panels across all guilds — used on startup to re-register views."""
    async with _conn().execute(
        "SELECT id, guild_id, channel_id, message_id, title, description, mode, entries "
        "FROM role_panels"
    ) as cur:
        rows = await cur.fetchall()
    return [_panel_row(r) for r in rows]


async def edit_role_panel(
    panel_id:    str,
    *,
    title:       str,
    description: str | None,
    mode:        str,
) -> None:
    await _conn().execute(
        "UPDATE role_panels SET title=?, description=?, mode=? WHERE id=?",
        (title, description, mode, panel_id),
    )
    await _conn().commit()


async def update_role_panel_message(
    panel_id:   str,
    channel_id: int,
    message_id: int,
) -> None:
    await _conn().execute(
        "UPDATE role_panels SET channel_id=?, message_id=? WHERE id=?",
        (str(channel_id), str(message_id), panel_id),
    )
    await _conn().commit()


async def delete_role_panel(panel_id: str) -> None:
    await _conn().execute("DELETE FROM role_panels WHERE id=?", (panel_id,))
    await _conn().commit()


async def add_role_to_panel(panel_id: str, entry: dict) -> None:
    """Append an entry dict to the panel's entries JSON array."""
    async with _conn().execute(
        "SELECT entries FROM role_panels WHERE id=?", (panel_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return
    entries = _json.loads(row["entries"] or "[]")
    entries.append(entry)
    await _conn().execute(
        "UPDATE role_panels SET entries=? WHERE id=?",
        (_json.dumps(entries), panel_id),
    )
    await _conn().commit()


async def remove_role_from_panel(panel_id: str, role_id: int) -> None:
    """Remove the entry for role_id from the panel's entries array."""
    async with _conn().execute(
        "SELECT entries FROM role_panels WHERE id=?", (panel_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return
    entries = [e for e in _json.loads(row["entries"] or "[]") if e.get("role_id") != role_id]
    await _conn().execute(
        "UPDATE role_panels SET entries=? WHERE id=?",
        (_json.dumps(entries), panel_id),
    )
    await _conn().commit()
