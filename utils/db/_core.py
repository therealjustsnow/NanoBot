"""utils/db/_core.py — shared SQLite connection state + schema machinery.

Holds the single module-level connection, the slow-query wrapper, the schema
helpers (_ensure_columns), the versioned-migration registry, and init()/close().
Domain modules (tags, economy, music, …) import _conn / _ensure_columns /
register_init from here; init() runs every registered table-setup in the order
the package __init__ imports the domains, then applies pending migrations.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable  # noqa: F401 - re-exported for type hints

import aiosqlite

from utils import db_crypto, sqlite_conn

from . import _cache, _ttl

log = logging.getLogger("NanoBot.db")

_DB_PATH = os.path.join("data", "nanobot.db")

# Module-level connection — opened once in init(), shared for the bot's lifetime.
_db: aiosqlite.Connection | None = None

# Whether the live SQLite build understands "… RETURNING <cols>" (3.35+). Probed
# once in init() against the connection actually in use, since the SQLCipher
# driver ships its own SQLite. False falls every accessor back to a plain
# write-then-read.
_RETURNING_OK: bool = False

# Second connection, read-only (PRAGMA query_only), used by _read_conn() for
# leaderboards and other whole-table reads. None until init() opens it — and it
# stays None when the database is in-memory (a test fixture), where a second
# connection would open a *different* empty database.
_reader: aiosqlite.Connection | None = None

# Table-setup callables registered by the domain modules at import time. init()
# awaits them in registration order (which __init__ fixes to the original
# sequence), so creation order — and the interleaved one-time migrations — match
# the pre-split behaviour exactly.
_TABLE_INITS: list[Callable[[], Awaitable[None]]] = []


def register_init(fn: "Callable[[], Awaitable[None]]"):
    """Register a domain's table-setup coroutine to run inside init()."""
    _TABLE_INITS.append(fn)
    return fn


def _conn() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("db.init() has not been called")
    return _db


def _read_conn() -> aiosqlite.Connection:
    """The connection for big read-only queries — leaderboards, ranks, counts.

    aiosqlite drives each connection from one worker thread, so everything in
    the bot queues behind everything else on `_db`. WAL already allows readers
    alongside the writer; this hands the heavy boards their own connection so a
    20k-member server leaderboard can't stall a /fish cast.

    Only for queries that are read-only *and* whole in one statement. A read
    that a write then depends on (get_balance before a debit) must stay on the
    writer: this connection sees committed data only, so it would miss an
    in-flight transaction. Falls back to the writer when there is no reader —
    which is the case in tests, where the fixture injects `_db` directly.
    """
    return _reader if _reader is not None else _conn()


