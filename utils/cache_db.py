"""
utils/cache_db.py
Async SQLite cache -- separate database for scraped external content.

Single file: data/cache.db
Keeps the main nanobot.db lean by isolating growing content pools
(FML stories, WYR questions, image URLs, etc.) in their own database.

Call await cache_db.init() once in NanoBot.setup_hook(), AFTER db.init().

------------------------------------------------------
Tables
------------------------------------------------------
  fml_stories       (hash) PK  -- scraped FML stories, deduped by content hash
  wyr_questions     (hash) PK  -- scraped WYR questions, deduped by content hash
  image_cache       (hash) PK  -- cached image/GIF URLs by source+endpoint
  cache_meta        (key) PK   -- last-scrape timestamps, stats
"""

import hashlib
import logging
import os
import time

import aiosqlite

log = logging.getLogger("NanoBot.cache_db")

_DB_PATH = os.path.join("data", "cache.db")

# Module-level connection -- opened once in init(), shared for the bot's lifetime
_db: aiosqlite.Connection | None = None


async def init() -> None:
    """Open the cache database and create all tables. Call once at bot startup."""
    global _db
    os.makedirs("data", exist_ok=True)
    _db = await aiosqlite.connect(_DB_PATH)
    _db.row_factory = aiosqlite.Row

    await _db.execute("PRAGMA journal_mode=WAL")

    await _db.executescript("""
        CREATE TABLE IF NOT EXISTS fml_stories (
            hash       TEXT PRIMARY KEY,
            content    TEXT NOT NULL,
            added_at   REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS wyr_questions (
            hash       TEXT PRIMARY KEY,
            question   TEXT NOT NULL,
            added_at   REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS image_cache (
            hash        TEXT PRIMARY KEY,
            source      TEXT NOT NULL,
            endpoint    TEXT NOT NULL,
            url         TEXT NOT NULL,
            source_url  TEXT,
            artist      TEXT,
            added_at    REAL NOT NULL,
            verified_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS img_source_ep
            ON image_cache (source, endpoint);

        CREATE TABLE IF NOT EXISTS cache_meta (
            key    TEXT PRIMARY KEY,
            value  TEXT NOT NULL
        );
    """)
    await _db.commit()
    log.info(f"Cache database ready: {_DB_PATH}")


async def close() -> None:
    """Close the cache database connection cleanly."""
    global _db
    if _db:
        await _db.close()
        _db = None


def _conn() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("cache_db.init() has not been called")
    return _db


def _hash(text: str) -> str:
    """Stable content hash for deduplication."""
    return hashlib.sha256(text.strip().lower().encode()).hexdigest()[:16]


# ══════════════════════════════════════════════════════════════════════════════
#  FML Stories
# ══════════════════════════════════════════════════════════════════════════════


async def add_fml_stories(stories: list[str]) -> int:
    """Insert stories, skipping duplicates. Returns count of NEW stories added."""
    now = time.time()
    added = 0
    for story in stories:
        h = _hash(story)
        try:
            await _conn().execute(
                "INSERT INTO fml_stories (hash, content, added_at) VALUES (?, ?, ?)",
                (h, story.strip(), now),
            )
            added += 1
        except aiosqlite.IntegrityError:
            pass  # duplicate
    if added:
        await _conn().commit()
    return added


