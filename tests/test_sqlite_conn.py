"""
tests/test_sqlite_conn.py
Tests for the shared-connection proxy in utils/sqlite_conn.py against a real
in-memory aiosqlite connection.

The headline test is `test_returning_write_does_not_block_a_concurrent_commit`:
it reproduces the production crash (`OperationalError: cannot commit
transaction - SQL statements in progress`) on the raw connection, then shows
the proxy makes it impossible — the whole reason this module exists. The rest
covers the cursor API `Result` has to satisfy, both call styles (await + async
with), delegation of non-execute attributes, and the slow-query log.
"""

import asyncio
import logging

import aiosqlite
import pytest

from utils import sqlite_conn


@pytest.fixture(autouse=True)
def _reset_threshold():
    # Each test sets its own threshold; restore the default afterwards.
    yield
    sqlite_conn.set_threshold(0)


def test_set_threshold_coercion():
    sqlite_conn.set_threshold(150)
    assert sqlite_conn.SLOW_QUERY_MS == 150.0
    assert sqlite_conn.enabled()
    sqlite_conn.set_threshold(None)
    assert sqlite_conn.SLOW_QUERY_MS == 0.0
    assert not sqlite_conn.enabled()
    sqlite_conn.set_threshold("bad")  # invalid -> disabled, no raise
    assert sqlite_conn.SLOW_QUERY_MS == 0.0
    sqlite_conn.set_threshold(-5)  # negative -> clamped to 0
    assert sqlite_conn.SLOW_QUERY_MS == 0.0


def test_short_trims_and_flattens():
    assert sqlite_conn._short("select\n   1") == "select 1"
    long = "x" * 200
    out = sqlite_conn._short(long, limit=50)
    assert len(out) == 50 and out.endswith("…")


async def _seed(conn):
    await conn.executescript("CREATE TABLE t (id INTEGER PRIMARY KEY, v INTEGER);")
    await conn.execute("INSERT INTO t (id, v) VALUES (1, 0)")
    await conn.commit()


@pytest.mark.asyncio
async def test_returning_write_does_not_block_a_concurrent_commit():
    """The production bug, and the fix, side by side.

    A DML statement with RETURNING is a *writer that returns rows*: the native
    two-step (execute, then await the fetch) leaves it active across an await,
    and SQLite refuses any commit while one is. Every add_coins/add_item/add_xp
    goes through that path, so a /fish cast committing in the window died with
    "cannot commit transaction - SQL statements in progress".
    """
    raw = await aiosqlite.connect(":memory:")
    try:
        await _seed(raw)

        # Native cursor: statement still active between execute and fetch.
        cur = await raw.execute("UPDATE t SET v = v + 1 WHERE id = 1 RETURNING v")
        with pytest.raises(aiosqlite.OperationalError, match="statements in progress"):
            await raw.commit()
        await cur.close()
        await raw.commit()

        # Proxy: execute + fetch + close happen in one worker-thread hop, so
        # there is no window at all.
        conn = sqlite_conn.wrap(raw, "test")
        cur2 = await conn.execute("UPDATE t SET v = v + 1 WHERE id = 1 RETURNING v")
        await raw.commit()  # would raise on a live statement
        assert (await cur2.fetchone())[0] == 2
    finally:
        await raw.close()


@pytest.mark.asyncio
async def test_interleaved_reads_and_commits_survive(tmp_path):
    """Many coroutines sharing one connection, reads and commits interleaved.

    The event loop is free to switch between any two awaits here; with the
    proxy in place no statement is ever live across one, so nothing blows up
    and the writes all land.
    """
    raw = await aiosqlite.connect(str(tmp_path / "race.db"))
    try:
        raw.row_factory = aiosqlite.Row
        await _seed(raw)
        conn = sqlite_conn.wrap(raw, "race")
        await conn.executemany(
            "INSERT INTO t (id, v) VALUES (?, ?)", [(i, i) for i in range(2, 60)]
        )
        await conn.commit()

        async def reader():
            for _ in range(40):
                async with conn.execute("SELECT id, v FROM t ORDER BY v") as cur:
                    await cur.fetchone()

        async def bumper():
            for _ in range(40):
                async with conn.execute(
                    "UPDATE t SET v = v + 1 WHERE id = 1 RETURNING v"
                ) as cur:
                    await cur.fetchone()
                await conn.commit()

        await asyncio.gather(reader(), bumper(), reader(), bumper())

        async with conn.execute("SELECT v FROM t WHERE id = 1") as cur:
            assert (await cur.fetchone())["v"] == 80
    finally:
        await raw.close()