async def init(encryption_key: str | None = None) -> None:
    """Open the database and create all tables. Call once at bot startup.

    When `encryption_key` is set the file is opened through SQLCipher
    (encrypted at rest); see utils/db_crypto.py.
    """
    global _db, _reader, _RETURNING_OK
    os.makedirs("data", exist_ok=True)
    _db = await db_crypto.connect(_DB_PATH, encryption_key)
    _db = sqlite_conn.wrap(_db, "nanobot")

    async with _db.execute("SELECT sqlite_version()") as cur:
        version = (await cur.fetchone())[0]
    _RETURNING_OK = tuple(int(p) for p in str(version).split(".")[:2]) >= (3, 35)
    _cache.clear()

    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA synchronous=NORMAL")
    await _db.execute("PRAGMA busy_timeout=5000")
    await _db.execute("PRAGMA foreign_keys=ON")
    await _db.execute("PRAGMA temp_store=MEMORY")
    await _db.execute("PRAGMA cache_size=-16000")
    await _db.execute("PRAGMA mmap_size=268435456")

    await _db.executescript("""
        CREATE TABLE IF NOT EXISTS tags (
            guild_id  TEXT NOT NULL,
            scope     TEXT NOT NULL,   -- "global" or user_id
            name      TEXT NOT NULL,
            content   TEXT,
            image_url TEXT,
            by_id     TEXT,
            by_name   TEXT,
            PRIMARY KEY (guild_id, scope, name)
        );

        CREATE TABLE IF NOT EXISTS notes (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id  TEXT NOT NULL,
            user_id   TEXT NOT NULL,
            content   TEXT NOT NULL,
            by_id     TEXT NOT NULL,
            by_name   TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS notes_guild_user ON notes (guild_id, user_id);

        CREATE TABLE IF NOT EXISTS prefixes (
            guild_id  TEXT PRIMARY KEY,
            prefix    TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS unban_schedules (
            key       TEXT PRIMARY KEY,   -- "guild_id:user_id"
            guild_id  TEXT NOT NULL,
            user_id   TEXT NOT NULL,
            until     REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS slow_schedules (
            channel_id  TEXT PRIMARY KEY,
            guild_id    TEXT NOT NULL,
            until       REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS reminders (
            id          TEXT PRIMARY KEY,
            target_id   TEXT NOT NULL,
            set_by_id   TEXT NOT NULL,
            guild_id    TEXT NOT NULL,
            channel_id  TEXT NOT NULL,
            message     TEXT NOT NULL,
            due         REAL NOT NULL,
            duration    REAL NOT NULL,
            dm          INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS reminders_target ON reminders (target_id);
        CREATE INDEX IF NOT EXISTS reminders_setter ON reminders (set_by_id);
    """)

    await _db.commit()
    # Domain table-setup, in the order __init__ imported the modules (matches the
    # original hard-coded sequence), then forward-only migrations.
    for _setup in _TABLE_INITS:
        await _setup()
    await _run_migrations()
    # Migrations can rewrite config rows (migration 3 does), so start the
    # cache from whatever the schema settled on.
    _cache.clear()
    _board_cache.clear()
    _rank_cache.clear()
    _reader = await _open_reader(encryption_key)
    log.info(f"Database ready: {_DB_PATH}")


async def fetch_one_returning(sql: str, params, *, returning: str):
    """Run a write with a RETURNING clause and hand back the resulting row.

    The pattern this replaces is "upsert, commit, then SELECT the value back" —
    a second round trip to the aiosqlite thread for a number the write already
    knew. RETURNING needs SQLite 3.35+; on an older driver this returns None and
    the caller falls back to its own read, so the accessors stay correct either
    way (see `_RETURNING_OK`).
    """
    if not _RETURNING_OK:
        await _conn().execute(sql, params)
        await _commit()
        return None
    # The connection proxy (utils/sqlite_conn.py) fetches and closes this
    # statement in the same worker-thread hop as the write. That matters here
    # more than anywhere else: a DML statement with RETURNING is a *writer*
    # that returns rows, and SQLite refuses to commit while one is still
    # active — so leaving it open across an await used to abort whatever other
    # coroutine committed next ("cannot commit transaction - SQL statements in
    # progress").
    async with _conn().execute(f"{sql} RETURNING {returning}", params) as cur:
        row = await cur.fetchone()
    await _commit()
    return row


# ── Transactions ─────────────────────────────────────────────────────────────
# The package shares ONE writer connection between every coroutine, so a naive
# "BEGIN … COMMIT" around a multi-step flow is unsafe: another coroutine's
# accessor can execute — and commit — inside the window, landing its work in
# your transaction and losing it to your rollback. So a transaction takes a
# process-wide write lock for its (short, HTTP-free) duration, and the inner
# accessors' own commits become no-ops until the outermost one finishes.
_tx_lock = asyncio.Lock()
_tx_depth = 0


async def _commit() -> None:
    """Commit — unless a transaction() block owns the connection right now.

    Every accessor in the package calls this instead of `_conn().commit()`, so
    a multi-step flow can be made genuinely atomic by wrapping it, without
    rewriting the accessors it calls.
    """
    if _tx_depth == 0:
        await _conn().commit()


