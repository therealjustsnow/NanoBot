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
  automod_config    (guild_id) PK  — global automod settings/rules JSON
  automod_badwords  (guild_id, word) PK
  automod_regex_patterns  (id) PK  — per-guild regex patterns for automod
  automod_attachment_words (guild_id, word) PK
  warnings          (id) PK  — one row per warning
  warn_config       (guild_id) PK  — thresholds + DM settings
  welcome_config    (guild_id) PK
  leave_config      (guild_id) PK
  votes             — vote records per user per bot-list site
  recurring_reminders (id) PK  — interval, status, next-fire time
  role_panels       (id) PK
  role_panel_entries — one row per role on a panel
  auditlog_config   (guild_id) PK  — channel, enabled state, event toggles
  music_settings    (guild_id) PK  — 24/7, volume, per-guild music toggles
  music_queue       — persisted per-guild queue (resume on restart)
  music_history     — recently played tracks per guild
  music_autoplaylist (guild_id, ...) — per-guild autoplay seed tracks
  music_song_blocklist  — per-guild blocked songs
  music_user_blocklist  — per-guild blocked requesters
  user_levels       (guild_id, user_id) PK  — message XP totals
  level_config      (guild_id) PK  — per-guild leveling settings
  level_rewards     (guild_id, level) PK  — role granted at a level
  level_ignored_channels (guild_id, channel_id) PK  — channels that earn no XP
  economy           (guild_id, user_id) PK  — coin balance, daily claim state
  economy_config    (guild_id) PK  — per-guild currency name/emoji, daily amount
  gatekeeper_config (guild_id) PK  — new-account mute / verification settings
  gatekeeper_pending (key) PK  — pending unmute + kick schedules ("guild:user")
  birthdays         (guild_id, user_id) PK  — registered birthday + announce marker
  birthday_config   (guild_id) PK  — announce channel, timezone, hour, gif/vc toggles

Note: role_panels_new is a transient table used only during the one-time
role-panel migration (table swap to drop a column); it is not a real table.
"""

import asyncio
import json
import logging
import os
import time
from typing import Any, Awaitable, Callable

import aiosqlite

from utils import db_crypto, sqlite_timing

log = logging.getLogger("NanoBot.db")

# Serializes read-modify-write cycles on the JSON columns of automod_config so
# two concurrent edits can't read the same row and clobber each other's change.
_automod_write_lock = asyncio.Lock()

_DB_PATH = os.path.join("data", "nanobot.db")

# Module-level connection — opened once in init(), shared for the bot's lifetime
_db: aiosqlite.Connection | None = None


async def init(encryption_key: str | None = None) -> None:
    """Open the database and create all tables. Call once at bot startup.

    When `encryption_key` is set the file is opened through SQLCipher
    (encrypted at rest); see utils/db_crypto.py.
    """
    global _db
    os.makedirs("data", exist_ok=True)
    _db = await db_crypto.connect(_DB_PATH, encryption_key)
    # Wrap so every query is timed when slow-query logging is enabled (no-op /
    # zero overhead while the threshold is 0). See utils/sqlite_timing.py.
    _db = sqlite_timing.wrap(_db, "nanobot")

    # ── Connection tuning ─────────────────────────────────────────────────────
    # All cheap, no schema/code impact — they widen write/read throughput so the
    # single shared connection scales to many guilds before a connection pool
    # would ever be worth its complexity.
    #
    #   journal_mode=WAL   readers never block writers, writers never block readers
    #   synchronous=NORMAL safe under WAL (a crash can lose the last txn but never
    #                      corrupts the DB) and skips an fsync per commit — the
    #                      biggest single write-throughput win
    #   busy_timeout=5000  wait up to 5s for a lock instead of erroring out
    #                      immediately ("database is locked"); a safety net if a
    #                      second connection (e.g. migrate.py) ever overlaps
    #   foreign_keys=ON    enforce FK constraints
    #   temp_store=MEMORY  keep temp tables / sort indices in RAM, not on disk
    #   cache_size=-16000  ~16 MB page cache (negative = KiB), fewer disk reads
    #   mmap_size=256 MiB  memory-mapped reads avoid a syscall per page
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA synchronous=NORMAL")
    await _db.execute("PRAGMA busy_timeout=5000")
    await _db.execute("PRAGMA foreign_keys=ON")
    await _db.execute("PRAGMA temp_store=MEMORY")
    await _db.execute("PRAGMA cache_size=-16000")
    await _db.execute("PRAGMA mmap_size=268435456")

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
        CREATE INDEX IF NOT EXISTS reminders_setter ON reminders (set_by_id);
    """)

    await _db.commit()
    await _ensure_warnings_tables()
    await _ensure_welcome_tables()
    await _ensure_votes_table()
    await _ensure_recurring_table()
    await _ensure_role_panels_tables()
    await _migrate_role_panel_entries()
    await _ensure_auditlog_tables()
    await _migrate_auditlog_null_events()
    await _ensure_automod_tables()
    await _ensure_music_tables()
    await _ensure_leveling_tables()
    await _ensure_economy_tables()
    await _ensure_gatekeeper_tables()
    await _ensure_birthday_tables()
    await _ensure_liverole_tables()
    await _run_migrations()
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
#  Schema helpers & versioned migrations
# ══════════════════════════════════════════════════════════════════════════════


async def _ensure_columns(table: str, columns: dict[str, str]) -> None:
    """Idempotently add any missing columns to *table*.

    `columns` maps a column name to its ALTER TABLE ADD COLUMN definition (type
    plus any constraints/default). The existing column list is checked rather
    than swallowing errors, so a locked or corrupt DB surfaces instead of being
    silently skipped.
    """
    async with _conn().execute(f"PRAGMA table_info({table})") as cur:
        existing = {row["name"] for row in await cur.fetchall()}
    added = False
    for col, definition in columns.items():
        if col not in existing:
            await _conn().execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
            added = True
    if added:
        await _conn().commit()


# Ordered, forward-only schema migrations keyed by version. PRAGMA user_version
# records the highest applied migration; on startup any with a higher number run
# in ascending order, and user_version is bumped only after a migration's
# callable succeeds — so a failure leaves the version unchanged and the migration
# is retried on the next start (write each one to be safe to re-run). The CREATE
# TABLE / _ensure_columns calls in init() are the version-0 baseline; add NEW
# schema changes here as @migration(N) instead of scattering ad-hoc ALTERs.
_MIGRATIONS: list[tuple[int, Callable[[aiosqlite.Connection], Awaitable[None]]]] = []


def migration(version: int):
    """Register a schema migration to run when the DB is below *version*."""

    def decorator(fn):
        _MIGRATIONS.append((version, fn))
        return fn

    return decorator


async def _run_migrations(migrations=None) -> None:
    """Apply every registered migration whose version exceeds user_version."""
    migrations = _MIGRATIONS if migrations is None else migrations
    async with _conn().execute("PRAGMA user_version") as cur:
        row = await cur.fetchone()
    current = row[0] if row else 0

    for version, fn in sorted(migrations):
        if version <= current:
            continue
        log.info("Applying DB migration %d (%s)", version, fn.__name__)
        try:
            await fn(_conn())
            # PRAGMA doesn't accept bound params; version is an int we control.
            await _conn().execute(f"PRAGMA user_version = {int(version)}")
            await _conn().commit()
        except Exception:
            await _conn().rollback()
            log.error("DB migration %d failed — rolled back", version)
            raise


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
    return {
        r["name"]: {"content": r["content"], "image_url": r["image_url"]} for r in rows
    }


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
            "content": r["content"],
            "image_url": r["image_url"],
            "by_id": r["by_id"],
            "by_name": r["by_name"],
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
    guild_id: int,
    scope: str,  # "global" or str(user_id)
    name: str,
    content: str | None,
    image_url: str | None,
    by_id: str | None = None,
    by_name: str | None = None,
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


async def update_tag_image(
    guild_id: int, scope: str, name: str, image_url: str | None
) -> None:
    await _conn().execute(
        "UPDATE tags SET image_url=? WHERE guild_id=? AND scope=? AND name=?",
        (image_url, str(guild_id), scope, name),
    )
    await _conn().commit()


async def update_tag_content(
    guild_id: int, scope: str, name: str, content: str
) -> None:
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
    guild_id: int,
    user_id: int,
    content: str,
    by_id: str,
    by_name: str,
    created_at: str,
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
    return [
        {
            "note": r["content"],
            "by_id": r["by_id"],
            "by_name": r["by_name"],
            "at": r["created_at"],
        }
        for r in rows
    ]


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
    return {
        r["key"]: {
            "guild_id": r["guild_id"],
            "user_id": r["user_id"],
            "until": r["until"],
        }
        for r in rows
    }


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
    return {
        r["channel_id"]: {"guild_id": r["guild_id"], "until": r["until"]} for r in rows
    }


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
            info["id"],
            info["target_id"],
            info["set_by_id"],
            info["guild_id"],
            info["channel_id"],
            info["message"],
            info["due"],
            info.get("duration", 0),
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


async def get_sent_reminders(user_id: int) -> dict:
    """Reminders this user set for OTHER people (set_by = user, target != user)."""
    async with _conn().execute(
        "SELECT id, target_id, set_by_id, guild_id, channel_id, message, due, duration, dm "
        "FROM reminders WHERE set_by_id=? AND target_id!=?",
        (str(user_id), str(user_id)),
    ) as cur:
        rows = await cur.fetchall()
    return {r["id"]: _reminder_row(r) for r in rows}


def _reminder_row(r: aiosqlite.Row) -> dict:
    return {
        "id": r["id"],
        "target_id": r["target_id"],
        "set_by_id": r["set_by_id"],
        "guild_id": r["guild_id"],
        "channel_id": r["channel_id"],
        "message": r["message"],
        "due": r["due"],
        "duration": r["duration"],
        "dm": bool(r["dm"]),
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
    guild_id: int,
    user_id: int,
    reason: str,
    by_id: str,
    by_name: str,
    created_at: str,
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
    return [
        {
            "id": r["id"],
            "reason": r["reason"],
            "by_name": r["by_name"],
            "at": r["created_at"],
        }
        for r in rows
    ]


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
        return {
            "kick_at": row["kick_at"],
            "ban_at": row["ban_at"],
            "dm_user": bool(row["dm_user"]),
        }
    return {"kick_at": 0, "ban_at": 0, "dm_user": True}


async def set_warn_config(
    guild_id: int, kick_at: int, ban_at: int, dm_user: bool
) -> None:
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
            guild_id    TEXT PRIMARY KEY,
            enabled     INTEGER NOT NULL DEFAULT 0,
            channel_id  TEXT,
            title       TEXT,
            content     TEXT,
            image_url   TEXT,
            dm          INTEGER NOT NULL DEFAULT 0,
            footer_text TEXT,
            thumbnail   TEXT,
            color       TEXT,
            image_text  TEXT
        )
    """)
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS leave_config (
            guild_id    TEXT PRIMARY KEY,
            enabled     INTEGER NOT NULL DEFAULT 0,
            channel_id  TEXT,
            title       TEXT,
            content     TEXT,
            image_url   TEXT,
            dm          INTEGER NOT NULL DEFAULT 0,
            footer_text TEXT,
            thumbnail   TEXT,
            color       TEXT,
            image_text  TEXT
        )
    """)
    await _conn().commit()

    # Migration: add new columns to existing tables that pre-date this change.
    new_columns = {
        "footer_text": "TEXT",
        "thumbnail": "TEXT",
        "color": "TEXT",
        "image_text": "TEXT",
    }
    for table in ("welcome_config", "leave_config"):
        await _ensure_columns(table, new_columns)


