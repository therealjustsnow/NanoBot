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

async def set_reminder(info: dict) -> None:
    await _conn().execute(
        """INSERT INTO reminders
           (id, target_id, set_by_id, guild_id, channel_id, message, due, duration, dm)
           VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
               due=excluded.due, message=excluded.message""",
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