@asynccontextmanager
async def transaction():
    """Run a block as one atomic unit: all of its writes land, or none do.

    Use for the handful of flows where a partial result is a real problem — a
    transfer that debits one wallet and credits another, a purchase that
    reserves stock, charges coins and writes a ledger row. Keep the body short
    and free of Discord calls: it holds the writer for its whole duration.

    Nested use is allowed; only the outermost block commits.

    Known limit: the lock only excludes other transaction() blocks. A plain
    accessor that writes while one is open still lands inside it, and would be
    undone by a rollback. That is why this is reserved for flows whose rollback
    path is a *crash* rather than an expected outcome — `purchase_item`, whose
    "out of stock" and "not enough coins" branches are ordinary, uses ordered
    compensating writes instead.
    """
    global _tx_depth
    if _tx_depth > 0:  # already inside one — join it
        _tx_depth += 1
        try:
            yield
        finally:
            _tx_depth -= 1
        return
    async with _tx_lock:
        await _conn().commit()  # flush anything an earlier accessor left open
        # Claim the depth *before* the BEGIN and hold it *past* the COMMIT.
        # Both are awaits, so another coroutine's accessor can run inside them;
        # with the depth already claimed its _commit() is a no-op instead of
        # committing this block's transaction out from under it.
        _tx_depth = 1
        try:
            await _conn().execute("BEGIN IMMEDIATE")
            yield
        except BaseException:
            await _conn().rollback()
            _tx_depth = 0
            raise
        else:
            await _conn().commit()
            _tx_depth = 0


async def _open_reader(encryption_key: str | None):
    """Open the read-only companion connection, or None if it isn't possible.

    Never fatal: the reader is an optimisation, and every caller falls back to
    the writer without it.
    """
    if ":memory:" in _DB_PATH:
        return None
    try:
        reader = await db_crypto.connect(_DB_PATH, encryption_key)
        reader = sqlite_conn.wrap(reader, "nanobot-ro")
        for pragma in (
            "PRAGMA busy_timeout=5000",
            "PRAGMA temp_store=MEMORY",
            "PRAGMA cache_size=-16000",
            "PRAGMA mmap_size=268435456",
            # Belt and braces: this connection must never write, and SQLite
            # will refuse rather than let a stray statement through.
            "PRAGMA query_only=ON",
        ):
            await reader.execute(pragma)
        return reader
    except Exception:
        log.warning("Read-only connection unavailable; boards will use the writer")
        return None


async def close() -> None:
    """Close the database connections cleanly."""
    global _db, _reader
    if _reader:
        await _reader.close()
        _reader = None
    if _db:
        await _db.close()
        _db = None


# Bound-parameter budget for one "… WHERE user_id IN (…)" query. SQLite's
# modern default cap is 32k, older builds 999 — 900 is safe everywhere and the
# chunk loop makes the caller's guild size irrelevant.
_IN_CHUNK = 900


# Server-scoped boards are the one read whose cost scales with guild size: a
# 20k-member guild fans out to 23 chunked queries and materialises every row,
# and a page-turn re-runs the lot. Measured at ~45ms idle and ~185ms while casts
# are streaming through the writer — so the result is memoised for a few
# seconds. Boards are a snapshot either way; this just stops the snapshot being
# recomputed for every page button. Never used for anything that gates a
# decision (see _ttl.py).
_BOARD_TTL = 30.0
_board_cache = _ttl.TTLCache(maxsize=32, ttl=_BOARD_TTL)


# "What rank am I?" is a COUNT of everyone ahead of you. It's an index range
# scan, so it costs nothing for the leaders and grows linearly for everyone
# else: measured at 1.3ms for a low balance across 50k wallets, 7ms at 250k and
# 31ms at 1M — and /balance asks twice (coins and contribution). Memoising by
# *value* keeps the displayed number itself exact — a member whose balance just
# changed misses the cache and gets a fresh count — while repeat views, other
# people's profiles and page-turns stop re-counting the same figure.
_RANK_TTL = 60.0
_rank_cache = _ttl.TTLCache(maxsize=512, ttl=_RANK_TTL)


