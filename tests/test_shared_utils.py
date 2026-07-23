"""
Tests for the shared economy utilities in utils/helpers.py added by the
cleanup pass: fmt_coins, weighted_pick, and KeyedLocks. These lock in that
the consolidation preserved the exact behavior of the per-cog code it
replaced.
"""

import asyncio

from utils.helpers import KeyedLocks, fmt_coins, weighted_pick


# ── fmt_coins ──────────────────────────────────────────────────────────────────
def test_fmt_coins_plural_singular_negative():
    assert fmt_coins(1234, "NanoCoin", "🪙") == "🪙 **1,234** NanoCoins"
    assert fmt_coins(1, "NanoCoin", "🪙") == "🪙 **1** NanoCoin"
    assert fmt_coins(-1, "NanoCoin", "🪙") == "🪙 **-1** NanoCoin"
    assert fmt_coins(0, "NanoCoin", "🪙") == "🪙 **0** NanoCoins"


def test_economy_helpers_reexport_is_shared_impl():
    from cogs.economy import helpers as econ_helpers

    assert econ_helpers.fmt_coins(2, "Coin", "x") == fmt_coins(2, "Coin", "x")


# ── weighted_pick ──────────────────────────────────────────────────────────────
_TABLE = [("a", 0.5), ("b", 0.3), ("c", 0.2)]


def test_weighted_pick_buckets():
    assert weighted_pick(_TABLE, 0.0) == "a"
    assert weighted_pick(_TABLE, 0.499) == "a"
    # Strict `roll < acc`: a boundary roll falls into the NEXT bucket —
    # identical to the per-cog loops this replaced.
    assert weighted_pick(_TABLE, 0.5) == "b"
    assert weighted_pick(_TABLE, 0.799) == "b"
    assert weighted_pick(_TABLE, 0.8) == "c"
    assert weighted_pick(_TABLE, 0.999) == "c"


def test_weighted_pick_last_key_soaks_rolloff():
    # Weights summing below 1.0 (float round-off) fall through to the last key.
    assert weighted_pick([("a", 0.3), ("b", 0.3)], 0.99) == "b"


def test_weighted_pick_matches_replaced_loop_exhaustively():
    def old_loop(table, roll):
        acc = 0.0
        for key, p in table:
            acc += p
            if roll < acc:
                return key
        return table[-1][0]

    for i in range(1000):
        roll = i / 1000
        assert weighted_pick(_TABLE, roll) == old_loop(_TABLE, roll)


# ── KeyedLocks ─────────────────────────────────────────────────────────────────
async def test_keyed_locks_mutual_exclusion():
    locks = KeyedLocks()
    order = []

    async def worker(tag):
        async with locks.hold("k"):
            order.append(f"{tag}-in")
            await asyncio.sleep(0.01)
            order.append(f"{tag}-out")

    await asyncio.gather(worker("a"), worker("b"))
    # Critical sections never interleave.
    assert order in (
        ["a-in", "a-out", "b-in", "b-out"],
        ["b-in", "b-out", "a-in", "a-out"],
    )


async def test_keyed_locks_entry_freed_when_idle():
    locks = KeyedLocks()
    async with locks.hold((1, 2)):
        assert len(locks) == 1
    assert len(locks) == 0  # released and unreferenced → pruned


async def test_keyed_locks_not_freed_while_waiter_exists():
    locks = KeyedLocks()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder():
        async with locks.hold("k"):
            entered.set()
            await release.wait()

    async def waiter():
        async with locks.hold("k"):
            pass

    h_task = asyncio.create_task(holder())
    await entered.wait()
    w_task = asyncio.create_task(waiter())
    await asyncio.sleep(0)  # let the waiter reach acquire()
    assert len(locks) == 1  # entry pinned by holder + waiter
    release.set()
    await asyncio.gather(h_task, w_task)
    assert len(locks) == 0  # last releaser pruned it


async def test_keyed_locks_independent_keys_dont_block():
    locks = KeyedLocks()
    async with locks.hold("a"):
        # A different key must be acquirable immediately (no global contention).
        async with locks.hold("b"):
            assert len(locks) == 2
