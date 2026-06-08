"""
tests/test_bg_tasks.py
Unit tests for NanoBot._spawn_bg's fire-and-forget task handling — specifically
the opt-in timeout that reaps a hung coroutine instead of letting it linger in
_bg_tasks forever.

No dpytest / gateway needed: _spawn_bg only touches the running event loop and
the bot's own _bg_tasks set, so a bare NanoBot instance is enough.
"""

import asyncio

import pytest

import main


@pytest.fixture
def bot():
    return main.NanoBot({})


@pytest.mark.asyncio
async def test_spawn_bg_runs_to_completion(bot):
    ran = asyncio.Event()

    async def work():
        ran.set()

    bot._spawn_bg(work())
    await asyncio.wait_for(ran.wait(), timeout=1)
    # Let the done-callback fire and drain the task out of the tracking set.
    await asyncio.sleep(0)
    assert ran.is_set()
    assert not bot._bg_tasks


@pytest.mark.asyncio
async def test_spawn_bg_timeout_cancels_hung_task(bot):
    started = asyncio.Event()

    async def hang():
        started.set()
        await asyncio.sleep(10)  # would outlive the test without the timeout

    bot._spawn_bg(hang(), timeout=0.05)
    await asyncio.wait_for(started.wait(), timeout=1)
    # Wait past the timeout; wait_for should cancel the inner coro.
    await asyncio.sleep(0.15)
    # Done-callback removes it from the tracking set instead of leaking.
    assert not bot._bg_tasks


@pytest.mark.asyncio
async def test_spawn_bg_no_timeout_does_not_cancel_long_task(bot):
    """timeout=None (default) must leave long-running coroutines untouched."""
    done = asyncio.Event()

    async def slow():
        await asyncio.sleep(0.2)  # longer than any default cap would allow
        done.set()

    bot._spawn_bg(slow())  # no timeout
    await asyncio.wait_for(done.wait(), timeout=1)
    assert done.is_set()