@pytest.mark.asyncio
async def test_result_covers_the_cursor_api():
    raw = await aiosqlite.connect(":memory:")
    try:
        raw.row_factory = aiosqlite.Row
        conn = sqlite_conn.wrap(raw, "test")
        await _seed(conn)
        await conn.executemany(
            "INSERT INTO t (id, v) VALUES (?, ?)", [(2, 20), (3, 30)]
        )
        await conn.commit()

        # rowcount on a plain DML write
        cur = await conn.execute("UPDATE t SET v = v + 1 WHERE id > 1")
        assert cur.rowcount == 2

        # lastrowid on an insert
        cur = await conn.execute("INSERT INTO t (v) VALUES (99)")
        assert cur.lastrowid == 4

        # fetchone walks the rows, then returns None; fetchall takes the rest
        async with conn.execute("SELECT id FROM t ORDER BY id") as cur:
            assert (await cur.fetchone())["id"] == 1
            assert [r["id"] for r in await cur.fetchall()] == [2, 3, 4]
            assert await cur.fetchone() is None

        # fetchmany and async iteration
        async with conn.execute("SELECT id FROM t ORDER BY id") as cur:
            assert len(await cur.fetchmany(2)) == 2
        rows = []
        async with conn.execute("SELECT id FROM t ORDER BY id") as cur:
            async for row in cur:
                rows.append(row["id"])
        assert rows == [1, 2, 3, 4]

        # a DML statement has no description and no rows to drain
        cur = await conn.execute("DELETE FROM t WHERE id = 4")
        assert cur.description is None and await cur.fetchall() == []
    finally:
        await raw.close()


@pytest.mark.asyncio
async def test_both_call_styles_and_delegation():
    sqlite_conn.set_threshold(1000)  # high enough that nothing logs as slow
    raw = await aiosqlite.connect(":memory:")
    try:
        conn = sqlite_conn.wrap(raw, "test")
        conn.row_factory = aiosqlite.Row  # __setattr__ should land on raw
        assert raw.row_factory is aiosqlite.Row

        await conn.executescript("CREATE TABLE t (id INTEGER, v TEXT);")
        await conn.executemany(
            "INSERT INTO t (id, v) VALUES (?, ?)", [(1, "a"), (2, "b")]
        )
        await conn.commit()  # commit delegates via __getattr__

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
async def test_falls_back_when_aiosqlite_internals_are_missing():
    """A driver without aiosqlite's private hooks still works (two-hop path)."""

    class _Hidden:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, parameters=None):
            return self._inner.execute(sql, parameters or [])

        def executemany(self, sql, parameters):
            return self._inner.executemany(sql, parameters)

        async def executescript(self, script):
            return await self._inner.executescript(script)

        async def commit(self):
            await self._inner.commit()

    raw = await aiosqlite.connect(":memory:")
    try:
        conn = sqlite_conn.wrap(_Hidden(raw), "fallback")
        await conn.executescript("CREATE TABLE t (id INTEGER, v TEXT);")
        await conn.execute("INSERT INTO t (id, v) VALUES (1, 'a')")
        await conn.commit()
        async with conn.execute("SELECT v FROM t") as cur:
            assert (await cur.fetchone())[0] == "a"
    finally:
        await raw.close()


@pytest.mark.asyncio
async def test_slow_query_is_logged(caplog):
    sqlite_conn.set_threshold(0.0001)  # tiny -> any query counts as "slow"
    raw = await aiosqlite.connect(":memory:")
    try:
        conn = sqlite_conn.wrap(raw, "unit")
        with caplog.at_level(logging.WARNING, logger="NanoBot.sqlite"):
            await conn.execute("SELECT 1")
        assert any(
            "slow query [unit]" in r.message for r in caplog.records
        ), caplog.records
    finally:
        await raw.close()