async def get_random_fml() -> str | None:
    """Return a random FML story from the cache."""
    async with _conn().execute(
        "SELECT content FROM fml_stories ORDER BY RANDOM() LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    return row["content"] if row else None


async def count_fml() -> int:
    async with _conn().execute("SELECT COUNT(*) FROM fml_stories") as cur:
        row = await cur.fetchone()
    return row[0]


async def purge_fml() -> int:
    """Delete all cached FML stories. Returns number of rows removed."""
    async with _conn().execute("SELECT COUNT(*) FROM fml_stories") as cur:
        before = (await cur.fetchone())[0]
    await _conn().execute("DELETE FROM fml_stories")
    await _conn().commit()
    return before


# ══════════════════════════════════════════════════════════════════════════════
#  WYR Questions
# ══════════════════════════════════════════════════════════════════════════════


async def add_wyr_questions(questions: list[str]) -> int:
    """Insert questions, skipping duplicates. Returns count of NEW questions added."""
    now = time.time()
    added = 0
    for q in questions:
        h = _hash(q)
        try:
            await _conn().execute(
                "INSERT INTO wyr_questions (hash, question, added_at) VALUES (?, ?, ?)",
                (h, q.strip(), now),
            )
            added += 1
        except aiosqlite.IntegrityError:
            pass  # duplicate
    if added:
        await _conn().commit()
    return added


async def get_random_wyr() -> str | None:
    """Return a random WYR question from the cache."""
    async with _conn().execute(
        "SELECT question FROM wyr_questions ORDER BY RANDOM() LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    return row["question"] if row else None


async def count_wyr() -> int:
    async with _conn().execute("SELECT COUNT(*) FROM wyr_questions") as cur:
        row = await cur.fetchone()
    return row[0]


# ══════════════════════════════════════════════════════════════════════════════
#  Image / GIF URL Cache
# ══════════════════════════════════════════════════════════════════════════════
#
#  source   = "nekos" | "nekosia"   (which API it came from)
#  endpoint = the API endpoint/tag  (e.g. "hug", "bite", "thighs")
#
#  Deduplication is on the URL itself (hashed), so the same image URL
#  won't be stored twice even if two different endpoints return it.
# ══════════════════════════════════════════════════════════════════════════════


async def add_images(
    source: str,
    endpoint: str,
    images: list[dict],
) -> int:
    """
    Insert image URLs, skipping duplicates. Returns count of NEW images added.

    Each dict in ``images`` should have:
      - url: str           (required)
      - source_url: str    (optional, artist source link)
      - artist: str        (optional, artist name/credit)
    """
    now = time.time()
    added = 0
    for img in images:
        url = img.get("url")
        if not url:
            continue
        h = _hash(url)
        try:
            await _conn().execute(
                "INSERT INTO image_cache "
                "(hash, source, endpoint, url, source_url, artist, added_at, verified_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    h,
                    source,
                    endpoint,
                    url,
                    img.get("source_url"),
                    img.get("artist"),
                    now,
                    now,
                ),
            )
            added += 1
        except aiosqlite.IntegrityError:
            # Already cached -- update verified_at to mark it as still valid
            await _conn().execute(
                "UPDATE image_cache SET verified_at=? WHERE hash=?",
                (now, h),
            )
    await _conn().commit()
    return added


async def get_random_image(source: str, endpoint: str) -> dict | None:
    """
    Return a random cached image for the given source+endpoint.
    Returns dict with keys: url, source_url, artist  (or None if empty).
    """
    async with _conn().execute(
        "SELECT url, source_url, artist FROM image_cache "
        "WHERE source=? AND endpoint=? ORDER BY RANDOM() LIMIT 1",
        (source, endpoint),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None
    return {
        "url": row["url"],
        "source_url": row["source_url"],
        "artist": row["artist"],
    }


async def count_images(source: str | None = None, endpoint: str | None = None) -> int:
    """Count cached images, optionally filtered by source and/or endpoint."""
    if source and endpoint:
        sql = "SELECT COUNT(*) FROM image_cache WHERE source=? AND endpoint=?"
        params = (source, endpoint)
    elif source:
        sql = "SELECT COUNT(*) FROM image_cache WHERE source=?"
        params = (source,)
    else:
        sql = "SELECT COUNT(*) FROM image_cache"
        params = ()
    async with _conn().execute(sql, params) as cur:
        row = await cur.fetchone()
    return row[0]


async def get_stale_images(
    max_age_seconds: float = 7 * 86400,
    limit: int = 500,
) -> list[dict]:
    """
    Return images whose verified_at is older than max_age_seconds.
    Used by the revalidation loop to find URLs that need checking.
    """
    cutoff = time.time() - max_age_seconds
    async with _conn().execute(
        "SELECT hash, source, endpoint, url FROM image_cache "
        "WHERE verified_at < ? ORDER BY verified_at ASC LIMIT ?",
        (cutoff, limit),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {
            "hash": r["hash"],
            "source": r["source"],
            "endpoint": r["endpoint"],
            "url": r["url"],
        }
        for r in rows
    ]


async def mark_verified(url_hash: str) -> None:
    """Update verified_at to now for a given image hash."""
    await _conn().execute(
        "UPDATE image_cache SET verified_at=? WHERE hash=?",
        (time.time(), url_hash),
    )
    await _conn().commit()


async def remove_image(url_hash: str) -> None:
    """Remove a dead image URL from the cache."""
    await _conn().execute("DELETE FROM image_cache WHERE hash=?", (url_hash,))
    await _conn().commit()


async def get_image_stats() -> dict:
    """Return a summary of cached images by source. For admin/debug commands."""
    async with _conn().execute(
        "SELECT source, endpoint, COUNT(*) as cnt FROM image_cache "
        "GROUP BY source, endpoint ORDER BY source, endpoint"
    ) as cur:
        rows = await cur.fetchall()
    stats: dict[str, dict[str, int]] = {}
    for r in rows:
        src = r["source"]
        if src not in stats:
            stats[src] = {}
        stats[src][r["endpoint"]] = r["cnt"]
    return stats


# ══════════════════════════════════════════════════════════════════════════════
#  Meta -- last-scrape timestamps
# ══════════════════════════════════════════════════════════════════════════════


async def get_meta(key: str) -> str | None:
    async with _conn().execute(
        "SELECT value FROM cache_meta WHERE key=? LIMIT 1", (key,)
    ) as cur:
        row = await cur.fetchone()
    return row["value"] if row else None


async def set_meta(key: str, value: str) -> None:
    await _conn().execute(
        "INSERT INTO cache_meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    await _conn().commit()
