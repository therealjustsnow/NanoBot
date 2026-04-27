"""
Tests for utils/cache_db.py — all run against an in-memory SQLite database.
"""

import time

import aiosqlite
import pytest

import utils.cache_db as cache_db


@pytest.fixture(autouse=True)
async def cache_database(monkeypatch):
    """Fresh in-memory cache DB wired into utils.cache_db for every test."""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    monkeypatch.setattr(cache_db, "_db", conn)

    await conn.executescript("""
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
    await conn.commit()

    yield conn

    await conn.close()
    monkeypatch.setattr(cache_db, "_db", None)


# ── FML Stories ───────────────────────────────────────────────────────────────


async def test_add_fml_stories_returns_count():
    added = await cache_db.add_fml_stories(["Today I tripped. FML", "I lost my keys. FML"])
    assert added == 2


async def test_add_fml_stories_skips_duplicates():
    await cache_db.add_fml_stories(["Same story. FML"])
    added = await cache_db.add_fml_stories(["Same story. FML", "New story. FML"])
    assert added == 1


async def test_get_random_fml_empty():
    assert await cache_db.get_random_fml() is None


async def test_get_random_fml_returns_string():
    await cache_db.add_fml_stories(["Today was rough. FML"])
    result = await cache_db.get_random_fml()
    assert isinstance(result, str)
    assert "rough" in result


async def test_count_fml():
    assert await cache_db.count_fml() == 0
    await cache_db.add_fml_stories(["a. FML", "b. FML"])
    assert await cache_db.count_fml() == 2


async def test_purge_fml():
    await cache_db.add_fml_stories(["x. FML", "y. FML"])
    removed = await cache_db.purge_fml()
    assert removed == 2
    assert await cache_db.count_fml() == 0


async def test_fml_dedup_is_case_and_whitespace_insensitive():
    await cache_db.add_fml_stories(["  Hello FML  "])
    added = await cache_db.add_fml_stories(["hello fml"])
    assert added == 0


# ── WYR Questions ─────────────────────────────────────────────────────────────


async def test_add_wyr_questions_returns_count():
    added = await cache_db.add_wyr_questions(["Would you rather fly or swim?"])
    assert added == 1


async def test_add_wyr_questions_skips_duplicates():
    await cache_db.add_wyr_questions(["WYR eat pizza or tacos?"])
    added = await cache_db.add_wyr_questions(["WYR eat pizza or tacos?"])
    assert added == 0


async def test_get_random_wyr_empty():
    assert await cache_db.get_random_wyr() is None


async def test_get_random_wyr_returns_string():
    await cache_db.add_wyr_questions(["Would you rather be rich or famous?"])
    result = await cache_db.get_random_wyr()
    assert isinstance(result, str)


async def test_count_wyr():
    assert await cache_db.count_wyr() == 0
    await cache_db.add_wyr_questions(["Q1?", "Q2?"])
    assert await cache_db.count_wyr() == 2


# ── Image Cache ───────────────────────────────────────────────────────────────


def _img(url="https://example.com/img.gif", source_url=None, artist=None):
    return {"url": url, "source_url": source_url, "artist": artist}


async def test_add_images_returns_count():
    added = await cache_db.add_images("nekos", "hug", [_img("https://a.com/1.gif"), _img("https://a.com/2.gif")])
    assert added == 2


async def test_add_images_skips_duplicate_url():
    await cache_db.add_images("nekos", "hug", [_img("https://a.com/dup.gif")])
    added = await cache_db.add_images("nekos", "hug", [_img("https://a.com/dup.gif")])
    assert added == 0


async def test_add_images_skips_missing_url():
    added = await cache_db.add_images("nekos", "hug", [{"url": None}])
    assert added == 0


async def test_add_images_updates_verified_at_on_duplicate():
    url = "https://a.com/update.gif"
    await cache_db.add_images("nekos", "pat", [_img(url)])
    # Get hash to check verified_at
    h = cache_db._hash(url)
    async with cache_db._conn().execute(
        "SELECT verified_at FROM image_cache WHERE hash=?", (h,)
    ) as cur:
        row = await cur.fetchone()
    original_verified = row["verified_at"]

    await cache_db.add_images("nekos", "pat", [_img(url)])
    async with cache_db._conn().execute(
        "SELECT verified_at FROM image_cache WHERE hash=?", (h,)
    ) as cur:
        row = await cur.fetchone()
    assert row["verified_at"] >= original_verified


async def test_get_random_image_empty():
    assert await cache_db.get_random_image("nekos", "hug") is None


async def test_get_random_image_returns_dict():
    await cache_db.add_images("nekos", "hug", [_img("https://cdn.example.com/img.gif", artist="artist1")])
    result = await cache_db.get_random_image("nekos", "hug")
    assert result is not None
    assert result["url"] == "https://cdn.example.com/img.gif"
    assert result["artist"] == "artist1"


async def test_get_random_image_scoped_by_source_endpoint():
    await cache_db.add_images("nekos", "hug", [_img("https://hug.example.com/1.gif")])
    await cache_db.add_images("nekos", "pat", [_img("https://pat.example.com/1.gif")])
    result = await cache_db.get_random_image("nekos", "hug")
    assert "hug" in result["url"]


async def test_count_images_total():
    await cache_db.add_images("nekos", "hug", [_img("https://a.com/1.gif")])
    await cache_db.add_images("nekos", "pat", [_img("https://a.com/2.gif")])
    assert await cache_db.count_images() == 2


async def test_count_images_by_source():
    await cache_db.add_images("nekos", "hug", [_img("https://a.com/3.gif")])
    await cache_db.add_images("nekosia", "hug", [_img("https://a.com/4.gif")])
    assert await cache_db.count_images(source="nekos") == 1
    assert await cache_db.count_images(source="nekosia") == 1


async def test_count_images_by_source_and_endpoint():
    await cache_db.add_images("nekos", "hug", [_img("https://a.com/5.gif"), _img("https://a.com/6.gif")])
    await cache_db.add_images("nekos", "pat", [_img("https://a.com/7.gif")])
    assert await cache_db.count_images(source="nekos", endpoint="hug") == 2


async def test_get_stale_images():
    old_time = time.time() - (10 * 86400)
    h = cache_db._hash("https://old.example.com/stale.gif")
    await cache_db._conn().execute(
        "INSERT INTO image_cache (hash, source, endpoint, url, source_url, artist, added_at, verified_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (h, "nekos", "hug", "https://old.example.com/stale.gif", None, None, old_time, old_time),
    )
    await cache_db._conn().commit()

    stale = await cache_db.get_stale_images(max_age_seconds=7 * 86400)
    assert any(s["url"] == "https://old.example.com/stale.gif" for s in stale)


async def test_get_stale_images_excludes_fresh():
    await cache_db.add_images("nekos", "hug", [_img("https://fresh.example.com/img.gif")])
    stale = await cache_db.get_stale_images(max_age_seconds=7 * 86400)
    assert not any(s["url"] == "https://fresh.example.com/img.gif" for s in stale)


async def test_mark_verified():
    url = "https://verify.example.com/img.gif"
    h = cache_db._hash(url)
    old_time = time.time() - 1000
    await cache_db._conn().execute(
        "INSERT INTO image_cache (hash, source, endpoint, url, source_url, artist, added_at, verified_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (h, "nekos", "hug", url, None, None, old_time, old_time),
    )
    await cache_db._conn().commit()

    before = time.time()
    await cache_db.mark_verified(h)
    async with cache_db._conn().execute("SELECT verified_at FROM image_cache WHERE hash=?", (h,)) as cur:
        row = await cur.fetchone()
    assert row["verified_at"] >= before


async def test_remove_image():
    url = "https://dead.example.com/img.gif"
    await cache_db.add_images("nekos", "hug", [_img(url)])
    h = cache_db._hash(url)
    await cache_db.remove_image(h)
    assert await cache_db.get_random_image("nekos", "hug") is None


async def test_get_image_stats():
    await cache_db.add_images("nekos", "hug", [_img("https://s.com/1.gif"), _img("https://s.com/2.gif")])
    await cache_db.add_images("nekosia", "pat", [_img("https://s.com/3.gif")])
    stats = await cache_db.get_image_stats()
    assert stats["nekos"]["hug"] == 2
    assert stats["nekosia"]["pat"] == 1


# ── Meta ──────────────────────────────────────────────────────────────────────


async def test_get_meta_missing():
    assert await cache_db.get_meta("last_scrape") is None


async def test_set_and_get_meta():
    await cache_db.set_meta("last_scrape", "1700000000")
    assert await cache_db.get_meta("last_scrape") == "1700000000"


async def test_set_meta_upsert():
    await cache_db.set_meta("key", "v1")
    await cache_db.set_meta("key", "v2")
    assert await cache_db.get_meta("key") == "v2"
