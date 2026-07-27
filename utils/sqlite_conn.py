"""
utils/sqlite_conn.py
The proxy both SQLite connections are opened behind.

Every coroutine in the bot shares ONE aiosqlite connection per database, and
aiosqlite drives it from a single worker thread. That makes two properties of
this proxy matter:

1. **One query is one hop.** The native `conn.execute(...)` hands back a cursor
   whose SQLite statement is still *active*; the rows only arrive on a second
   await (`fetchone`/`fetchall`). Between those two awaits the event loop is
   free to run another coroutine on the same connection — and if that coroutine
   commits while a **write** statement is mid-flight, SQLite refuses:

       OperationalError: cannot commit transaction - SQL statements in progress

   A plain SELECT is harmless (SQLite only counts pending *writers*), but a DML
   statement with a RETURNING clause is a writer that returns rows, so
   `utils/db/_core.fetch_one_returning` left exactly that window open on every
   `add_coins`/`add_item`/`add_xp` — and any concurrent commit (a /fish cast,
   say) blew up. So this proxy runs execute + fetch + close as ONE callable on
   the connection's worker thread and returns a `Result` holding the rows in
   memory: no statement is ever active across an await, and the whole class of
   bug goes away without touching the ~250 call sites. It is also one round
   trip fewer per read than the native two-step.

2. **Slow-query logging.** Any query slower than ``SLOW_QUERY_MS`` is logged
   with its duration and a trimmed SQL snippet — the cheap way to *see*
   whether the shared connection is a bottleneck instead of guessing. Off by
   default (0); set it from config at startup with ``set_threshold``.

`Result` is a drop-in for the aiosqlite cursor as this codebase uses it:
``await cur.fetchone()``, ``await cur.fetchall()``, ``cur.rowcount``,
``cur.lastrowid``, iteration, and ``await cur.close()``. Both call styles keep
working, so no accessor had to change:

    cur = await conn.execute(sql, params)
    async with conn.execute(sql, params) as cur: ...

Everything else (commit, rollback, close, executescript, row_factory, …)
delegates straight to the wrapped connection.
"""

from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger("NanoBot.sqlite")

# Queries slower than this (milliseconds) are logged. 0 (or less) disables the
# logging — set it from config with set_threshold(). The one-hop execution in
# this module is not optional and is unaffected by the threshold.
SLOW_QUERY_MS: float = 0.0


def set_threshold(ms: float | int | None) -> None:
    """Set the slow-query log threshold in milliseconds. 0/None disables it."""
    global SLOW_QUERY_MS
    try:
        SLOW_QUERY_MS = max(0.0, float(ms or 0))
    except (TypeError, ValueError):
        SLOW_QUERY_MS = 0.0


def enabled() -> bool:
    return SLOW_QUERY_MS > 0


