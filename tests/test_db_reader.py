"""
Tests for the read-only companion connection (utils/db/_core._read_conn).

aiosqlite drives one connection from one worker thread, so every query in the
bot queues behind every other one. WAL already permits a reader alongside the
writer, so the heavy boards — a server leaderboard fans out to ceil(members/900)
queries and materialises the lot — get their own connection instead of stalling
the write path.

The contract worth protecting: the reader exists on a real file database, it
cannot write, it sees committed data, and everything still works when it isn't
there (which is how every other test in this suite runs).
"""

import aiosqlite
import pytest

import utils.db as db
from utils.db import _core


@pytest.fixture
async def file_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "nanobot.db"))
    await db.init()
    yield
    await db.close()


async def test_reader_is_opened_for_a_file_database(file_db):
    assert _core._reader is not None
    assert _core._read_conn() is _core._reader
    assert _core._read_conn() is not _core._conn()


async def test_reader_refuses_writes(file_db):
    """PRAGMA query_only means a stray write fails loudly instead of silently
    landing on the wrong connection."""
    with pytest.raises(aiosqlite.OperationalError):
        await _core._reader.execute(
            "INSERT INTO economy (user_id, coins) VALUES ('1',1)"
        )


async def test_boards_read_committed_writes_through_the_reader(file_db):
    await db.add_coins(1, 500)
    await db.add_coins(2, 100)

    board = await db.get_econ_leaderboard(10, 0)
    assert [row["user_id"] for row in board] == [1, 2]
    assert await db.count_econ() == 2
    assert await db.get_econ_rank(2) == (2, 100)
    # The server-scoped path (chunked IN) rides the reader too.
    assert len(await db.get_econ_leaderboard_for([1, 2])) == 2


async def test_close_releases_both_connections(file_db):
    await db.close()
    assert _core._reader is None
    with pytest.raises(RuntimeError):
        _core._conn()
    # A second close is harmless (the fixture will call it again).


async def test_read_conn_falls_back_to_the_writer_without_a_reader(monkeypatch):
    """Every other test injects an in-memory connection with no reader — that
    path has to keep working, so the fallback is the tested default."""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    monkeypatch.setattr(db, "_db", conn)
    monkeypatch.setattr(db, "_reader", None)
    try:
        await db._ensure_economy_tables()
        await db.add_coins(7, 42)
        assert _core._read_conn() is conn
        assert await db.count_econ() == 1
    finally:
        await conn.close()


async def test_in_memory_path_never_opens_a_reader(monkeypatch):
    """A second connection to :memory: would be a different, empty database —
    so init() must not open one."""
    monkeypatch.setattr(db, "_DB_PATH", ":memory:")
    assert await _core._open_reader(None) is None


# ── Board memo ───────────────────────────────────────────────────────────────
async def test_server_board_is_memoised_briefly(file_db):
    """A page-turn shouldn't re-run ceil(members/900) queries and re-materialise
    every row — the board is a snapshot either way."""
    await db.add_coins(1, 500)
    first = await db.get_econ_leaderboard_for([1, 2])
    await db.add_coins(2, 900)  # lands after the snapshot
    again = await db.get_econ_leaderboard_for([1, 2])
    assert [r["user_id"] for r in again] == [r["user_id"] for r in first]

    _core._board_cache.clear()
    fresh = await db.get_econ_leaderboard_for([1, 2])
    assert len(fresh) == 2


async def test_board_memo_expires(file_db, monkeypatch):
    await db.add_coins(1, 500)
    await db.get_econ_leaderboard_for([1, 2])
    monkeypatch.setattr(_core._board_cache, "ttl", -1)  # everything is stale
    await db.add_coins(2, 900)
    assert len(await db.get_econ_leaderboard_for([1, 2])) == 2


async def test_board_memo_keys_on_the_member_set(file_db):
    await db.add_coins(1, 500)
    await db.add_coins(2, 900)
    assert len(await db.get_econ_leaderboard_for([1])) == 1
    assert len(await db.get_econ_leaderboard_for([1, 2])) == 2


async def test_board_memo_hands_back_a_private_list(file_db):
    """A caller sorting or slicing its rows must not mutate the cached copy."""
    await db.add_coins(1, 500)
    rows = await db.get_econ_leaderboard_for([1])
    rows.clear()
    assert len(await db.get_econ_leaderboard_for([1])) == 1


# ── Transactions ─────────────────────────────────────────────────────────────
async def test_transaction_commits_everything_or_nothing(file_db):
    await db.add_coins(1, 100)

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        async with _core.transaction():
            await db.add_coins(1, 900)
            await db.add_coins(2, 900)
            raise Boom
    assert (await db.get_balance(1), await db.get_balance(2)) == (100, 0)

    async with _core.transaction():
        await db.add_coins(1, 900)
        await db.add_coins(2, 900)
    assert (await db.get_balance(1), await db.get_balance(2)) == (1000, 900)


async def test_inner_commits_are_deferred_to_the_outermost_block(file_db):
    async with _core.transaction():
        await db.add_coins(1, 50)  # its own _commit() is a no-op in here
        assert _core._tx_depth == 1
        async with _core.transaction():  # nesting joins, doesn't re-BEGIN
            assert _core._tx_depth == 2
            await db.add_coins(1, 50)
    assert _core._tx_depth == 0
    assert await db.get_balance(1) == 100


async def test_transaction_depth_resets_after_a_failure(file_db):
    with pytest.raises(ValueError):
        async with _core.transaction():
            raise ValueError
    assert _core._tx_depth == 0
    await db.add_coins(1, 5)  # the connection is usable again
    assert await db.get_balance(1) == 5


# ── Rank memo ────────────────────────────────────────────────────────────────
async def test_rank_memo_keys_on_the_value_so_the_figure_stays_exact(file_db):
    """Ranking is a COUNT of everyone ahead — cheap at the top, linear at the
    bottom (31ms across 1M wallets). Memoised by value, so a member whose
    balance just moved gets a fresh count instead of a stale one."""
    await db.add_coins(1, 500)
    await db.add_coins(2, 100)

    assert await db.get_econ_rank(2) == (2, 100)
    before = _core._rank_cache.hits
    assert await db.get_econ_rank(2) == (2, 100)  # same value → memo
    assert _core._rank_cache.hits == before + 1

    await db.add_coins(2, 900)  # now richer than user 1
    assert await db.get_econ_rank(2) == (1, 1000)  # new value → fresh count


async def test_rank_memo_expires(file_db, monkeypatch):
    await db.add_coins(1, 100)
    assert await db.get_econ_rank(1) == (1, 100)
    monkeypatch.setattr(_core._rank_cache, "ttl", -1)
    misses = _core._rank_cache.misses
    assert await db.get_econ_rank(1) == (1, 100)
    assert _core._rank_cache.misses > misses
