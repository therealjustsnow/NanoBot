"""utils/db.automod — automod rule/config.

Part of the utils/db package (split from the former single-file utils/db.py).
Shared connection state + schema machinery live in utils/db/_core.py; this
module holds the automod rule/config accessors and registers its table setup with _core so
db.init() creates them in the right order.
"""

import asyncio
import json
from typing import Any

import aiosqlite

from utils import db_crypto

from ._core import _commit, _conn, _ensure_columns, register_init

# Serializes read-modify-write cycles on automod_config JSON columns.
_automod_write_lock = asyncio.Lock()


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
    await _commit()
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
    await _commit()


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
    await _commit()


async def set_automod_timeout_seconds(guild_id: int, seconds: int) -> None:
    """Set the timeout duration (in seconds) applied by the automod timeout action."""
    await _conn().execute(
        """INSERT INTO automod_config (guild_id, timeout_seconds)
           VALUES (?, ?)
           ON CONFLICT(guild_id) DO UPDATE SET timeout_seconds=excluded.timeout_seconds""",
        (str(guild_id), seconds),
    )
    await _commit()


async def set_automod_log_channel(guild_id: int, channel_id: int | None) -> None:
    """Set (or clear) the dedicated automod log channel. Pass None to revert to fallback."""
    await _ensure_automod_guild(guild_id)
    await _conn().execute(
        "UPDATE automod_config SET log_channel_id=? WHERE guild_id=?",
        (str(channel_id) if channel_id is not None else None, str(guild_id)),
    )
    await _commit()


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
        await _commit()


async def add_automod_badword(guild_id: int, word: str) -> bool:
    """Add a word to the filter. Returns True if added, False if already present."""
    try:
        await _conn().execute(
            "INSERT INTO automod_badwords (guild_id, word) VALUES (?, ?)",
            (str(guild_id), word.lower().strip()),
        )
        await _commit()
        return True
    except db_crypto.INTEGRITY_ERRORS:
        return False


async def remove_automod_badword(guild_id: int, word: str) -> bool:
    """Remove a word from the filter. Returns True if removed, False if not found."""
    cur = await _conn().execute(
        "DELETE FROM automod_badwords WHERE guild_id=? AND word=?",
        (str(guild_id), word.lower().strip()),
    )
    await _commit()
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
        await _commit()
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
        await _commit()
        return True
    except db_crypto.INTEGRITY_ERRORS:
        return False


async def remove_automod_regex(guild_id: int, pattern: str) -> bool:
    """Remove a regex pattern by its exact pattern string. Returns True if removed."""
    cur = await _conn().execute(
        "DELETE FROM automod_regex_patterns WHERE guild_id=? AND pattern=?",
        (str(guild_id), pattern),
    )
    await _commit()
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
        await _commit()
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
    await _commit()
    return changed > 0


async def get_automod_attachment_words(guild_id: int) -> list[str]:
    """Return all attachment-trigger words for a guild, sorted alphabetically."""
    async with _conn().execute(
        "SELECT word FROM automod_attachment_words WHERE guild_id=? ORDER BY word ASC",
        (str(guild_id),),
    ) as cur:
        rows = await cur.fetchall()
    return [r["word"] for r in rows]


register_init(_ensure_automod_tables)