def _short(sql: str, limit: int = 120) -> str:
    """Collapse whitespace and trim a SQL string for a one-line log entry."""
    flat = " ".join(str(sql).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def wrap(conn: Any, label: str) -> "_ProxyConnection":
    """Wrap an aiosqlite connection so its queries run in one atomic hop."""
    return _ProxyConnection(conn, label)


class Result:
    """A finished query: rows already fetched, statement already closed.

    Deliberately dumb — it is a snapshot, not a live cursor. `rowcount` and
    `lastrowid` are read off the real cursor before it is closed, which is when
    SQLite has them; `description` is kept so callers can tell a row-returning
    statement from a DML one.
    """

    __slots__ = ("_rows", "_pos", "rowcount", "lastrowid", "description")

    def __init__(self, rows, rowcount: int, lastrowid, description):
        self._rows = rows
        self._pos = 0
        self.rowcount = rowcount
        self.lastrowid = lastrowid
        self.description = description

    async def fetchone(self):
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    async def fetchmany(self, size: int = 1):
        rows = self._rows[self._pos : self._pos + max(0, int(size))]
        self._pos += len(rows)
        return list(rows)

    async def fetchall(self):
        rows = self._rows[self._pos :]
        self._pos = len(self._rows)
        return list(rows)

    async def close(self) -> None:
        # Nothing to release — the statement was closed on the worker thread
        # before this object existed. Kept so the aiosqlite cursor API holds.
        self._rows = []
        self._pos = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        row = await self.fetchone()
        if row is None:
            raise StopAsyncIteration
        return row

    async def __aenter__(self) -> "Result":
        return self

    async def __aexit__(self, *exc) -> bool:
        await self.close()
        return False


def _run_query(raw, sql, params, many: bool):
    """Execute, drain, and close — all inside the connection's worker thread.

    This is the whole point of the module: it runs as one unit, so no statement
    is left active for another coroutine's commit to trip over. Returns a plain
    tuple because it crosses the thread boundary.
    """
    if many:
        cur = raw.executemany(sql, params)
    elif params is None:
        cur = raw.execute(sql)
    else:
        cur = raw.execute(sql, params)
    try:
        # DML without RETURNING has no description and no rows to drain.
        rows = cur.fetchall() if cur.description is not None else []
        return rows, cur.rowcount, cur.lastrowid, cur.description
    finally:
        cur.close()


async def _dispatch(conn, sql, params, many: bool) -> Result:
    """Run one query on `conn`, preferring the single-hop path."""
    execute = getattr(conn, "_execute", None)
    try:
        raw = conn._conn  # aiosqlite's underlying sqlite3/sqlcipher3 connection
    except Exception:
        raw = None
    if execute is None or raw is None:
        return await _dispatch_fallback(conn, sql, params, many)
    rows, rowcount, lastrowid, description = await execute(
        _run_query, raw, sql, params, many
    )
    return Result(rows, rowcount, lastrowid, description)


async def _dispatch_fallback(conn, sql, params, many: bool) -> Result:
    """Two-hop path for a driver that doesn't expose aiosqlite's internals.

    Correct but not race-free — it is only here so an aiosqlite change breaks
    the atomicity guarantee rather than the bot.
    """
    if many:
        cur = await conn.executemany(sql, params)
    elif params is None:
        cur = await conn.execute(sql)
    else:
        cur = await conn.execute(sql, params)
    try:
        rows = await cur.fetchall() if cur.description is not None else []
        return Result(rows, cur.rowcount, cur.lastrowid, cur.description)
    finally:
        await cur.close()


class _Execute:
    """Awaitable + async-context-manager wrapping one query."""

    __slots__ = ("_conn", "_label", "_sql", "_params", "_many", "_result")

    def __init__(self, conn, label, sql, params, many: bool = False):
        self._conn = conn
        self._label = label
        self._sql = sql
        self._params = params
        self._many = many
        self._result: Result | None = None

    async def _run(self) -> Result:
        t0 = time.perf_counter()
        result = await _dispatch(self._conn, self._sql, self._params, self._many)
        if SLOW_QUERY_MS > 0:
            dur_ms = (time.perf_counter() - t0) * 1000
            if dur_ms >= SLOW_QUERY_MS:
                log.warning(
                    "slow query [%s] %.0fms: %s",
                    self._label,
                    dur_ms,
                    _short(self._sql),
                )
        return result

    def __await__(self):
        return self._run().__await__()

    async def __aenter__(self) -> Result:
        self._result = await self._run()
        return self._result

    async def __aexit__(self, *exc) -> bool:
        if self._result is not None:
            await self._result.close()
            self._result = None
        return False


class _ProxyConnection:
    """Thin proxy that runs execute/executemany in one hop, delegates the rest."""

    def __init__(self, conn, label):
        # Bypass __getattr__/delegation for our own attributes.
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_label", label)

    def execute(self, sql, parameters=None) -> _Execute:
        return _Execute(self._conn, self._label, sql, parameters)

    def executemany(self, sql, parameters) -> _Execute:
        return _Execute(self._conn, self._label, sql, parameters, many=True)

    async def executescript(self, sql_script):
        """Run a multi-statement script and close its cursor.

        aiosqlite hands back a cursor here too; leaving it open would reopen the
        very window this module exists to close.
        """
        cur = await self._conn.executescript(sql_script)
        await cur.close()
        return None

    def __getattr__(self, name):
        # commit, rollback, close, in_transaction, row_factory reads, … all pass
        # straight through to the real connection.
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        # e.g. row_factory = ... should land on the wrapped connection.
        setattr(self._conn, name, value)