async def count_ahead(sql: str, value) -> int:
    """Run a "COUNT(*) … > ?" ranking query, memoised on `value`.

    `sql` is a code-side constant with one placeholder; it runs on the reader.
    """
    key = (sql, value)
    cached = _rank_cache.get(key)
    if cached is not None:
        return cached
    async with _read_conn().execute(sql, (value,)) as cur:
        ahead = (await cur.fetchone())[0]
    return _rank_cache.put(key, int(ahead))


async def rows_for_users(base_sql: str, user_ids, *, positive_col: str | None = None):
    """Run `base_sql` restricted to `user_ids`, chunking the IN clause.

    The economy tables are global (one row per user), so a *server*-scoped view
    of one — a guild leaderboard, say — is the global table filtered to that
    guild's members. `base_sql` is a complete SELECT with no WHERE clause of its
    own; `positive_col` optionally drops zero rows. Both are code-side constants,
    never caller input. Rows come back unordered and unpaged: the caller sorts
    and slices, since the result is bounded by the guild's member list.
    """
    ids = [str(u) for u in dict.fromkeys(user_ids)]
    key = (base_sql, positive_col, tuple(ids))
    cached = _board_cache.get(key)
    if cached is not None:
        return list(cached)
    rows: list = []
    for start in range(0, len(ids), _IN_CHUNK):
        chunk = ids[start : start + _IN_CHUNK]
        sql = f"{base_sql} WHERE user_id IN ({','.join('?' * len(chunk))})"
        if positive_col:
            sql += f" AND {positive_col} > 0"
        async with _read_conn().execute(sql, chunk) as cur:
            rows.extend(await cur.fetchall())
    _board_cache.put(key, rows)
    return list(rows)


async def _ensure_columns(table: str, columns: dict[str, str]) -> None:
    """Idempotently add any missing columns to *table*.

    `columns` maps a column name to its ALTER TABLE ADD COLUMN definition (type
    plus any constraints/default). The existing column list is checked rather
    than swallowing errors, so a locked or corrupt DB surfaces instead of being
    silently skipped.
    """
    async with _conn().execute(f"PRAGMA table_info({table})") as cur:
        existing = {row["name"] for row in await cur.fetchall()}
    added = False
    for col, definition in columns.items():
        if col not in existing:
            await _conn().execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
            added = True
    if added:
        await _conn().commit()


# Ordered, forward-only schema migrations keyed by version. PRAGMA user_version
# records the highest applied migration; on startup any with a higher number run
# in ascending order, and user_version is bumped only after a migration's
# callable succeeds — so a failure leaves the version unchanged and the migration
# is retried on the next start (write each one to be safe to re-run). The CREATE
# TABLE / _ensure_columns calls in init() are the version-0 baseline; add NEW
# schema changes here as @migration(N) instead of scattering ad-hoc ALTERs.
_MIGRATIONS: list[tuple[int, Callable[[aiosqlite.Connection], Awaitable[None]]]] = []


def migration(version: int):
    """Register a schema migration to run when the DB is below *version*."""

    def decorator(fn):
        _MIGRATIONS.append((version, fn))
        return fn

    return decorator


async def _run_migrations(migrations=None) -> None:
    """Apply every registered migration whose version exceeds user_version."""
    migrations = _MIGRATIONS if migrations is None else migrations
    async with _conn().execute("PRAGMA user_version") as cur:
        row = await cur.fetchone()
    current = row[0] if row else 0

    for version, fn in sorted(migrations):
        if version <= current:
            continue
        log.info("Applying DB migration %d (%s)", version, fn.__name__)
        try:
            await fn(_conn())
            # PRAGMA doesn't accept bound params; version is an int we control.
            await _conn().execute(f"PRAGMA user_version = {int(version)}")
            await _conn().commit()
        except Exception:
            await _conn().rollback()
            log.error("DB migration %d failed — rolled back", version)
            raise
