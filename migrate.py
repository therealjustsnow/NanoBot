"""
migrate.py — NanoBot JSON → SQLite migration
─────────────────────────────────────────────
Reads all existing JSON data files and imports them into data/nanobot.db.
Your JSON files are left untouched as a backup.

Run ONCE before starting NanoBot for the first time after upgrading:

    python migrate.py

Safe to run multiple times — uses INSERT OR IGNORE / ON CONFLICT DO NOTHING
so nothing is duplicated if you run it twice.
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

_DATA  = "data"
_DB    = os.path.join(_DATA, "nanobot.db")

# ── ANSI colour helpers ────────────────────────────────────────────────────────
_USE_COLOUR = sys.platform != "win32" or os.getenv("TERM")
def _c(code, text): return f"\033[{code}m{text}\033[0m" if _USE_COLOUR else text
def ok(msg):   print(f"  {_c('32', '✅')} {msg}")
def skip(msg): print(f"  {_c('33', '⚠️ ')} {msg}")
def info(msg): print(f"  {_c('34', 'ℹ️ ')} {msg}")
def err(msg):  print(f"  {_c('31', '❌')} {msg}")


def _read_json(filename: str) -> dict:
    path = os.path.join(_DATA, filename)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            skip(f"{filename} is corrupt and will be skipped: {e}")
            return {}


def _norm_tag(v) -> dict:
    """Normalise legacy plain-string tags to dict shape."""
    if isinstance(v, str):
        return {"content": v, "image_url": None, "by_id": None, "by_name": None}
    return v


def migrate(db: sqlite3.Connection):
    cur = db.cursor()

    # ── Schema ─────────────────────────────────────────────────────────────────
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS tags (
            guild_id  TEXT NOT NULL,
            scope     TEXT NOT NULL,
            name      TEXT NOT NULL,
            content   TEXT,
            image_url TEXT,
            by_id     TEXT,
            by_name   TEXT,
            PRIMARY KEY (guild_id, scope, name)
        );
        CREATE TABLE IF NOT EXISTS notes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id   TEXT NOT NULL,
            user_id    TEXT NOT NULL,
            content    TEXT NOT NULL,
            by_id      TEXT NOT NULL,
            by_name    TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS notes_guild_user ON notes (guild_id, user_id);
        CREATE TABLE IF NOT EXISTS prefixes (
            guild_id TEXT PRIMARY KEY,
            prefix   TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS unban_schedules (
            key      TEXT PRIMARY KEY,
            guild_id TEXT NOT NULL,
            user_id  TEXT NOT NULL,
            until    REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS slow_schedules (
            channel_id TEXT PRIMARY KEY,
            guild_id   TEXT NOT NULL,
            until      REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reminders (
            id         TEXT PRIMARY KEY,
            target_id  TEXT NOT NULL,
            set_by_id  TEXT NOT NULL,
            guild_id   TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            message    TEXT NOT NULL,
            due        REAL NOT NULL,
            duration   REAL NOT NULL,
            dm         INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS reminders_target ON reminders (target_id);
    """)
    db.commit()

    # ── tags.json ──────────────────────────────────────────────────────────────
    print("\n  Tags")
    tags_data = _read_json("tags.json")
    tag_count = 0
    for guild_id, guild_data in tags_data.items():
        # Global tags
        for name, raw in guild_data.get("global", {}).items():
            tag = _norm_tag(raw)
            cur.execute(
                "INSERT OR IGNORE INTO tags (guild_id, scope, name, content, image_url, by_id, by_name) "
                "VALUES (?,?,?,?,?,?,?)",
                (guild_id, "global", name,
                 tag.get("content"), tag.get("image_url"),
                 tag.get("by_id"), tag.get("by_name")),
            )
            tag_count += cur.rowcount

        # Personal tags
        for user_id, user_tags in guild_data.get("personal", {}).items():
            for name, raw in user_tags.items():
                tag = _norm_tag(raw)
                cur.execute(
                    "INSERT OR IGNORE INTO tags (guild_id, scope, name, content, image_url, by_id, by_name) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (guild_id, user_id, name,
                     tag.get("content"), tag.get("image_url"), None, None),
                )
                tag_count += cur.rowcount

    db.commit()
    ok(f"Tags: {tag_count} imported")

    # ── notes.json ─────────────────────────────────────────────────────────────
    print("\n  Notes")
    notes_data = _read_json("notes.json")
    note_count = 0
    for guild_id, users in notes_data.items():
        for user_id, notes in users.items():
            for n in notes:
                cur.execute(
                    "INSERT INTO notes (guild_id, user_id, content, by_id, by_name, created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (guild_id, user_id, n.get("note", ""),
                     n.get("by_id", ""), n.get("by_name", ""),
                     n.get("at", datetime.now(timezone.utc).isoformat())),
                )
                note_count += 1
    db.commit()
    ok(f"Notes: {note_count} imported")

    # ── prefixes.json ──────────────────────────────────────────────────────────
    print("\n  Prefixes")
    prefix_data = _read_json("prefixes.json")
    prefix_count = 0
    for guild_id, prefix in prefix_data.items():
        cur.execute(
            "INSERT OR IGNORE INTO prefixes (guild_id, prefix) VALUES (?,?)",
            (guild_id, prefix),
        )
        prefix_count += cur.rowcount
    db.commit()
    ok(f"Prefixes: {prefix_count} imported")

    # ── unban_schedules.json ───────────────────────────────────────────────────
    print("\n  Unban schedules")
    unban_data = _read_json("unban_schedules.json")
    unban_count = 0
    now = datetime.now(timezone.utc).timestamp()
    for key, info in unban_data.items():
        if info.get("until", 0) > now:  # skip already-expired
            cur.execute(
                "INSERT OR IGNORE INTO unban_schedules (key, guild_id, user_id, until) "
                "VALUES (?,?,?,?)",
                (key, str(info["guild_id"]), str(info["user_id"]), info["until"]),
            )
            unban_count += cur.rowcount
        else:
            skip(f"Skipped expired unban: {key}")
    db.commit()
    ok(f"Unban schedules: {unban_count} imported")

    # ── slow_schedules.json ────────────────────────────────────────────────────
    print("\n  Slowmode schedules")
    slow_data = _read_json("slow_schedules.json")
    slow_count = 0
    for channel_id, info in slow_data.items():
        if info.get("until", 0) > now:
            cur.execute(
                "INSERT OR IGNORE INTO slow_schedules (channel_id, guild_id, until) "
                "VALUES (?,?,?)",
                (channel_id, str(info["guild_id"]), info["until"]),
            )
            slow_count += cur.rowcount
        else:
            skip(f"Skipped expired slowmode: channel {channel_id}")
    db.commit()
    ok(f"Slowmode schedules: {slow_count} imported")

    # ── reminders.json ─────────────────────────────────────────────────────────
    print("\n  Reminders")
    reminder_data = _read_json("reminders.json")
    reminder_count = 0
    for rid, info in reminder_data.items():
        if info.get("due", 0) > now:
            cur.execute(
                "INSERT OR IGNORE INTO reminders "
                "(id, target_id, set_by_id, guild_id, channel_id, message, due, duration, dm) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    info["id"], info["target_id"], info["set_by_id"],
                    info["guild_id"], info["channel_id"], info["message"],
                    info["due"], info.get("duration", 0),
                    1 if info.get("dm", True) else 0,
                ),
            )
            reminder_count += cur.rowcount
        else:
            skip(f"Skipped expired reminder: {rid}")
    db.commit()
    ok(f"Reminders: {reminder_count} imported")


def main():
    print(_c("1;36", "\n⚡ NanoBot — JSON → SQLite Migration\n"))

    if not os.path.isdir(_DATA):
        err(f"'{_DATA}/' directory not found. Run from the NanoBot root directory.")
        sys.exit(1)

    already_exists = os.path.exists(_DB)
    if already_exists:
        size = os.path.getsize(_DB) // 1024
        info(f"nanobot.db already exists ({size} KB) — duplicate rows will be skipped (INSERT OR IGNORE).")
    else:
        info("Creating new nanobot.db...")

    db = sqlite3.connect(_DB)
    db.row_factory = sqlite3.Row

    try:
        migrate(db)
    finally:
        db.close()

    print()
    print(_c("1;32", "  ✅ Migration complete!"))
    print()
    print("  Your original JSON files are untouched in data/ as a backup.")
    print("  Once you're happy everything works, you can delete them.")
    print()


if __name__ == "__main__":
    main()
