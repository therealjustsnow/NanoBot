"""
tests/test_sqlite_timing.py
Tests for the slow-query logging wrapper in utils/sqlite_timing.py against a
real in-memory aiosqlite connection.

Covers both call styles (await + async with), delegation of non-execute
attributes, the disabled (zero-overhead, native passthrough) path, and that a
query over the threshold is logged while a fast one is not.
"""

import logging

import aiosqlite
import pytest

from utils import sqlite_timing


@pytest.fixture(autouse=True)
def _reset_threshold():
    # Each test sets its own threshold; restore the default afterwards.
    yield
    sqlite_timing.set_threshold(0)


def test_set_threshold_coercion():
    sqlite_timing.set_threshold(150)
    assert sqlite_timing.SLOW_QUERY_MS == 150.0
    assert sqlite_timing.enabled()
    sqlite_timing.set_threshold(None)
    assert sqlite_timing.SLOW_QUERY_MS == 0.0
    assert not sqlite_timing.enabled()
    sqlite_timing.set_threshold("bad")  # invalid -> disabled, no raise
    assert sqlite_timing.SLOW_QUERY_MS == 0.0
    sqlite_timing.set_threshold(-5)  # negative -> clamped to 0
    assert sqlite_timing.SLOW_QUERY_MS == 0.0


def test_short_trims_and_flattens():
    assert sqlite_timing._short("select\n   1") == "select 1"
    long = "x" * 200
    out = sqlite_timing._short(long, limit=50)
    assert len(out) == 50 and out.endswith("…")


@pytest.mark.asyncio
async def test_disabled_returns_native_object():
    sqlite_timing.set_threshold(0)
    raw = await aiosqlite.connect(":memory:")
    try:
        conn = sqlite_timing.wrap(raw, "test")
        # When disabled, execute() hands back the native aiosqlite execute object
        # (not our _TimedExecute wrapper).
        ex = conn.execute("SELECT 1")
        assert not isinstance(ex, sqlite_timing._TimedExecute)
        cur = await ex
        assert (await cur.fetchone())[0] == 1
    finally:
        await raw.close()


@pytest.mark.asyncio
async def test_enabled_both_call_styles_and_delegation():
    sqlite_timing.set_threshold(1000)  # high enough that nothing logs as slow
    raw = await aiosqlite.connect(":memory:")
    try:
        conn = sqlite_timing.wrap(raw, "test")
        conn.row_factory = aiosqlite.Row  # __setattr__ should land on raw
        assert raw.row_factory is aiosqlite.Row

        # executescript delegates via __getattr__
        await conn.executescript("CREATE TABLE t (id INTEGER, v TEXT);")

        # executemany (timed wrapper) + commit delegation
        await conn.executemany(
            "INSERT INTO t (id, v) VALUES (?, ?)", [(1, "a"), (2, "b")]
        )
        await conn.commit()

        # await style
        cur = await conn.execute("SELECT COUNT(*) FROM t")
        assert (await cur.fetchone())[0] == 2

        # async with style
        async with conn.execute("SELECT v FROM t WHERE id = ?", (1,)) as cur2:
            row = await cur2.fetchone()
            assert row[0] == "a"
    finally:
        await raw.close()


@pytest.mark.asyncio
async def test_slow_query_is_logged(caplog):
    sqlite_timing.set_threshold(0.0001)  # tiny -> any query counts as "slow"
    raw = await aiosqlite.connect(":memory:")
    try:
        conn = sqlite_timing.wrap(raw, "unit")
        with caplog.at_level(logging.WARNING, logger="NanoBot.sqlite"):
            await conn.execute("SELECT 1")
        assert any(
            "slow query [unit]" in r.message for r in caplog.records
        ), caplog.records
    finally:
        await raw.close()
