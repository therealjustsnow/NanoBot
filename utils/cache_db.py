"""
utils/cache_db.py
Async SQLite cache — separate database for scraped external content.

Single file: data/cache.db
Keeps the main nanobot.db lean by isolating growing content pools
(FML stories, WYR questions, etc.) in their own database.

Call await cache_db.init() once in NanoBot.setup_hook(), AFTER db.init().

──────────────────────────────────────────────────────
Tables
──────────────────────────────────────────────────────
  fml_stories       (hash) PK — scraped FML stories, deduped by content hash
  wyr_questions     (hash) PK — scraped WYR questions, deduped by content hash
  cache_meta        (key) PK  — last-scrape timestamps, stats
"""

import hashlib
import logging
import os
import time

import aiosqlite

log = logging.getLogger("NanoBot.cache_db")

_DB_PATH = os.path.join("data", "cache.db")

# Module-level connection — opened once in init(), shared for the bot's lifetime
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
#  Meta — last-scrape timestamps
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
