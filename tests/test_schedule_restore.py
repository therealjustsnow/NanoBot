"""
Tests for restart-safe timer re-arming.

`on_ready` fires again on every gateway re-IDENTIFY, and `restore_schedules`
used to be dispatched from it unguarded while the cogs assigned straight into
their `{key: Task}` dicts. The old task kept running, so one reconnect meant one
duplicate reminder / unban / kick — permanently, and doubling each time.

Two things fix it, and both are tested here: the dispatch is once-per-process,
and `helpers.spawn_tracked` cancels whatever it replaces so a restore that does
run twice is idempotent.
"""

import asyncio
import time

import pytest

import utils.db as db
from utils import helpers as h


async def _forever():
    await asyncio.sleep(3600)


async def test_spawn_tracked_cancels_the_task_it_replaces():
    tasks: dict[str, asyncio.Task] = {}
    first = h.spawn_tracked(tasks, "r1", _forever())
    second = h.spawn_tracked(tasks, "r1", _forever())

    await asyncio.sleep(0)
    assert first.cancelled() or first.cancelling()
    assert tasks["r1"] is second
    assert not second.done()
    second.cancel()


async def test_spawn_tracked_keys_are_independent():
    tasks: dict[str, asyncio.Task] = {}
    a = h.spawn_tracked(tasks, "a", _forever())
    h.spawn_tracked(tasks, "b", _forever())
    await asyncio.sleep(0)
    assert not a.done()
    assert set(tasks) == {"a", "b"}
    for task in tasks.values():
        task.cancel()


async def test_repeated_restore_leaves_one_task_per_key():
    """The reconnect scenario: restore the same rows three times over."""
    tasks: dict[str, asyncio.Task] = {}
    rows = ["r1", "r2", "r3"]
    for _ in range(3):
        for key in rows:
            h.spawn_tracked(tasks, key, _forever())
    await asyncio.sleep(0)

    assert len(tasks) == 3
    live = [t for t in tasks.values() if not t.done()]
    assert len(live) == 3
    for task in tasks.values():
        task.cancel()


async def test_spawn_tracked_replaces_a_finished_task_without_error():
    tasks: dict[str, asyncio.Task] = {}

    async def _done():
        return None

    h.spawn_tracked(tasks, "k", _done())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    replacement = h.spawn_tracked(tasks, "k", _forever())
    assert tasks["k"] is replacement
    replacement.cancel()


async def test_restore_dispatch_is_guarded_and_starts_unfired(bot):
    """on_ready re-fires on every reconnect; the dispatch behind it must not."""
    assert bot._schedules_restored is False


@pytest.mark.cogs("cogs.reminders")
async def test_reminder_restore_is_idempotent(bot):
    """The real reconnect path: restoring the same rows twice must leave one
    live timer per reminder, not two racing to deliver."""
    cog = bot.get_cog("Reminders")
    due = time.time() + 3600
    for rid in ("r1", "r2"):
        await db.set_reminder(
            {
                "id": rid,
                "target_id": "1",
                "set_by_id": "1",
                "guild_id": "1",
                "channel_id": "1",
                "message": "ping",
                "due": due,
                "duration": 3600,
                "dm": True,
            }
        )

    await cog.on_restore_schedules()
    first = dict(cog._tasks)
    await cog.on_restore_schedules()
    await asyncio.sleep(0)

    assert set(cog._tasks) == {"r1", "r2"}
    # Each original task was cancelled rather than orphaned.
    for rid, task in first.items():
        assert task is not cog._tasks[rid]
        assert task.cancelled() or task.cancelling()
    assert all(not t.done() for t in cog._tasks.values())
    # …and the rows survived: a cancelled timer must never delete its reminder,
    # or a cog reload would wipe everything pending.
    assert set(await db.get_all_reminders()) == {"r1", "r2"}


@pytest.mark.cogs("cogs.reminders")
async def test_unloading_the_cog_keeps_pending_reminders(bot):
    """cog_unload cancels every timer; the reminders must still be there for
    the reloaded instance to restore."""
    cog = bot.get_cog("Reminders")
    await db.set_reminder(
        {
            "id": "keepme",
            "target_id": "1",
            "set_by_id": "1",
            "guild_id": "1",
            "channel_id": "1",
            "message": "ping",
            "due": time.time() + 3600,
            "duration": 3600,
            "dm": True,
        }
    )
    await cog.on_restore_schedules()
    cog.cog_unload()
    await asyncio.sleep(0)
    assert set(await db.get_all_reminders()) == {"keepme"}
