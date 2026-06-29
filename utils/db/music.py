"""utils/db.music — music queue/settings.

Part of the utils/db package (split from the former single-file utils/db.py).
Shared connection state + schema machinery live in utils/db/_core.py; this
module holds the music queue/settings accessors and registers its table setup with _core so
db.init() creates them in the right order.
"""

import time

import aiosqlite

from utils import db_crypto

from ._core import _conn, _ensure_columns, register_init


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


register_init(_ensure_music_tables)
