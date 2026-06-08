"""
utils/sqlite_timing.py
Optional slow-query logging for the aiosqlite connections.

Wraps a connection so every execute()/executemany() is timed; any that takes
longer than ``SLOW_QUERY_MS`` is logged with its duration and a trimmed SQL
snippet. This is the cheap way to *see* whether the single shared connection is
ever a bottleneck — point a read pool at the problem only once the logs prove
one exists, instead of guessing.

Disabled by default (SLOW_QUERY_MS = 0): when off, execute() returns the native
aiosqlite object untouched, so there is genuinely zero added overhead. Set the
threshold from config at startup via ``set_threshold``.

The wrapper preserves both ways aiosqlite's execute is used:
    cur = await conn.execute(sql, params)
    async with conn.execute(sql, params) as cur: ...
and delegates everything else (commit, close, executescript, row_factory reads,
…) straight to the wrapped connection.
"""

from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger("NanoBot.sqlite")

# Queries slower than this (milliseconds) are logged. 0 (or less) disables the
# whole mechanism — set it from config with set_threshold().
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


def wrap(conn: Any, label: str) -> "_TimedConnection":
    """Wrap an aiosqlite connection so its queries are timed when enabled."""
    return _TimedConnection(conn, label)


class _TimedExecute:
    """Awaitable + async-context-manager that times one execute call."""

    __slots__ = ("_conn", "_label", "_sql", "_params", "_many", "_cursor")

    def __init__(self, conn, label, sql, params, many=False):
        self._conn = conn
        self._label = label
        self._sql = sql
        self._params = params
        self._many = many
        self._cursor = None

    async def _run(self):
        t0 = time.perf_counter()
        if self._many:
            cur = await self._conn.executemany(self._sql, self._params)
        elif self._params is None:
            cur = await self._conn.execute(self._sql)
        else:
            cur = await self._conn.execute(self._sql, self._params)
        dur_ms = (time.perf_counter() - t0) * 1000
        if dur_ms >= SLOW_QUERY_MS:
            log.warning(
                "slow query [%s] %.0fms: %s", self._label, dur_ms, _short(self._sql)
            )
        return cur

    def __await__(self):
        return self._run().__await__()

    async def __aenter__(self):
        self._cursor = await self._run()
        return self._cursor

    async def __aexit__(self, *exc):
        if self._cursor is not None:
            await self._cursor.close()
            self._cursor = None
        return False


class _TimedConnection:
    """Thin proxy that times execute/executemany and delegates the rest."""

    def __init__(self, conn, label):
        # Bypass __getattr__/delegation for our own attributes.
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_label", label)

    def execute(self, sql, parameters=None):
        if SLOW_QUERY_MS <= 0:
            # Disabled — hand back the native object, zero added overhead.
            if parameters is None:
                return self._conn.execute(sql)
            return self._conn.execute(sql, parameters)
        return _TimedExecute(self._conn, self._label, sql, parameters)

    def executemany(self, sql, parameters):
        if SLOW_QUERY_MS <= 0:
            return self._conn.executemany(sql, parameters)
        return _TimedExecute(self._conn, self._label, sql, parameters, many=True)

    def __getattr__(self, name):
        # commit, close, executescript, rollback, in_transaction, … all pass
        # straight through to the real connection.
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        # e.g. row_factory = ... should land on the wrapped connection.
        setattr(self._conn, name, value)