async def _get_event_config(table: str, guild_id: int) -> dict | None:
    async with _conn().execute(
        f"SELECT enabled, channel_id, title, content, image_url, dm, "
        f"footer_text, thumbnail, color, image_text FROM {table} WHERE guild_id=?",
        (str(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    return {
        "enabled": bool(row["enabled"]),
        "channel_id": row["channel_id"],
        "title": row["title"],
        "content": row["content"],
        "image_url": row["image_url"],
        "dm": bool(row["dm"]),
        "footer_text": row["footer_text"],
        "thumbnail": row["thumbnail"],
        "color": row["color"],
        "image_text": row["image_text"],
    }


async def _set_event_config(table: str, guild_id: int, **kwargs) -> None:
    await _conn().execute(
        f"INSERT INTO {table} "
        "(guild_id, enabled, channel_id, title, content, image_url, dm, "
        "footer_text, thumbnail, color, image_text) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(guild_id) DO UPDATE SET "
        "enabled=excluded.enabled, channel_id=excluded.channel_id, "
        "title=excluded.title, content=excluded.content, "
        "image_url=excluded.image_url, dm=excluded.dm, "
        "footer_text=excluded.footer_text, thumbnail=excluded.thumbnail, "
        "color=excluded.color, image_text=excluded.image_text",
        (
            str(guild_id),
            1 if kwargs.get("enabled", False) else 0,
            kwargs.get("channel_id"),
            kwargs.get("title"),
            kwargs.get("content"),
            kwargs.get("image_url"),
            1 if kwargs.get("dm", False) else 0,
            kwargs.get("footer_text"),
            kwargs.get("thumbnail"),
            kwargs.get("color"),
            kwargs.get("image_text"),
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


# ══════════════════════════════════════════════════════════════════════════════
#  Recurring Reminders
# ══════════════════════════════════════════════════════════════════════════════


async def _ensure_recurring_table():
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS recurring_reminders (
            id          TEXT PRIMARY KEY,
            target_id   TEXT NOT NULL,
            set_by_id   TEXT NOT NULL,
            guild_id    TEXT NOT NULL,
            channel_id  TEXT NOT NULL,
            message     TEXT NOT NULL,
            interval    REAL NOT NULL,
            next_due    REAL NOT NULL,
            dm          INTEGER NOT NULL DEFAULT 1,
            paused      INTEGER NOT NULL DEFAULT 0,
            fire_count  INTEGER NOT NULL DEFAULT 0,
            label       TEXT
        )
    """)
    await _conn().execute(
        "CREATE INDEX IF NOT EXISTS recurring_target "
        "ON recurring_reminders (target_id)"
    )
    await _conn().commit()


async def recurring_id_exists(rid: str) -> bool:
    async with _conn().execute(
        "SELECT 1 FROM recurring_reminders WHERE id=? LIMIT 1", (rid,)
    ) as cur:
        return await cur.fetchone() is not None


async def set_recurring(info: dict) -> None:
    """Insert a new recurring reminder. Ignores duplicates (idempotent)."""
    await _conn().execute(
        """INSERT OR IGNORE INTO recurring_reminders
           (id, target_id, set_by_id, guild_id, channel_id, message,
            interval, next_due, dm, paused, fire_count, label)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            info["id"],
            info["target_id"],
            info["set_by_id"],
            info["guild_id"],
            info["channel_id"],
            info["message"],
            info["interval"],
            info["next_due"],
            1 if info.get("dm", True) else 0,
            1 if info.get("paused", False) else 0,
            info.get("fire_count", 0),
            info.get("label"),
        ),
    )
    await _conn().commit()


async def update_recurring(info: dict) -> None:
    """Update mutable fields — next_due, fire_count, and paused — after each fire."""
    await _conn().execute(
        """UPDATE recurring_reminders
           SET next_due=?, fire_count=?, paused=?
           WHERE id=?""",
        (
            info["next_due"],
            info.get("fire_count", 0),
            1 if info.get("paused", False) else 0,
            info["id"],
        ),
    )
    await _conn().commit()


async def set_recurring_paused(rid: str, paused: bool) -> None:
    """Flip the paused flag only — used by pause/resume commands."""
    await _conn().execute(
        "UPDATE recurring_reminders SET paused=? WHERE id=?",
        (1 if paused else 0, rid),
    )
    await _conn().commit()


async def remove_recurring(rid: str) -> None:
    """Permanently delete a recurring reminder."""
    await _conn().execute("DELETE FROM recurring_reminders WHERE id=?", (rid,))
    await _conn().commit()


async def get_recurring(rid: str) -> dict | None:
    """Fetch a single recurring reminder by ID. Returns None if not found."""
    async with _conn().execute(
        "SELECT id, target_id, set_by_id, guild_id, channel_id, message, "
        "interval, next_due, dm, paused, fire_count, label "
        "FROM recurring_reminders WHERE id=? LIMIT 1",
        (rid,),
    ) as cur:
        row = await cur.fetchone()
    return _recurring_row(row) if row else None


async def get_user_recurring(user_id: int) -> list[dict]:
    """All recurring reminders for a user, ordered by next_due ascending."""
    async with _conn().execute(
        "SELECT id, target_id, set_by_id, guild_id, channel_id, message, "
        "interval, next_due, dm, paused, fire_count, label "
        "FROM recurring_reminders WHERE target_id=? ORDER BY next_due ASC",
        (str(user_id),),
    ) as cur:
        rows = await cur.fetchall()
    return [_recurring_row(r) for r in rows]


async def get_all_recurring() -> dict:
    """Returns {id: info} for every recurring reminder — used on bot restore."""
    async with _conn().execute(
        "SELECT id, target_id, set_by_id, guild_id, channel_id, message, "
        "interval, next_due, dm, paused, fire_count, label "
        "FROM recurring_reminders"
    ) as cur:
        rows = await cur.fetchall()
    return {r["id"]: _recurring_row(r) for r in rows}


async def count_user_recurring(user_id: int) -> int:
    async with _conn().execute(
        "SELECT COUNT(*) FROM recurring_reminders WHERE target_id=?",
        (str(user_id),),
    ) as cur:
        row = await cur.fetchone()
    return row[0]


def _recurring_row(r: aiosqlite.Row) -> dict:
    return {
        "id": r["id"],
        "target_id": r["target_id"],
        "set_by_id": r["set_by_id"],
        "guild_id": r["guild_id"],
        "channel_id": r["channel_id"],
        "message": r["message"],
        "interval": r["interval"],
        "next_due": r["next_due"],
        "dm": bool(r["dm"]),
        "paused": bool(r["paused"]),
        "fire_count": r["fire_count"],
        "label": r["label"],
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Role Panels
# ══════════════════════════════════════════════════════════════════════════════


async def _ensure_role_panels_tables() -> None:
    await _conn().executescript("""
        CREATE TABLE IF NOT EXISTS role_panels (
            id          TEXT PRIMARY KEY,
            guild_id    TEXT NOT NULL,
            title       TEXT NOT NULL,
            description TEXT,
            mode        TEXT NOT NULL DEFAULT 'toggle',
            channel_id  TEXT,
            message_id  TEXT
        );
        CREATE INDEX IF NOT EXISTS role_panels_guild ON role_panels (guild_id);

        CREATE TABLE IF NOT EXISTS role_panel_entries (
            panel_id    TEXT NOT NULL REFERENCES role_panels(id) ON DELETE CASCADE,
            role_id     INTEGER NOT NULL,
            label       TEXT NOT NULL,
            emoji       TEXT,
            style       TEXT NOT NULL DEFAULT 'secondary',
            position    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (panel_id, role_id)
        );
        CREATE INDEX IF NOT EXISTS rpe_panel ON role_panel_entries (panel_id);
    """)
    await _conn().commit()


async def _migrate_role_panel_entries() -> None:
    """
    One-time migration: older versions stored entries as a JSON blob in a
    'entries' column on the role_panels table. This reads that column (if it
    still exists) and populates role_panel_entries, then drops the old column
    via a table rebuild.

    Safe to run on every startup — exits immediately if no migration is needed.
    """
    # Check whether the old 'entries' column exists on role_panels
    async with _conn().execute("PRAGMA table_info(role_panels)") as cur:
        columns = {row["name"] for row in await cur.fetchall()}

    if "entries" not in columns:
        return  # Already migrated or fresh install — nothing to do

    log.info(
        "DB migration: migrating role_panel entries from JSON column to relational table"
    )

    async with _conn().execute(
        "SELECT id, entries FROM role_panels WHERE entries IS NOT NULL AND entries != ''"
    ) as cur:
        rows = await cur.fetchall()

    migrated_panels = 0
    migrated_entries = 0

    for row in rows:
        panel_id = row["id"]
        try:
            entries = json.loads(row["entries"])
        except (json.JSONDecodeError, TypeError):
            log.warning(
                f"DB migration: could not parse entries for panel {panel_id!r} — skipping"
            )
            continue

        for i, entry in enumerate(entries):
            role_id = entry.get("role_id")
            if not role_id:
                continue
            await _conn().execute(
                "INSERT OR IGNORE INTO role_panel_entries "
                "(panel_id, role_id, label, emoji, style, position) VALUES (?,?,?,?,?,?)",
                (
                    panel_id,
                    int(role_id),
                    entry.get("label") or "Role",
                    entry.get("emoji"),
                    entry.get("style", "secondary"),
                    i,
                ),
            )
            migrated_entries += 1
        migrated_panels += 1

    await _conn().commit()

    # Rebuild role_panels without the 'entries' column.
    # SQLite < 3.35 doesn't support DROP COLUMN, so we do a table swap.
    await _conn().executescript("""
        CREATE TABLE IF NOT EXISTS role_panels_new (
            id          TEXT PRIMARY KEY,
            guild_id    TEXT NOT NULL,
            title       TEXT NOT NULL,
            description TEXT,
            mode        TEXT NOT NULL DEFAULT 'toggle',
            channel_id  TEXT,
            message_id  TEXT
        );
        INSERT OR IGNORE INTO role_panels_new
            (id, guild_id, title, description, mode, channel_id, message_id)
        SELECT id, guild_id, title, description, mode, channel_id, message_id
        FROM role_panels;
        DROP TABLE role_panels;
        ALTER TABLE role_panels_new RENAME TO role_panels;
        CREATE INDEX IF NOT EXISTS role_panels_guild ON role_panels (guild_id);
    """)
    await _conn().commit()

    log.info(
        f"DB migration complete: migrated {migrated_entries} entries across "
        f"{migrated_panels} panel(s). Old 'entries' column removed."
    )


def _panel_row(panel: aiosqlite.Row, entries: list[aiosqlite.Row]) -> dict:
    return {
        "id": panel["id"],
        "guild_id": panel["guild_id"],
        "title": panel["title"],
        "description": panel["description"],
        "mode": panel["mode"],
        "channel_id": panel["channel_id"],
        "message_id": panel["message_id"],
        "entries": [
            {
                "role_id": e["role_id"],
                "label": e["label"],
                "emoji": e["emoji"],
                "style": e["style"],
            }
            for e in sorted(entries, key=lambda x: x["position"])
        ],
    }


async def _fetch_entries(panel_id: str) -> list[aiosqlite.Row]:
    async with _conn().execute(
        "SELECT role_id, label, emoji, style, position "
        "FROM role_panel_entries WHERE panel_id=? ORDER BY position ASC",
        (panel_id,),
    ) as cur:
        return await cur.fetchall()


async def create_role_panel(
    panel_id: str,
    guild_id: int,
    title: str,
    description: str | None,
    mode: str,
) -> None:
    """Insert a new panel with no entries."""
    await _conn().execute(
        "INSERT INTO role_panels (id, guild_id, title, description, mode) "
        "VALUES (?,?,?,?,?)",
        (panel_id, str(guild_id), title, description, mode),
    )
    await _conn().commit()


async def get_role_panel(panel_id: str) -> dict | None:
    """Return a single panel (with entries) or None."""
    async with _conn().execute(
        "SELECT id, guild_id, title, description, mode, channel_id, message_id "
        "FROM role_panels WHERE id=? LIMIT 1",
        (panel_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    entries = await _fetch_entries(panel_id)
    return _panel_row(row, entries)


async def get_role_panels_for_guild(guild_id: int) -> list[dict]:
    """All panels for a guild, ordered by rowid (creation order)."""
    async with _conn().execute(
        "SELECT id, guild_id, title, description, mode, channel_id, message_id "
        "FROM role_panels WHERE guild_id=? ORDER BY rowid ASC",
        (str(guild_id),),
    ) as cur:
        rows = await cur.fetchall()
    panels = []
    for row in rows:
        entries = await _fetch_entries(row["id"])
        panels.append(_panel_row(row, entries))
    return panels


async def get_all_role_panels() -> list[dict]:
    """All panels across every guild — used on startup to restore persistent views."""
    async with _conn().execute(
        "SELECT id, guild_id, title, description, mode, channel_id, message_id "
        "FROM role_panels ORDER BY rowid ASC"
    ) as cur:
        rows = await cur.fetchall()
    panels = []
    for row in rows:
        entries = await _fetch_entries(row["id"])
        panels.append(_panel_row(row, entries))
    return panels


async def edit_role_panel(
    panel_id: str,
    title: str,
    description: str | None,
    mode: str,
) -> None:
    """Update title, description, and mode on an existing panel."""
    await _conn().execute(
        "UPDATE role_panels SET title=?, description=?, mode=? WHERE id=?",
        (title, description, mode, panel_id),
    )
    await _conn().commit()


async def update_role_panel_message(
    panel_id: str, channel_id: int, message_id: int
) -> None:
    """Record where the panel message was posted."""
    await _conn().execute(
        "UPDATE role_panels SET channel_id=?, message_id=? WHERE id=?",
        (str(channel_id), str(message_id), panel_id),
    )
    await _conn().commit()


async def delete_role_panel(panel_id: str) -> None:
    """Delete a panel and all its entries (CASCADE handles entries)."""
    await _conn().execute("DELETE FROM role_panels WHERE id=?", (panel_id,))
    await _conn().commit()


async def add_role_to_panel(panel_id: str, entry: dict) -> None:
    """
    Append a role entry to a panel.
    entry must have: role_id, label, emoji (or None), style.
    Position is set to max(existing) + 1 so ordering is stable.
    """
    async with _conn().execute(
        "SELECT COALESCE(MAX(position), -1) FROM role_panel_entries WHERE panel_id=?",
        (panel_id,),
    ) as cur:
        row = await cur.fetchone()
    next_pos = (row[0] + 1) if row else 0

    await _conn().execute(
        "INSERT INTO role_panel_entries (panel_id, role_id, label, emoji, style, position) "
        "VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(panel_id, role_id) DO UPDATE SET "
        "label=excluded.label, emoji=excluded.emoji, style=excluded.style",
        (
            panel_id,
            entry["role_id"],
            entry.get("label") or "Role",
            entry.get("emoji"),
            entry.get("style", "secondary"),
            next_pos,
        ),
    )
    await _conn().commit()


async def remove_role_from_panel(panel_id: str, role_id: int) -> None:
    """Remove a single role entry from a panel."""
    await _conn().execute(
        "DELETE FROM role_panel_entries WHERE panel_id=? AND role_id=?",
        (panel_id, role_id),
    )
    await _conn().commit()


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
    await _conn().commit()


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
    await _conn().commit()
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
    await _conn().commit()


async def set_auditlog_enabled(guild_id: int, enabled: bool) -> None:
    """Enable or disable audit logging for a guild. Creates the row if absent."""
    await _conn().execute(
        """INSERT INTO auditlog_config (guild_id, enabled, events)
           VALUES (?, ?, ?)
           ON CONFLICT(guild_id) DO UPDATE SET enabled=excluded.enabled""",
        (str(guild_id), 1 if enabled else 0, _AUDIT_ALL_EVENTS_JSON),
    )
    await _conn().commit()


async def set_auditlog_events(guild_id: int, events: set[str]) -> None:
    """Replace the full set of enabled events for a guild. Creates row if absent."""
    await _conn().execute(
        """INSERT INTO auditlog_config (guild_id, events)
           VALUES (?, ?)
           ON CONFLICT(guild_id) DO UPDATE SET events=excluded.events""",
        (str(guild_id), json.dumps(sorted(events))),
    )
    await _conn().commit()


# ══════════════════════════════════════════════════════════════════════════════
#  AutoMod
# ══════════════════════════════════════════════════════════════════════════════
# `rules` is a JSON object:  {rule_key: {enabled, action, ...rule-specific fields}}
# `ignore_channels` and `ignore_roles` are JSON arrays of ID strings.
# Bad words live in a separate table for clean add / remove / list ops.


async def _ensure_automod_tables() -> None:
    await _conn().executescript("""
        CREATE TABLE IF NOT EXISTS automod_config (
            guild_id         TEXT PRIMARY KEY,
            enabled          INTEGER NOT NULL DEFAULT 0,
            rules            TEXT NOT NULL DEFAULT '{}',
            ignore_channels  TEXT NOT NULL DEFAULT '[]',
            ignore_roles     TEXT NOT NULL DEFAULT '[]',
            timeout_seconds  INTEGER NOT NULL DEFAULT 600
        );

        CREATE TABLE IF NOT EXISTS automod_badwords (
            guild_id  TEXT NOT NULL,
            word      TEXT NOT NULL,
            PRIMARY KEY (guild_id, word)
        );
        CREATE INDEX IF NOT EXISTS abw_guild ON automod_badwords (guild_id);

        CREATE TABLE IF NOT EXISTS automod_regex_patterns (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id  TEXT NOT NULL,
            pattern   TEXT NOT NULL,
            label     TEXT,
            UNIQUE(guild_id, pattern)
        );
        CREATE INDEX IF NOT EXISTS automod_regex_guild ON automod_regex_patterns (guild_id);

        CREATE TABLE IF NOT EXISTS automod_attachment_words (
            guild_id  TEXT NOT NULL,
            word      TEXT NOT NULL,
            PRIMARY KEY (guild_id, word)
        );
        CREATE INDEX IF NOT EXISTS aaw_guild ON automod_attachment_words (guild_id);
    """)
    await _conn().commit()
    # Add columns for older schemas.
    await _ensure_columns(
        "automod_config", {"timeout_seconds": "INTEGER NOT NULL DEFAULT 600"}
    )
    await _ensure_columns("automod_config", {"log_channel_id": "TEXT"})


def _automod_row(row: aiosqlite.Row) -> dict:
    return {
        "enabled": bool(row["enabled"]),
        "rules": json.loads(row["rules"]),
        "ignore_channels": json.loads(row["ignore_channels"]),
        "ignore_roles": json.loads(row["ignore_roles"]),
        "timeout_seconds": row["timeout_seconds"],
        "log_channel_id": row["log_channel_id"],
    }


async def _ensure_automod_guild(guild_id: int) -> None:
    """Insert a default automod row for a guild if one doesn't exist yet."""
    await _conn().execute(
        "INSERT OR IGNORE INTO automod_config (guild_id) VALUES (?)",
        (str(guild_id),),
    )
    await _conn().commit()


async def get_automod_config(guild_id: int) -> dict | None:
    """Return the full automod config for a guild, or None if not yet set up."""
    async with _conn().execute(
        "SELECT enabled, rules, ignore_channels, ignore_roles, timeout_seconds, log_channel_id "
        "FROM automod_config WHERE guild_id=? LIMIT 1",
        (str(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    return _automod_row(row) if row else None


async def set_automod_enabled(guild_id: int, enabled: bool) -> None:
    """Enable or disable automod for a guild. Creates the row if absent."""
    await _conn().execute(
        """INSERT INTO automod_config (guild_id, enabled)
           VALUES (?, ?)
           ON CONFLICT(guild_id) DO UPDATE SET enabled=excluded.enabled""",
        (str(guild_id), 1 if enabled else 0),
    )
    await _conn().commit()


async def set_automod_timeout_seconds(guild_id: int, seconds: int) -> None:
    """Set the timeout duration (in seconds) applied by the automod timeout action."""
    await _conn().execute(
        """INSERT INTO automod_config (guild_id, timeout_seconds)
           VALUES (?, ?)
           ON CONFLICT(guild_id) DO UPDATE SET timeout_seconds=excluded.timeout_seconds""",
        (str(guild_id), seconds),
    )
    await _conn().commit()


async def set_automod_log_channel(guild_id: int, channel_id: int | None) -> None:
    """Set (or clear) the dedicated automod log channel. Pass None to revert to fallback."""
    await _ensure_automod_guild(guild_id)
    await _conn().execute(
        "UPDATE automod_config SET log_channel_id=? WHERE guild_id=?",
        (str(channel_id) if channel_id is not None else None, str(guild_id)),
    )
    await _conn().commit()


async def set_automod_rule(guild_id: int, rule: str, **kwargs: Any) -> None:
    """
    Merge kwargs into the rule dict for `rule`.  Existing keys not in kwargs
    are preserved.  Creates the guild row if absent.

    Examples
    --------
    set_automod_rule(gid, "spam", enabled=True, action="warn", count=5, seconds=5)
    set_automod_rule(gid, "invites", action="softban", dm_message="Please read the server rules.")
    set_automod_rule(gid, "caps", percent=70, min_length=10)
    set_automod_rule(gid, "mentions", limit=5)
    """
    await _ensure_automod_guild(guild_id)

    async with _automod_write_lock:
        async with _conn().execute(
            "SELECT rules FROM automod_config WHERE guild_id=? LIMIT 1",
            (str(guild_id),),
        ) as cur:
            row = await cur.fetchone()

        rules: dict = json.loads(row["rules"]) if row else {}
        existing = rules.get(rule, {})
        existing.update(kwargs)
        rules[rule] = existing

        await _conn().execute(
            "UPDATE automod_config SET rules=? WHERE guild_id=?",
            (json.dumps(rules), str(guild_id)),
        )
        await _conn().commit()


async def add_automod_badword(guild_id: int, word: str) -> bool:
    """Add a word to the filter. Returns True if added, False if already present."""
    try:
        await _conn().execute(
            "INSERT INTO automod_badwords (guild_id, word) VALUES (?, ?)",
            (str(guild_id), word.lower().strip()),
        )
        await _conn().commit()
        return True
    except db_crypto.INTEGRITY_ERRORS:
        return False


async def remove_automod_badword(guild_id: int, word: str) -> bool:
    """Remove a word from the filter. Returns True if removed, False if not found."""
    cur = await _conn().execute(
        "DELETE FROM automod_badwords WHERE guild_id=? AND word=?",
        (str(guild_id), word.lower().strip()),
    )
    await _conn().commit()
    return cur.rowcount > 0


async def get_automod_badwords(guild_id: int) -> list[str]:
    """Return all bad words for a guild, sorted alphabetically."""
    async with _conn().execute(
        "SELECT word FROM automod_badwords WHERE guild_id=? ORDER BY word ASC",
        (str(guild_id),),
    ) as cur:
        rows = await cur.fetchall()
    return [r["word"] for r in rows]


async def toggle_automod_ignore(guild_id: int, kind: str, target_id: int) -> str:
    """
    Toggle a channel or role exemption.
    kind = "channel" | "role"
    Returns "added" or "removed".
    """
    await _ensure_automod_guild(guild_id)

    col = "ignore_channels" if kind == "channel" else "ignore_roles"
    tid = str(target_id)

    async with _automod_write_lock:
        async with _conn().execute(
            f"SELECT {col} FROM automod_config WHERE guild_id=? LIMIT 1",
            (str(guild_id),),
        ) as cur:
            row = await cur.fetchone()

        ids: list[str] = json.loads(row[col]) if row else []

        if tid in ids:
            ids.remove(tid)
            result = "removed"
        else:
            ids.append(tid)
            result = "added"

        await _conn().execute(
            f"UPDATE automod_config SET {col}=? WHERE guild_id=?",
            (json.dumps(ids), str(guild_id)),
        )
        await _conn().commit()
    return result


# ══════════════════════════════════════════════════════════════════════════════
#  AutoMod — Regex Patterns
# ══════════════════════════════════════════════════════════════════════════════


async def add_automod_regex(
    guild_id: int, pattern: str, label: str | None = None
) -> bool:
    """Add a regex pattern to the filter. Returns True if added, False if already present."""
    try:
        await _conn().execute(
            "INSERT INTO automod_regex_patterns (guild_id, pattern, label) VALUES (?,?,?)",
            (str(guild_id), pattern, label),
        )
        await _conn().commit()
        return True
    except db_crypto.INTEGRITY_ERRORS:
        return False


async def remove_automod_regex(guild_id: int, pattern: str) -> bool:
    """Remove a regex pattern by its exact pattern string. Returns True if removed."""
    cur = await _conn().execute(
        "DELETE FROM automod_regex_patterns WHERE guild_id=? AND pattern=?",
        (str(guild_id), pattern),
    )
    await _conn().commit()
    return cur.rowcount > 0


async def get_automod_regex_patterns(guild_id: int) -> list[dict]:
    """Return all patterns for a guild as list of {id, pattern, label}, ordered by id."""
    async with _conn().execute(
        "SELECT id, pattern, label FROM automod_regex_patterns "
        "WHERE guild_id=? ORDER BY id ASC",
        (str(guild_id),),
    ) as cur:
        rows = await cur.fetchall()
    return [{"id": r["id"], "pattern": r["pattern"], "label": r["label"]} for r in rows]


async def add_automod_attachment_word(guild_id: int, word: str) -> bool:
    """Add a word to the attachment-word filter. Returns False if already present."""
    try:
        await _conn().execute(
            "INSERT INTO automod_attachment_words (guild_id, word) VALUES (?, ?)",
            (str(guild_id), word),
        )
        await _conn().commit()
        return True
    except db_crypto.INTEGRITY_ERRORS:
        return False


async def remove_automod_attachment_word(guild_id: int, word: str) -> bool:
    """Remove a word from the attachment-word filter. Returns False if not found."""
    async with _conn().execute(
        "DELETE FROM automod_attachment_words WHERE guild_id=? AND word=?",
        (str(guild_id), word),
    ) as cur:
        changed = cur.rowcount
    await _conn().commit()
    return changed > 0


async def get_automod_attachment_words(guild_id: int) -> list[str]:
    """Return all attachment-trigger words for a guild, sorted alphabetically."""
    async with _conn().execute(
        "SELECT word FROM automod_attachment_words WHERE guild_id=? ORDER BY word ASC",
        (str(guild_id),),
    ) as cur:
        rows = await cur.fetchall()
    return [r["word"] for r in rows]


# ══════════════════════════════════════════════════════════════════════════════
#  Music — persistent per-guild autoplaylist
# ══════════════════════════════════════════════════════════════════════════════
async def _ensure_music_tables() -> None:
    await _conn().executescript("""
        CREATE TABLE IF NOT EXISTS music_autoplaylist (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id  TEXT NOT NULL,
            url       TEXT NOT NULL,
            title     TEXT,
            added_by  TEXT,
            added_at  INTEGER NOT NULL,
            UNIQUE(guild_id, url)
        );
        CREATE INDEX IF NOT EXISTS music_apl_guild
            ON music_autoplaylist (guild_id);
        CREATE TABLE IF NOT EXISTS music_settings (
            guild_id          TEXT PRIMARY KEY,
            stay_connected    INTEGER NOT NULL DEFAULT 0,
            voice_channel_id  TEXT,
            text_channel_id   TEXT,
            loop_mode         TEXT
        );
        CREATE TABLE IF NOT EXISTS music_queue (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id       TEXT NOT NULL,
            position       INTEGER NOT NULL,
            is_current     INTEGER NOT NULL DEFAULT 0,
            query          TEXT NOT NULL,
            title          TEXT,
            duration       INTEGER,
            webpage_url    TEXT,
            thumbnail      TEXT,
            uploader       TEXT,
            requester_id   TEXT,
            requester_name TEXT
        );
        CREATE INDEX IF NOT EXISTS music_queue_guild
            ON music_queue (guild_id);
        CREATE TABLE IF NOT EXISTS music_song_blocklist (
            guild_id  TEXT NOT NULL,
            pattern   TEXT NOT NULL,
            added_by  TEXT,
            PRIMARY KEY (guild_id, pattern)
        );
        CREATE INDEX IF NOT EXISTS music_songblock_guild
            ON music_song_blocklist (guild_id);
        CREATE TABLE IF NOT EXISTS music_user_blocklist (
            guild_id  TEXT NOT NULL,
            user_id   TEXT NOT NULL,
            added_by  TEXT,
            PRIMARY KEY (guild_id, user_id)
        );
        CREATE INDEX IF NOT EXISTS music_userblock_guild
            ON music_user_blocklist (guild_id);
        CREATE TABLE IF NOT EXISTS music_history (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id      TEXT NOT NULL,
            title         TEXT,
            url           TEXT,
            requester_id  TEXT,
            played_at     INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS music_history_guild
            ON music_history (guild_id, id);
    """)
    await _conn().commit()

    # Migration: add columns to music_settings rows that pre-date persistence.
    await _ensure_columns(
        "music_settings",
        {"voice_channel_id": "TEXT", "text_channel_id": "TEXT", "loop_mode": "TEXT"},
    )


async def get_music_stay(guild_id: int) -> bool:
    """Whether 24/7 mode is on — bot stays in voice even when the channel empties."""
    async with _conn().execute(
        "SELECT stay_connected FROM music_settings WHERE guild_id=?",
        (str(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    return bool(row and row[0])


async def set_music_stay(guild_id: int, value: bool) -> None:
    await _conn().execute(
        "INSERT INTO music_settings (guild_id, stay_connected) VALUES (?, ?) "
        "ON CONFLICT(guild_id) DO UPDATE SET stay_connected=excluded.stay_connected",
        (str(guild_id), 1 if value else 0),
    )
    await _conn().commit()


async def add_autoplaylist_entry(
    guild_id: int, url: str, title: str | None, added_by: int | None
) -> bool:
    """Add a track URL to a guild's autoplaylist. Returns False if already present."""
    try:
        await _conn().execute(
            "INSERT INTO music_autoplaylist (guild_id, url, title, added_by, added_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                str(guild_id),
                url,
                title,
                str(added_by) if added_by else None,
                int(time.time()),
            ),
        )
        await _conn().commit()
        return True
    except db_crypto.INTEGRITY_ERRORS:
        return False


async def remove_autoplaylist_entry(guild_id: int, url: str) -> bool:
    """Remove a track URL from a guild's autoplaylist. Returns False if not found."""
    async with _conn().execute(
        "DELETE FROM music_autoplaylist WHERE guild_id=? AND url=?",
        (str(guild_id), url),
    ) as cur:
        changed = cur.rowcount
    await _conn().commit()
    return changed > 0


async def get_autoplaylist(guild_id: int) -> list[dict]:
    """Return all autoplaylist entries for a guild, oldest first."""
    async with _conn().execute(
        "SELECT url, title, added_by FROM music_autoplaylist "
        "WHERE guild_id=? ORDER BY added_at ASC",
        (str(guild_id),),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {"url": r["url"], "title": r["title"], "added_by": r["added_by"]} for r in rows
    ]


async def clear_autoplaylist(guild_id: int) -> int:
    """Delete every autoplaylist entry for a guild. Returns the number removed."""
    async with _conn().execute(
        "DELETE FROM music_autoplaylist WHERE guild_id=?",
        (str(guild_id),),
    ) as cur:
        changed = cur.rowcount
    await _conn().commit()
    return changed


# ── Persistent queue (survives restart when music_persist_queue is on) ──────────
_QUEUE_FIELDS = (
    "query",
    "title",
    "duration",
    "webpage_url",
    "thumbnail",
    "uploader",
    "requester_id",
    "requester_name",
)


def _queue_row(r: aiosqlite.Row) -> dict:
    return {f: r[f] for f in _QUEUE_FIELDS}


async def save_music_queue(
    guild_id: int,
    current: dict | None,
    queue: list[dict],
    voice_channel_id: int | None,
    text_channel_id: int | None,
    loop_mode: str | None = None,
) -> None:
    """Replace a guild's persisted queue snapshot and its resume channels.

    `current` and each item in `queue` are dicts with the _QUEUE_FIELDS keys.
    """
    gid = str(guild_id)
    await _conn().execute("DELETE FROM music_queue WHERE guild_id=?", (gid,))

    rows = []
    if current is not None:
        rows.append((-1, 1, current))
    for pos, track in enumerate(queue):
        rows.append((pos, 0, track))

    if rows:
        await _conn().executemany(
            "INSERT INTO music_queue "
            "(guild_id, position, is_current, query, title, duration, webpage_url, "
            "thumbnail, uploader, requester_id, requester_name) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    gid,
                    pos,
                    is_cur,
                    t.get("query"),
                    t.get("title"),
                    t.get("duration"),
                    t.get("webpage_url"),
                    t.get("thumbnail"),
                    t.get("uploader"),
                    t.get("requester_id"),
                    t.get("requester_name"),
                )
                for pos, is_cur, t in rows
            ],
        )

    await _conn().execute(
        "INSERT INTO music_settings (guild_id, voice_channel_id, text_channel_id, loop_mode) "
        "VALUES (?,?,?,?) "
        "ON CONFLICT(guild_id) DO UPDATE SET "
        "voice_channel_id=excluded.voice_channel_id, "
        "text_channel_id=excluded.text_channel_id, "
        "loop_mode=excluded.loop_mode",
        (
            gid,
            str(voice_channel_id) if voice_channel_id else None,
            str(text_channel_id) if text_channel_id else None,
            loop_mode,
        ),
    )
    await _conn().commit()


async def clear_music_queue(guild_id: int) -> None:
    """Drop a guild's persisted queue and forget its resume channels."""
    gid = str(guild_id)
    await _conn().execute("DELETE FROM music_queue WHERE guild_id=?", (gid,))
    await _conn().execute(
        "UPDATE music_settings SET voice_channel_id=NULL, text_channel_id=NULL, "
        "loop_mode=NULL WHERE guild_id=?",
        (gid,),
    )
    await _conn().commit()


async def get_all_persisted_queues() -> list[dict]:
    """Return every guild with a saved queue and a voice channel to rejoin.

    Each dict: {guild_id, voice_channel_id, text_channel_id, loop_mode,
    current, queue}. Used once on startup to resume playback.
    """
    async with _conn().execute(
        "SELECT guild_id, voice_channel_id, text_channel_id, loop_mode "
        "FROM music_settings WHERE voice_channel_id IS NOT NULL"
    ) as cur:
        settings = await cur.fetchall()

    result = []
    for s in settings:
        gid = s["guild_id"]
        async with _conn().execute(
            "SELECT position, is_current, query, title, duration, webpage_url, "
            "thumbnail, uploader, requester_id, requester_name "
            "FROM music_queue WHERE guild_id=? ORDER BY is_current DESC, position ASC",
            (gid,),
        ) as qcur:
            rows = await qcur.fetchall()
        if not rows:
            continue
        current = None
        queue = []
        for r in rows:
            if r["is_current"]:
                current = _queue_row(r)
            else:
                queue.append(_queue_row(r))
        result.append(
            {
                "guild_id": gid,
                "voice_channel_id": s["voice_channel_id"],
                "text_channel_id": s["text_channel_id"],
                "loop_mode": s["loop_mode"],
                "current": current,
                "queue": queue,
            }
        )
    return result


# ── Song blocklist (block URLs / words / phrases from being queued) ─────────────
async def add_music_song_block(
    guild_id: int, pattern: str, added_by: int | None = None
) -> bool:
    """Add a blocked pattern (lowercased). Returns False if already present."""
    try:
        await _conn().execute(
            "INSERT INTO music_song_blocklist (guild_id, pattern, added_by) "
            "VALUES (?,?,?)",
            (
                str(guild_id),
                pattern.lower().strip(),
                str(added_by) if added_by else None,
            ),
        )
        await _conn().commit()
        return True
    except db_crypto.INTEGRITY_ERRORS:
        return False


async def remove_music_song_block(guild_id: int, pattern: str) -> bool:
    cur = await _conn().execute(
        "DELETE FROM music_song_blocklist WHERE guild_id=? AND pattern=?",
        (str(guild_id), pattern.lower().strip()),
    )
    await _conn().commit()
    return cur.rowcount > 0


async def get_music_song_blocks(guild_id: int) -> list[str]:
    async with _conn().execute(
        "SELECT pattern FROM music_song_blocklist WHERE guild_id=? ORDER BY pattern ASC",
        (str(guild_id),),
    ) as cur:
        rows = await cur.fetchall()
    return [r["pattern"] for r in rows]


# ── User blocklist (bar members from using music commands) ──────────────────────
async def add_music_user_block(
    guild_id: int, user_id: int, added_by: int | None = None
) -> bool:
    try:
        await _conn().execute(
            "INSERT INTO music_user_blocklist (guild_id, user_id, added_by) "
            "VALUES (?,?,?)",
            (str(guild_id), str(user_id), str(added_by) if added_by else None),
        )
        await _conn().commit()
        return True
    except db_crypto.INTEGRITY_ERRORS:
        return False


async def remove_music_user_block(guild_id: int, user_id: int) -> bool:
    cur = await _conn().execute(
        "DELETE FROM music_user_blocklist WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id)),
    )
    await _conn().commit()
    return cur.rowcount > 0


async def is_music_user_blocked(guild_id: int, user_id: int) -> bool:
    async with _conn().execute(
        "SELECT 1 FROM music_user_blocklist WHERE guild_id=? AND user_id=? LIMIT 1",
        (str(guild_id), str(user_id)),
    ) as cur:
        return await cur.fetchone() is not None


async def get_music_user_blocks(guild_id: int) -> list[str]:
    async with _conn().execute(
        "SELECT user_id FROM music_user_blocklist WHERE guild_id=?",
        (str(guild_id),),
    ) as cur:
        rows = await cur.fetchall()
    return [r["user_id"] for r in rows]


# ── Played-track history ────────────────────────────────────────────────────────
async def add_music_history(
    guild_id: int,
    title: str | None,
    url: str | None,
    requester_id: int | None,
    keep: int = 200,
) -> None:
    """Append a played track and trim the guild's history to the newest `keep`."""
    gid = str(guild_id)
    await _conn().execute(
        "INSERT INTO music_history (guild_id, title, url, requester_id, played_at) "
        "VALUES (?,?,?,?,?)",
        (
            gid,
            title,
            url,
            str(requester_id) if requester_id else None,
            int(time.time()),
        ),
    )
    await _conn().execute(
        "DELETE FROM music_history WHERE guild_id=? AND id NOT IN "
        "(SELECT id FROM music_history WHERE guild_id=? ORDER BY id DESC LIMIT ?)",
        (gid, gid, keep),
    )
    await _conn().commit()


async def get_music_history(guild_id: int, limit: int = 25) -> list[dict]:
    """Return recently played tracks, newest first."""
    async with _conn().execute(
        "SELECT title, url, requester_id, played_at FROM music_history "
        "WHERE guild_id=? ORDER BY id DESC LIMIT ?",
        (str(guild_id), limit),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {
            "title": r["title"],
            "url": r["url"],
            "requester_id": r["requester_id"],
            "played_at": r["played_at"],
        }
        for r in rows
    ]


async def clear_music_history(guild_id: int) -> int:
    async with _conn().execute(
        "DELETE FROM music_history WHERE guild_id=?", (str(guild_id),)
    ) as cur:
        changed = cur.rowcount
    await _conn().commit()
    return changed


# ══════════════════════════════════════════════════════════════════════════════
#  Leveling (per-guild XP + level rewards)
# ══════════════════════════════════════════════════════════════════════════════


async def _ensure_leveling_tables():
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS user_levels (
            guild_id  TEXT NOT NULL,
            user_id   TEXT NOT NULL,
            xp        INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )
    """)
    await _conn().execute(
        "CREATE INDEX IF NOT EXISTS user_levels_guild_xp "
        "ON user_levels (guild_id, xp DESC)"
    )
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS level_config (
            guild_id          TEXT PRIMARY KEY,
            enabled           INTEGER NOT NULL DEFAULT 0,
            xp_min            INTEGER NOT NULL DEFAULT 15,
            xp_max            INTEGER NOT NULL DEFAULT 25,
            cooldown          INTEGER NOT NULL DEFAULT 60,
            announce_channel  TEXT,
            announce          INTEGER NOT NULL DEFAULT 1,
            coin_reward       INTEGER NOT NULL DEFAULT 0
        )
    """)
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS level_rewards (
            guild_id  TEXT NOT NULL,
            level     INTEGER NOT NULL,
            role_id   TEXT NOT NULL,
            PRIMARY KEY (guild_id, level)
        )
    """)
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS level_ignored_channels (
            guild_id    TEXT NOT NULL,
            channel_id  TEXT NOT NULL,
            PRIMARY KEY (guild_id, channel_id)
        )
    """)
    await _conn().commit()
    # Upgrade path for guilds whose level_config predates coin_reward.
    await _ensure_columns("level_config", {"coin_reward": "INTEGER NOT NULL DEFAULT 0"})


# ── XP ───────────────────────────────────────────────────────────────────────
async def add_xp(guild_id: int, user_id: int, amount: int) -> int:
    """Add (or subtract) XP. Clamps at 0. Returns the new XP total."""
    new_xp = max(0, await get_xp(guild_id, user_id) + int(amount))
    await set_xp(guild_id, user_id, new_xp)
    return new_xp


async def set_xp(guild_id: int, user_id: int, amount: int) -> None:
    """Set a member's XP to an absolute value (clamped at 0)."""
    await _conn().execute(
        "INSERT INTO user_levels (guild_id, user_id, xp) VALUES (?,?,?) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET xp=excluded.xp",
        (str(guild_id), str(user_id), max(0, int(amount))),
    )
    await _conn().commit()


async def get_xp(guild_id: int, user_id: int) -> int:
    async with _conn().execute(
        "SELECT xp FROM user_levels WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id)),
    ) as cur:
        row = await cur.fetchone()
    return row["xp"] if row else 0


async def get_rank(guild_id: int, user_id: int) -> tuple[int, int] | None:
    """Return (rank, xp) for a member; rank is 1-based. None if no XP row."""
    xp = await get_xp(guild_id, user_id)
    if xp <= 0:
        async with _conn().execute(
            "SELECT 1 FROM user_levels WHERE guild_id=? AND user_id=?",
            (str(guild_id), str(user_id)),
        ) as cur:
            if not await cur.fetchone():
                return None
    async with _conn().execute(
        "SELECT COUNT(*) FROM user_levels WHERE guild_id=? AND xp > ?",
        (str(guild_id), xp),
    ) as cur:
        ahead = (await cur.fetchone())[0]
    return ahead + 1, xp


async def get_leaderboard(
    guild_id: int, limit: int = 10, offset: int = 0
) -> list[dict]:
    async with _conn().execute(
        "SELECT user_id, xp FROM user_levels WHERE guild_id=? AND xp > 0 "
        "ORDER BY xp DESC, user_id ASC LIMIT ? OFFSET ?",
        (str(guild_id), int(limit), int(offset)),
    ) as cur:
        rows = await cur.fetchall()
    return [{"user_id": int(r["user_id"]), "xp": r["xp"]} for r in rows]


async def count_ranked(guild_id: int) -> int:
    async with _conn().execute(
        "SELECT COUNT(*) FROM user_levels WHERE guild_id=? AND xp > 0",
        (str(guild_id),),
    ) as cur:
        return (await cur.fetchone())[0]


async def reset_levels(guild_id: int, user_id: int | None = None) -> int:
    """Wipe XP for one member, or the whole guild. Returns rows removed."""
    if user_id is None:
        cur = await _conn().execute(
            "DELETE FROM user_levels WHERE guild_id=?", (str(guild_id),)
        )
    else:
        cur = await _conn().execute(
            "DELETE FROM user_levels WHERE guild_id=? AND user_id=?",
            (str(guild_id), str(user_id)),
        )
    await _conn().commit()
    return cur.rowcount


# ── Config ─────────────────────────────────────────────────────────────────────
async def get_level_config(guild_id: int) -> dict:
    async with _conn().execute(
        "SELECT enabled, xp_min, xp_max, cooldown, announce_channel, announce, "
        "coin_reward FROM level_config WHERE guild_id=?",
        (str(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    if row:
        return {
            "enabled": bool(row["enabled"]),
            "xp_min": row["xp_min"],
            "xp_max": row["xp_max"],
            "cooldown": row["cooldown"],
            "announce_channel": (
                int(row["announce_channel"]) if row["announce_channel"] else None
            ),
            "announce": bool(row["announce"]),
            "coin_reward": row["coin_reward"],
        }
    return {
        "enabled": False,
        "xp_min": 15,
        "xp_max": 25,
        "cooldown": 60,
        "announce_channel": None,
        "announce": True,
        "coin_reward": 0,
    }


async def set_level_config(guild_id: int, **kwargs) -> None:
    """Upsert level config. Unspecified columns keep their current/default value."""
    current = await get_level_config(guild_id)
    current.update(kwargs)
    await _conn().execute(
        "INSERT INTO level_config "
        "(guild_id, enabled, xp_min, xp_max, cooldown, announce_channel, announce, "
        "coin_reward) "
        "VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(guild_id) DO UPDATE SET enabled=excluded.enabled, "
        "xp_min=excluded.xp_min, xp_max=excluded.xp_max, cooldown=excluded.cooldown, "
        "announce_channel=excluded.announce_channel, announce=excluded.announce, "
        "coin_reward=excluded.coin_reward",
        (
            str(guild_id),
            1 if current["enabled"] else 0,
            int(current["xp_min"]),
            int(current["xp_max"]),
            int(current["cooldown"]),
            str(current["announce_channel"]) if current["announce_channel"] else None,
            1 if current["announce"] else 0,
            int(current["coin_reward"]),
        ),
    )
    await _conn().commit()


# ── Role rewards ─────────────────────────────────────────────────────────────
async def add_level_reward(guild_id: int, level: int, role_id: int) -> None:
    await _conn().execute(
        "INSERT INTO level_rewards (guild_id, level, role_id) VALUES (?,?,?) "
        "ON CONFLICT(guild_id, level) DO UPDATE SET role_id=excluded.role_id",
        (str(guild_id), int(level), str(role_id)),
    )
    await _conn().commit()


async def remove_level_reward(guild_id: int, level: int) -> bool:
    cur = await _conn().execute(
        "DELETE FROM level_rewards WHERE guild_id=? AND level=?",
        (str(guild_id), int(level)),
    )
    await _conn().commit()
    return cur.rowcount > 0


async def get_level_rewards(guild_id: int) -> dict[int, int]:
    """Return {level: role_id} for a guild, ascending by level."""
    async with _conn().execute(
        "SELECT level, role_id FROM level_rewards WHERE guild_id=? ORDER BY level ASC",
        (str(guild_id),),
    ) as cur:
        rows = await cur.fetchall()
    return {r["level"]: int(r["role_id"]) for r in rows}


# ── Ignored channels ───────────────────────────────────────────────────────────
async def add_level_ignored_channel(guild_id: int, channel_id: int) -> None:
    await _conn().execute(
        "INSERT OR IGNORE INTO level_ignored_channels (guild_id, channel_id) "
        "VALUES (?,?)",
        (str(guild_id), str(channel_id)),
    )
    await _conn().commit()


async def remove_level_ignored_channel(guild_id: int, channel_id: int) -> bool:
    cur = await _conn().execute(
        "DELETE FROM level_ignored_channels WHERE guild_id=? AND channel_id=?",
        (str(guild_id), str(channel_id)),
    )
    await _conn().commit()
    return cur.rowcount > 0


async def get_level_ignored_channels(guild_id: int) -> set[int]:
    async with _conn().execute(
        "SELECT channel_id FROM level_ignored_channels WHERE guild_id=?",
        (str(guild_id),),
    ) as cur:
        rows = await cur.fetchall()
    return {int(r["channel_id"]) for r in rows}


# ══════════════════════════════════════════════════════════════════════════════
#  Economy (per-guild coin balances + daily claim)
# ══════════════════════════════════════════════════════════════════════════════


async def _ensure_economy_tables():
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS economy (
            guild_id    TEXT NOT NULL,
            user_id     TEXT NOT NULL,
            coins       INTEGER NOT NULL DEFAULT 0,
            last_daily  REAL NOT NULL DEFAULT 0,
            streak      INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )
    """)
    await _conn().execute(
        "CREATE INDEX IF NOT EXISTS economy_guild_coins "
        "ON economy (guild_id, coins DESC)"
    )
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS economy_config (
            guild_id        TEXT PRIMARY KEY,
            daily_amount    INTEGER NOT NULL DEFAULT 100,
            streak_bonus    INTEGER NOT NULL DEFAULT 0,
            currency_name   TEXT NOT NULL DEFAULT 'NanoCoin',
            currency_emoji  TEXT NOT NULL DEFAULT '🪙'
        )
    """)
    await _conn().commit()
    # Lifetime co-op contribution stat (never decreases when coins are spent) and
    # the per-confirmed-co-op reward knob — added after the baseline tables.
    await _ensure_columns("economy", {"contribution": "INTEGER NOT NULL DEFAULT 0"})
    await _ensure_columns(
        "economy_config",
        {
            "coop_reward": "INTEGER NOT NULL DEFAULT 50",
            # Group-raid reward (per participant) + party-size bounds.
            "raid_reward": "INTEGER NOT NULL DEFAULT 100",
            "raid_min": "INTEGER NOT NULL DEFAULT 3",
            "raid_max": "INTEGER NOT NULL DEFAULT 20",
        },
    )
    await _conn().execute(
        "CREATE INDEX IF NOT EXISTS economy_guild_contrib "
        "ON economy (guild_id, contribution DESC)"
    )
    # Shop: redeemable rewards mods configure, and a purchase ledger that backs
    # per-user limits, cooldowns, stock counts, and custom-reward fulfilment.
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS shop_items (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id        TEXT NOT NULL,
            name            TEXT NOT NULL,
            description     TEXT NOT NULL DEFAULT '',
            price           INTEGER NOT NULL,
            kind            TEXT NOT NULL,
            role_id         TEXT,
            payload         TEXT NOT NULL DEFAULT '',
            stock           INTEGER NOT NULL DEFAULT -1,
            per_user_limit  INTEGER NOT NULL DEFAULT 0,
            cooldown        INTEGER NOT NULL DEFAULT 0,
            enabled         INTEGER NOT NULL DEFAULT 1,
            created_at      REAL NOT NULL DEFAULT 0,
            UNIQUE (guild_id, name)
        )
    """)
    await _conn().execute(
        "CREATE INDEX IF NOT EXISTS shop_items_guild ON shop_items (guild_id)"
    )
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS shop_purchases (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id      TEXT NOT NULL,
            item_id       INTEGER NOT NULL,
            user_id       TEXT NOT NULL,
            item_name     TEXT NOT NULL,
            kind          TEXT NOT NULL,
            price         INTEGER NOT NULL,
            bought_at     REAL NOT NULL,
            fulfilled     INTEGER NOT NULL DEFAULT 0,
            fulfilled_by  TEXT
        )
    """)
    await _conn().execute(
        "CREATE INDEX IF NOT EXISTS shop_purchases_user "
        "ON shop_purchases (guild_id, item_id, user_id)"
    )
    await _conn().execute(
        "CREATE INDEX IF NOT EXISTS shop_purchases_pending "
        "ON shop_purchases (guild_id, kind, fulfilled)"
    )
    await _conn().commit()


# ── Balances ───────────────────────────────────────────────────────────────────
async def get_balance(guild_id: int, user_id: int) -> int:
    async with _conn().execute(
        "SELECT coins FROM economy WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id)),
    ) as cur:
        row = await cur.fetchone()
    return row["coins"] if row else 0


async def set_coins(guild_id: int, user_id: int, amount: int) -> None:
    await _conn().execute(
        "INSERT INTO economy (guild_id, user_id, coins) VALUES (?,?,?) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET coins=excluded.coins",
        (str(guild_id), str(user_id), max(0, int(amount))),
    )
    await _conn().commit()


async def add_coins(guild_id: int, user_id: int, amount: int) -> int:
    """Add (or subtract) coins atomically. Clamps at 0. Returns the new balance.

    The mutation is a single SQL statement so concurrent callers can't lose an
    update or race a stale read (the old read-modify-write could create or drop
    coins under concurrent /gamble, /pay, level-ups, etc.).
    """
    amount = int(amount)
    await _conn().execute(
        "INSERT INTO economy (guild_id, user_id, coins) VALUES (?,?,MAX(0,?)) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET coins=MAX(0, coins + ?)",
        (str(guild_id), str(user_id), amount, amount),
    )
    await _conn().commit()
    return await get_balance(guild_id, user_id)


async def try_debit_coins(guild_id: int, user_id: int, amount: int) -> bool:
    """Atomically subtract `amount` only if the balance covers it.

    Returns True on success, False if amount <= 0 or funds are insufficient. The
    conditional UPDATE makes the check-and-debit a single atomic step, so two
    concurrent debits (e.g. rapid /gamble) can't both spend the same coins.
    """
    if amount <= 0:
        return False
    cur = await _conn().execute(
        "UPDATE economy SET coins = coins - ? "
        "WHERE guild_id=? AND user_id=? AND coins >= ?",
        (int(amount), str(guild_id), str(user_id), int(amount)),
    )
    await _conn().commit()
    return cur.rowcount > 0


async def transfer_coins(guild_id: int, from_id: int, to_id: int, amount: int) -> bool:
    """Move coins between two members. Returns False if amount <= 0 or low funds.

    The debit is an atomic conditional UPDATE, so concurrent transfers can't
    overdraw the sender.
    """
    if not await try_debit_coins(guild_id, from_id, amount):
        return False
    await add_coins(guild_id, to_id, amount)
    return True


async def get_econ_rank(guild_id: int, user_id: int) -> tuple[int, int] | None:
    """Return (rank, coins) for a member; None if they have no account row."""
    async with _conn().execute(
        "SELECT coins FROM economy WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id)),
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    coins = row["coins"]
    async with _conn().execute(
        "SELECT COUNT(*) FROM economy WHERE guild_id=? AND coins > ?",
        (str(guild_id), coins),
    ) as cur:
        ahead = (await cur.fetchone())[0]
    return ahead + 1, coins


async def get_econ_leaderboard(
    guild_id: int, limit: int = 10, offset: int = 0
) -> list[dict]:
    async with _conn().execute(
        "SELECT user_id, coins FROM economy WHERE guild_id=? AND coins > 0 "
        "ORDER BY coins DESC, user_id ASC LIMIT ? OFFSET ?",
        (str(guild_id), int(limit), int(offset)),
    ) as cur:
        rows = await cur.fetchall()
    return [{"user_id": int(r["user_id"]), "coins": r["coins"]} for r in rows]


async def count_econ(guild_id: int) -> int:
    async with _conn().execute(
        "SELECT COUNT(*) FROM economy WHERE guild_id=? AND coins > 0",
        (str(guild_id),),
    ) as cur:
        return (await cur.fetchone())[0]


async def reset_economy(guild_id: int, user_id: int | None = None) -> int:
    if user_id is None:
        cur = await _conn().execute(
            "DELETE FROM economy WHERE guild_id=?", (str(guild_id),)
        )
    else:
        cur = await _conn().execute(
            "DELETE FROM economy WHERE guild_id=? AND user_id=?",
            (str(guild_id), str(user_id)),
        )
    await _conn().commit()
    return cur.rowcount


# ── Daily claim state ──────────────────────────────────────────────────────────
async def get_daily_state(guild_id: int, user_id: int) -> tuple[float, int]:
    """Return (last_daily_epoch, streak) for a member."""
    async with _conn().execute(
        "SELECT last_daily, streak FROM economy WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id)),
    ) as cur:
        row = await cur.fetchone()
    if row:
        return row["last_daily"], row["streak"]
    return 0.0, 0


async def set_daily_state(
    guild_id: int, user_id: int, last_daily: float, streak: int
) -> None:
    await _conn().execute(
        "INSERT INTO economy (guild_id, user_id, last_daily, streak) VALUES (?,?,?,?) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET "
        "last_daily=excluded.last_daily, streak=excluded.streak",
        (str(guild_id), str(user_id), float(last_daily), int(streak)),
    )
    await _conn().commit()


# ── Config ─────────────────────────────────────────────────────────────────────
async def get_econ_config(guild_id: int) -> dict:
    async with _conn().execute(
        "SELECT daily_amount, streak_bonus, currency_name, currency_emoji, "
        "coop_reward, raid_reward, raid_min, raid_max "
        "FROM economy_config WHERE guild_id=?",
        (str(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    if row:
        return {
            "daily_amount": row["daily_amount"],
            "streak_bonus": row["streak_bonus"],
            "currency_name": row["currency_name"],
            "currency_emoji": row["currency_emoji"],
            "coop_reward": row["coop_reward"],
            "raid_reward": row["raid_reward"],
            "raid_min": row["raid_min"],
            "raid_max": row["raid_max"],
        }
    return {
        "daily_amount": 100,
        "streak_bonus": 0,
        "currency_name": "NanoCoin",
        "currency_emoji": "🪙",
        "coop_reward": 50,
        "raid_reward": 100,
        "raid_min": 3,
        "raid_max": 20,
    }


async def set_econ_config(guild_id: int, **kwargs) -> None:
    current = await get_econ_config(guild_id)
    current.update(kwargs)
    await _conn().execute(
        "INSERT INTO economy_config "
        "(guild_id, daily_amount, streak_bonus, currency_name, currency_emoji, "
        "coop_reward, raid_reward, raid_min, raid_max) "
        "VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(guild_id) DO UPDATE SET daily_amount=excluded.daily_amount, "
        "streak_bonus=excluded.streak_bonus, currency_name=excluded.currency_name, "
        "currency_emoji=excluded.currency_emoji, coop_reward=excluded.coop_reward, "
        "raid_reward=excluded.raid_reward, raid_min=excluded.raid_min, "
        "raid_max=excluded.raid_max",
        (
            str(guild_id),
            int(current["daily_amount"]),
            int(current["streak_bonus"]),
            str(current["currency_name"]),
            str(current["currency_emoji"]),
            int(current["coop_reward"]),
            int(current["raid_reward"]),
            int(current["raid_min"]),
            int(current["raid_max"]),
        ),
    )
    await _conn().commit()


# ── Contribution (lifetime co-op stat) ───────────────────────────────────────────
async def add_contribution(guild_id: int, user_id: int, amount: int) -> int:
    """Add to a member's lifetime contribution total. Returns the new total.

    Single atomic statement (mirrors add_coins) so concurrent co-op confirms
    can't lose an update. Contribution never decreases on spend.
    """
    amount = int(amount)
    await _conn().execute(
        "INSERT INTO economy (guild_id, user_id, contribution) VALUES (?,?,MAX(0,?)) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET "
        "contribution=MAX(0, contribution + ?)",
        (str(guild_id), str(user_id), amount, amount),
    )
    await _conn().commit()
    return await get_contribution(guild_id, user_id)


async def get_contribution(guild_id: int, user_id: int) -> int:
    async with _conn().execute(
        "SELECT contribution FROM economy WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id)),
    ) as cur:
        row = await cur.fetchone()
    return row["contribution"] if row else 0


async def get_contrib_rank(guild_id: int, user_id: int) -> tuple[int, int] | None:
    """Return (rank, contribution) for a member; None if they have no points."""
    points = await get_contribution(guild_id, user_id)
    if points <= 0:
        return None
    async with _conn().execute(
        "SELECT COUNT(*) FROM economy WHERE guild_id=? AND contribution > ?",
        (str(guild_id), points),
    ) as cur:
        ahead = (await cur.fetchone())[0]
    return ahead + 1, points


async def get_contrib_leaderboard(
    guild_id: int, limit: int = 10, offset: int = 0
) -> list[dict]:
    async with _conn().execute(
        "SELECT user_id, contribution FROM economy "
        "WHERE guild_id=? AND contribution > 0 "
        "ORDER BY contribution DESC, user_id ASC LIMIT ? OFFSET ?",
        (str(guild_id), int(limit), int(offset)),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {"user_id": int(r["user_id"]), "contribution": r["contribution"]} for r in rows
    ]


async def count_contrib(guild_id: int) -> int:
    async with _conn().execute(
        "SELECT COUNT(*) FROM economy WHERE guild_id=? AND contribution > 0",
        (str(guild_id),),
    ) as cur:
        return (await cur.fetchone())[0]


# ══════════════════════════════════════════════════════════════════════════════
#  Shop (redeemable rewards + purchase ledger)
# ══════════════════════════════════════════════════════════════════════════════


def _shop_row(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "price": row["price"],
        "kind": row["kind"],
        "role_id": int(row["role_id"]) if row["role_id"] else None,
        "payload": row["payload"],
        "stock": row["stock"],
        "per_user_limit": row["per_user_limit"],
        "cooldown": row["cooldown"],
        "enabled": bool(row["enabled"]),
    }


async def add_shop_item(
    guild_id: int,
    name: str,
    price: int,
    kind: str,
    *,
    description: str = "",
    role_id: int | None = None,
    payload: str = "",
    stock: int = -1,
    per_user_limit: int = 0,
    cooldown: int = 0,
) -> int | None:
    """Create a shop item. Returns the new item id, or None if the name is taken."""
    try:
        cur = await _conn().execute(
            "INSERT INTO shop_items (guild_id, name, description, price, kind, "
            "role_id, payload, stock, per_user_limit, cooldown, enabled, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,1,?)",
            (
                str(guild_id),
                name,
                description,
                int(price),
                kind,
                str(role_id) if role_id else None,
                payload,
                int(stock),
                int(per_user_limit),
                int(cooldown),
                time.time(),
            ),
        )
    except db_crypto.INTEGRITY_ERRORS:
        return None
    await _conn().commit()
    return cur.lastrowid


async def edit_shop_item(guild_id: int, item_id: int, **fields) -> bool:
    """Update mutable fields of an item. Returns False if nothing matched."""
    allowed = {
        "name",
        "description",
        "price",
        "role_id",
        "payload",
        "stock",
        "per_user_limit",
        "cooldown",
        "enabled",
    }
    sets, params = [], []
    for key, val in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key}=?")
        if key == "role_id":
            params.append(str(val) if val else None)
        elif key == "enabled":
            params.append(1 if val else 0)
        else:
            params.append(val)
    if not sets:
        return False
    params += [str(guild_id), int(item_id)]
    try:
        cur = await _conn().execute(
            f"UPDATE shop_items SET {', '.join(sets)} WHERE guild_id=? AND id=?",
            params,
        )
    except db_crypto.INTEGRITY_ERRORS:
        return False
    await _conn().commit()
    return cur.rowcount > 0


async def remove_shop_item(guild_id: int, item_id: int) -> bool:
    cur = await _conn().execute(
        "DELETE FROM shop_items WHERE guild_id=? AND id=?",
        (str(guild_id), int(item_id)),
    )
    await _conn().commit()
    return cur.rowcount > 0


async def get_shop_item(guild_id: int, item_id: int) -> dict | None:
    async with _conn().execute(
        "SELECT * FROM shop_items WHERE guild_id=? AND id=?",
        (str(guild_id), int(item_id)),
    ) as cur:
        row = await cur.fetchone()
    return _shop_row(row) if row else None


async def get_shop_item_by_name(guild_id: int, name: str) -> dict | None:
    """Case-insensitive name lookup (shop names are unique per guild)."""
    async with _conn().execute(
        "SELECT * FROM shop_items WHERE guild_id=? AND name=? COLLATE NOCASE",
        (str(guild_id), name),
    ) as cur:
        row = await cur.fetchone()
    return _shop_row(row) if row else None


async def list_shop_items(
    guild_id: int, *, enabled_only: bool = False, limit: int = 100, offset: int = 0
) -> list[dict]:
    sql = "SELECT * FROM shop_items WHERE guild_id=?"
    if enabled_only:
        sql += " AND enabled=1"
    sql += " ORDER BY price ASC, id ASC LIMIT ? OFFSET ?"
    async with _conn().execute(sql, (str(guild_id), int(limit), int(offset))) as cur:
        rows = await cur.fetchall()
    return [_shop_row(r) for r in rows]


async def count_shop_items(guild_id: int, *, enabled_only: bool = False) -> int:
    sql = "SELECT COUNT(*) FROM shop_items WHERE guild_id=?"
    if enabled_only:
        sql += " AND enabled=1"
    async with _conn().execute(sql, (str(guild_id),)) as cur:
        return (await cur.fetchone())[0]


async def count_user_purchases(guild_id: int, item_id: int, user_id: int) -> int:
    async with _conn().execute(
        "SELECT COUNT(*) FROM shop_purchases "
        "WHERE guild_id=? AND item_id=? AND user_id=?",
        (str(guild_id), int(item_id), str(user_id)),
    ) as cur:
        return (await cur.fetchone())[0]


async def last_purchase_time(guild_id: int, item_id: int, user_id: int) -> float:
    async with _conn().execute(
        "SELECT MAX(bought_at) FROM shop_purchases "
        "WHERE guild_id=? AND item_id=? AND user_id=?",
        (str(guild_id), int(item_id), str(user_id)),
    ) as cur:
        row = await cur.fetchone()
    return row[0] or 0.0


async def purchase_item(guild_id: int, item_id: int, user_id: int) -> dict:
    """Atomically attempt a purchase, enforcing stock, per-user limit, cooldown,
    and funds.

    Returns a dict with "ok" plus a "reason" on failure
    ("missing"/"disabled"/"limit"/"cooldown"/"out_of_stock"/"funds"), a
    "retry_after" for cooldown failures, and on success "item" + "new_balance".
    Stock is decremented with an atomic conditional UPDATE and refunded if the
    coin debit then fails, so a sold-out race can never overspend or oversell.
    """
    item = await get_shop_item(guild_id, item_id)
    if not item:
        return {"ok": False, "reason": "missing"}
    if not item["enabled"]:
        return {"ok": False, "reason": "disabled"}

    if item["per_user_limit"] > 0:
        bought = await count_user_purchases(guild_id, item_id, user_id)
        if bought >= item["per_user_limit"]:
            return {"ok": False, "reason": "limit", "item": item}
    if item["cooldown"] > 0:
        last = await last_purchase_time(guild_id, item_id, user_id)
        elapsed = time.time() - last
        if last and elapsed < item["cooldown"]:
            return {
                "ok": False,
                "reason": "cooldown",
                "retry_after": int(item["cooldown"] - elapsed),
                "item": item,
            }

    # Reserve stock first (atomic), so two buyers can't claim the last unit.
    if item["stock"] != -1:
        cur = await _conn().execute(
            "UPDATE shop_items SET stock=stock-1 "
            "WHERE guild_id=? AND id=? AND stock>0",
            (str(guild_id), int(item_id)),
        )
        await _conn().commit()
        if cur.rowcount == 0:
            return {"ok": False, "reason": "out_of_stock", "item": item}

    # Charge the buyer; refund the reserved stock if they can't afford it.
    if not await try_debit_coins(guild_id, user_id, item["price"]):
        if item["stock"] != -1:
            await _conn().execute(
                "UPDATE shop_items SET stock=stock+1 WHERE guild_id=? AND id=?",
                (str(guild_id), int(item_id)),
            )
            await _conn().commit()
        return {"ok": False, "reason": "funds", "item": item}

    await _conn().execute(
        "INSERT INTO shop_purchases (guild_id, item_id, user_id, item_name, kind, "
        "price, bought_at, fulfilled) VALUES (?,?,?,?,?,?,?,?)",
        (
            str(guild_id),
            int(item_id),
            str(user_id),
            item["name"],
            item["kind"],
            item["price"],
            time.time(),
            # Role rewards are granted immediately; custom rewards await a mod.
            1 if item["kind"] == "role" else 0,
        ),
    )
    await _conn().commit()
    new_balance = await get_balance(guild_id, user_id)
    return {"ok": True, "item": item, "new_balance": new_balance}


async def list_pending_purchases(
    guild_id: int, limit: int = 25, offset: int = 0
) -> list[dict]:
    """Unfulfilled custom-reward purchases, oldest first (the mod fulfil queue)."""
    async with _conn().execute(
        "SELECT id, item_id, user_id, item_name, price, bought_at FROM shop_purchases "
        "WHERE guild_id=? AND kind='custom' AND fulfilled=0 "
        "ORDER BY bought_at ASC LIMIT ? OFFSET ?",
        (str(guild_id), int(limit), int(offset)),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {
            "id": r["id"],
            "item_id": r["item_id"],
            "user_id": int(r["user_id"]),
            "item_name": r["item_name"],
            "price": r["price"],
            "bought_at": r["bought_at"],
        }
        for r in rows
    ]


async def count_pending_purchases(guild_id: int) -> int:
    async with _conn().execute(
        "SELECT COUNT(*) FROM shop_purchases "
        "WHERE guild_id=? AND kind='custom' AND fulfilled=0",
        (str(guild_id),),
    ) as cur:
        return (await cur.fetchone())[0]


async def fulfill_purchase(guild_id: int, purchase_id: int, mod_id: int) -> dict | None:
    """Mark a pending custom purchase fulfilled. Returns the purchase, or None."""
    async with _conn().execute(
        "SELECT id, user_id, item_name FROM shop_purchases "
        "WHERE guild_id=? AND id=? AND fulfilled=0",
        (str(guild_id), int(purchase_id)),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    await _conn().execute(
        "UPDATE shop_purchases SET fulfilled=1, fulfilled_by=? WHERE id=?",
        (str(mod_id), int(purchase_id)),
    )
    await _conn().commit()
    return {
        "id": row["id"],
        "user_id": int(row["user_id"]),
        "item_name": row["item_name"],
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Gatekeeper (new-account mute + captcha verification)
# ══════════════════════════════════════════════════════════════════════════════
# `gatekeeper_config` holds per-guild settings. `gatekeeper_pending` tracks every
# member currently held behind the mute role: `unmute_at` is set only for
# account-age mutes (auto-lifts once the account is old enough); `kick_at` is set
# whenever verification is on (the member is kicked if still unverified by then).
# Verifying or leaving removes the row.

# Default timings: mute accounts younger than 30d, auto-unmute at 35d (5 weeks),
# kick unverified members after 7d.
_GK_DEFAULTS = {
    "enabled": False,
    "mute_role_id": None,
    "quarantine_channel_id": None,
    "log_channel_id": None,
    "min_account_age": 2592000,  # 30 days
    "unmute_age": 3024000,  # 35 days (5 weeks)
    "mute_new_accounts": True,
    "mute_default_avatar": True,  # Discord's auto-assigned logo avatars
    "mute_stock_avatar": True,  # pickable stock avatars matched against the catalog
    "verify_enabled": True,
    "verify_message": None,
    "kick_timeout": 604800,  # 7 days
    "stock_threshold": 8,  # max dHash Hamming distance for a stock-avatar match
    # How the account-age and bad-avatar checks combine, per guild:
    #   "or"  → mute if young OR bad-avatar (original behaviour)
    #   "and" → mute only if young AND (no-avatar or stock-avatar)
    "match_mode": "or",
    # When the account-age check is one of the mute reasons, auto-unmute once the
    # account crosses unmute_age. Off → those members must verify to get out.
    "age_unmute_enabled": True,
}

# Which columns are stored as 0/1 integers but exposed as Python bools.
_GK_BOOL_COLS = (
    "enabled",
    "mute_new_accounts",
    "mute_default_avatar",
    "mute_stock_avatar",
    "verify_enabled",
    "age_unmute_enabled",
)
# Ordered list of all config columns (used for the upsert in set_gatekeeper_config).
_GK_COLS = (
    "enabled",
    "mute_role_id",
    "quarantine_channel_id",
    "log_channel_id",
    "min_account_age",
    "unmute_age",
    "mute_new_accounts",
    "mute_default_avatar",
    "mute_stock_avatar",
    "verify_enabled",
    "verify_message",
    "kick_timeout",
    "stock_threshold",
    "match_mode",
    "age_unmute_enabled",
)


async def _ensure_gatekeeper_tables() -> None:
    await _conn().executescript("""
        CREATE TABLE IF NOT EXISTS gatekeeper_config (
            guild_id              TEXT PRIMARY KEY,
            enabled               INTEGER NOT NULL DEFAULT 0,
            mute_role_id          TEXT,
            quarantine_channel_id TEXT,
            log_channel_id        TEXT,
            min_account_age       INTEGER NOT NULL DEFAULT 2592000,
            unmute_age            INTEGER NOT NULL DEFAULT 3024000,
            mute_new_accounts     INTEGER NOT NULL DEFAULT 1,
            mute_default_avatar   INTEGER NOT NULL DEFAULT 1,
            mute_stock_avatar     INTEGER NOT NULL DEFAULT 1,
            verify_enabled        INTEGER NOT NULL DEFAULT 1,
            verify_message        TEXT,
            kick_timeout          INTEGER NOT NULL DEFAULT 604800,
            stock_threshold       INTEGER NOT NULL DEFAULT 8,
            match_mode            TEXT NOT NULL DEFAULT 'or',
            age_unmute_enabled    INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS gatekeeper_pending (
            key         TEXT PRIMARY KEY,   -- "guild_id:user_id"
            guild_id    TEXT NOT NULL,
            user_id     TEXT NOT NULL,
            reason      TEXT NOT NULL,
            unmute_at   REAL,
            kick_at     REAL,
            created_at  REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS gk_pending_guild ON gatekeeper_pending (guild_id);
    """)
    await _conn().commit()
    # Added after the table's first cut — backfill for early-branch databases.
    await _ensure_columns(
        "gatekeeper_config",
        {
            "stock_threshold": "INTEGER NOT NULL DEFAULT 8",
            "match_mode": "TEXT NOT NULL DEFAULT 'or'",
            "age_unmute_enabled": "INTEGER NOT NULL DEFAULT 1",
        },
    )


def _gatekeeper_row(row: aiosqlite.Row) -> dict:
    out: dict = {}
    for col in _GK_COLS:
        val = row[col]
        out[col] = bool(val) if col in _GK_BOOL_COLS else val
    return out


async def get_gatekeeper_config(guild_id: int) -> dict:
    """Return the gatekeeper config for a guild, defaults merged in when unset."""
    async with _conn().execute(
        f"SELECT {', '.join(_GK_COLS)} FROM gatekeeper_config WHERE guild_id=? LIMIT 1",
        (str(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    return _gatekeeper_row(row) if row else dict(_GK_DEFAULTS)


async def set_gatekeeper_config(guild_id: int, **kwargs: Any) -> None:
    """Merge kwargs into the guild's config row (creating it from defaults)."""
    current = await get_gatekeeper_config(guild_id)
    current.update(kwargs)
    values = [str(guild_id)]
    for col in _GK_COLS:
        val = current[col]
        if col in _GK_BOOL_COLS:
            values.append(1 if val else 0)
        else:
            values.append(val)
    placeholders = ", ".join(["?"] * (len(_GK_COLS) + 1))
    updates = ", ".join(f"{c}=excluded.{c}" for c in _GK_COLS)
    await _conn().execute(
        f"INSERT INTO gatekeeper_config (guild_id, {', '.join(_GK_COLS)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT(guild_id) DO UPDATE SET {updates}",
        values,
    )
    await _conn().commit()


async def set_gatekeeper_pending(
    key: str,
    guild_id: int,
    user_id: int,
    reason: str,
    unmute_at: float | None,
    kick_at: float | None,
) -> None:
    """Insert or replace a pending gatekeeper hold for a member."""
    await _conn().execute(
        "INSERT INTO gatekeeper_pending "
        "(key, guild_id, user_id, reason, unmute_at, kick_at, created_at) "
        "VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET reason=excluded.reason, "
        "unmute_at=excluded.unmute_at, kick_at=excluded.kick_at",
        (
            key,
            str(guild_id),
            str(user_id),
            reason,
            unmute_at,
            kick_at,
            time.time(),
        ),
    )
    await _conn().commit()


async def get_gatekeeper_pending(key: str) -> dict | None:
    async with _conn().execute(
        "SELECT key, guild_id, user_id, reason, unmute_at, kick_at, created_at "
        "FROM gatekeeper_pending WHERE key=? LIMIT 1",
        (key,),
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def get_all_gatekeeper_pending() -> dict:
    async with _conn().execute(
        "SELECT key, guild_id, user_id, reason, unmute_at, kick_at, created_at "
        "FROM gatekeeper_pending"
    ) as cur:
        rows = await cur.fetchall()
    return {r["key"]: dict(r) for r in rows}


async def remove_gatekeeper_pending(key: str) -> None:
    await _conn().execute("DELETE FROM gatekeeper_pending WHERE key=?", (key,))
    await _conn().commit()


# ══════════════════════════════════════════════════════════════════════════════
#  Birthdays
# ══════════════════════════════════════════════════════════════════════════════
# `birthdays` holds each member's registered birthday (per guild). `month`/`day`
# always set; `year` is optional (drives the "turns N" line when known).
# `last_announced` is the local date string ("YYYY-MM-DD") the birthday was last
# celebrated — the once-per-year/once-per-day guard so the daily check fires each
# birthday exactly once.
#
# `birthday_config` holds per-guild settings: where to announce, the local
# timezone + hour the announcement fires, and the gif / voice-channel toggles.
_BD_DEFAULTS = {
    "enabled": False,
    "channel_id": None,
    "message": None,
    "timezone": "UTC",
    "hour": 9,  # local hour (0-23) the announcement fires
    "gif_enabled": True,
    "vc_enabled": True,  # join the member's voice channel and play the song once
    "ping_enabled": True,
    "song": None,  # optional path/URL override for the voice-channel song
}
_BD_BOOL_COLS = ("enabled", "gif_enabled", "vc_enabled", "ping_enabled")
_BD_COLS = (
    "enabled",
    "channel_id",
    "message",
    "timezone",
    "hour",
    "gif_enabled",
    "vc_enabled",
    "ping_enabled",
    "song",
)


async def _ensure_birthday_tables() -> None:
    await _conn().executescript("""
        CREATE TABLE IF NOT EXISTS birthdays (
            guild_id       TEXT NOT NULL,
            user_id        TEXT NOT NULL,
            month          INTEGER NOT NULL,
            day            INTEGER NOT NULL,
            year           INTEGER,
            last_announced TEXT,
            created_at     REAL NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        );
        CREATE INDEX IF NOT EXISTS birthdays_guild ON birthdays (guild_id);

        CREATE TABLE IF NOT EXISTS birthday_config (
            guild_id      TEXT PRIMARY KEY,
            enabled       INTEGER NOT NULL DEFAULT 0,
            channel_id    TEXT,
            message       TEXT,
            timezone      TEXT NOT NULL DEFAULT 'UTC',
            hour          INTEGER NOT NULL DEFAULT 9,
            gif_enabled   INTEGER NOT NULL DEFAULT 1,
            vc_enabled    INTEGER NOT NULL DEFAULT 1,
            ping_enabled  INTEGER NOT NULL DEFAULT 1,
            song          TEXT
        );
    """)
    await _conn().commit()


def _birthday_config_row(row: aiosqlite.Row) -> dict:
    out: dict = {}
    for col in _BD_COLS:
        val = row[col]
        out[col] = bool(val) if col in _BD_BOOL_COLS else val
    return out


async def get_birthday_config(guild_id: int) -> dict:
    """Return the birthday config for a guild, defaults merged in when unset."""
    async with _conn().execute(
        f"SELECT {', '.join(_BD_COLS)} FROM birthday_config WHERE guild_id=? LIMIT 1",
        (str(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    return _birthday_config_row(row) if row else dict(_BD_DEFAULTS)


async def set_birthday_config(guild_id: int, **kwargs: Any) -> None:
    """Merge kwargs into the guild's config row (creating it from defaults)."""
    current = await get_birthday_config(guild_id)
    current.update(kwargs)
    values = [str(guild_id)]
    for col in _BD_COLS:
        val = current[col]
        if col in _BD_BOOL_COLS:
            values.append(1 if val else 0)
        else:
            values.append(val)
    placeholders = ", ".join(["?"] * (len(_BD_COLS) + 1))
    updates = ", ".join(f"{c}=excluded.{c}" for c in _BD_COLS)
    await _conn().execute(
        f"INSERT INTO birthday_config (guild_id, {', '.join(_BD_COLS)}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT(guild_id) DO UPDATE SET {updates}",
        values,
    )
    await _conn().commit()


async def get_enabled_birthday_configs() -> dict:
    """Return {guild_id(int): config} for every guild with announcements on."""
    async with _conn().execute(
        f"SELECT guild_id, {', '.join(_BD_COLS)} FROM birthday_config "
        "WHERE enabled=1 AND channel_id IS NOT NULL"
    ) as cur:
        rows = await cur.fetchall()
    return {int(r["guild_id"]): _birthday_config_row(r) for r in rows}


async def set_birthday(
    guild_id: int, user_id: int, month: int, day: int, year: int | None
) -> None:
    """Register (or update) a member's birthday. Resets the announced marker so a
    corrected date can still fire today."""
    await _conn().execute(
        "INSERT INTO birthdays "
        "(guild_id, user_id, month, day, year, last_announced, created_at) "
        "VALUES (?,?,?,?,?,NULL,?) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET "
        "month=excluded.month, day=excluded.day, year=excluded.year, "
        "last_announced=NULL",
        (str(guild_id), str(user_id), int(month), int(day), year, time.time()),
    )
    await _conn().commit()


async def get_birthday(guild_id: int, user_id: int) -> dict | None:
    async with _conn().execute(
        "SELECT guild_id, user_id, month, day, year, last_announced "
        "FROM birthdays WHERE guild_id=? AND user_id=? LIMIT 1",
        (str(guild_id), str(user_id)),
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def remove_birthday(guild_id: int, user_id: int) -> bool:
    """Delete a member's birthday. Returns True if a row was removed."""
    cur = await _conn().execute(
        "DELETE FROM birthdays WHERE guild_id=? AND user_id=?",
        (str(guild_id), str(user_id)),
    )
    await _conn().commit()
    return cur.rowcount > 0


async def get_guild_birthdays(guild_id: int) -> list[dict]:
    """Every registered birthday in a guild (for the daily check + list command)."""
    async with _conn().execute(
        "SELECT guild_id, user_id, month, day, year, last_announced "
        "FROM birthdays WHERE guild_id=? ORDER BY month, day",
        (str(guild_id),),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def set_birthday_announced(guild_id: int, user_id: int, date_str: str) -> None:
    """Stamp the local date a birthday was last celebrated (once-per-year guard)."""
    await _conn().execute(
        "UPDATE birthdays SET last_announced=? WHERE guild_id=? AND user_id=?",
        (date_str, str(guild_id), str(user_id)),
    )
    await _conn().commit()


# ══════════════════════════════════════════════════════════════════════════════
#  Live role (streaming presence)
# ══════════════════════════════════════════════════════════════════════════════
# Per-guild config for the streaming auto-role + go-live notifications. Both are
# driven by PRESENCE_UPDATE (requires the privileged presences intent).


async def _ensure_liverole_tables() -> None:
    await _conn().execute("""
        CREATE TABLE IF NOT EXISTS liverole_config (
            guild_id    TEXT PRIMARY KEY,
            enabled     INTEGER NOT NULL DEFAULT 0,
            role_id     TEXT,
            channel_id  TEXT,
            announce    INTEGER NOT NULL DEFAULT 1,
            message     TEXT
        )
    """)
    await _conn().commit()


async def get_liverole_config(guild_id: int) -> dict | None:
    """Return the live-role config for a guild, or None if never set up."""
    async with _conn().execute(
        "SELECT enabled, role_id, channel_id, announce, message "
        "FROM liverole_config WHERE guild_id=? LIMIT 1",
        (str(guild_id),),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    return {
        "enabled": bool(row["enabled"]),
        "role_id": row["role_id"],
        "channel_id": row["channel_id"],
        "announce": bool(row["announce"]),
        "message": row["message"],
    }


async def set_liverole_enabled(guild_id: int, enabled: bool) -> None:
    await _conn().execute(
        """INSERT INTO liverole_config (guild_id, enabled)
           VALUES (?, ?)
           ON CONFLICT(guild_id) DO UPDATE SET enabled=excluded.enabled""",
        (str(guild_id), 1 if enabled else 0),
    )
    await _conn().commit()


async def set_liverole_role(guild_id: int, role_id: int | None) -> None:
    await _conn().execute(
        """INSERT INTO liverole_config (guild_id, role_id)
           VALUES (?, ?)
           ON CONFLICT(guild_id) DO UPDATE SET role_id=excluded.role_id""",
        (str(guild_id), str(role_id) if role_id else None),
    )
    await _conn().commit()


async def set_liverole_channel(guild_id: int, channel_id: int | None) -> None:
    await _conn().execute(
        """INSERT INTO liverole_config (guild_id, channel_id)
           VALUES (?, ?)
           ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id""",
        (str(guild_id), str(channel_id) if channel_id else None),
    )
    await _conn().commit()


async def set_liverole_announce(guild_id: int, announce: bool) -> None:
    await _conn().execute(
        """INSERT INTO liverole_config (guild_id, announce)
           VALUES (?, ?)
           ON CONFLICT(guild_id) DO UPDATE SET announce=excluded.announce""",
        (str(guild_id), 1 if announce else 0),
    )
    await _conn().commit()


async def set_liverole_message(guild_id: int, message: str | None) -> None:
    await _conn().execute(
        """INSERT INTO liverole_config (guild_id, message)
           VALUES (?, ?)
           ON CONFLICT(guild_id) DO UPDATE SET message=excluded.message""",
        (str(guild_id), message),
    )
    await _conn().commit()
