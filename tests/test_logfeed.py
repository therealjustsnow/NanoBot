"""
Tests for utils/logfeed.py — the shared, rate-limit-aware log-channel poster.

The audit log and AutoMod both used to call `channel.send` straight from the
listener that noticed an event. Each listener runs in its own task, so a burst
(a mass-role change, a purge, a spammer being caught over and over) became that
many simultaneous POSTs to one channel and a 429. What matters here is not that
posts are *slower* but that they are **serialized, batched and spaced**: a
delay applied inside a dozen concurrent tasks delays each by the same amount
and they still arrive together, which is the trap this module exists to avoid.

Every test shortens the module's timings, since the point under test is the
shape of the traffic, not the wall-clock constants.
"""

import asyncio

import discord
import pytest

from utils import logfeed


class _Channel:
    """A channel that records each send as one request, with its timestamp."""

    def __init__(self, channel_id=1, fail=None):
        self.id = channel_id
        self.sends: list[list[discord.Embed]] = []
        self.times: list[float] = []
        self._fail = fail

    async def send(self, *, embeds=None, embed=None, **kw):
        if self._fail is not None:
            raise self._fail
        self.sends.append(list(embeds) if embeds is not None else [embed])
        self.times.append(asyncio.get_running_loop().time())


def _embed(n: int) -> discord.Embed:
    return discord.Embed(title=f"event {n}")


@pytest.fixture(autouse=True)
def fast_feed(monkeypatch):
    """Shrink the timings, and never leak a worker into the next test."""
    monkeypatch.setattr(logfeed, "COALESCE_WINDOW", 0.02)
    monkeypatch.setattr(logfeed, "MIN_INTERVAL", 0.05)
    monkeypatch.setattr(logfeed, "IDLE_TIMEOUT", 0.2)
    yield
    logfeed.shutdown()


async def test_a_single_event_is_posted(monkeypatch):
    ch = _Channel()
    logfeed.post(ch, _embed(1))
    await logfeed.drain()
    await asyncio.sleep(0.05)

    assert len(ch.sends) == 1
    assert ch.sends[0][0].title == "event 1"


async def test_a_burst_becomes_one_request(monkeypatch):
    """The actual fix: ten events that arrive together cost one POST, not ten."""
    ch = _Channel()
    for n in range(10):
        logfeed.post(ch, _embed(n))
    await logfeed.drain()
    await asyncio.sleep(0.05)

    assert len(ch.sends) == 1
    assert len(ch.sends[0]) == 10


async def test_a_batch_never_exceeds_discords_embed_cap():
    ch = _Channel()
    for n in range(25):
        logfeed.post(ch, _embed(n))
    await logfeed.drain()
    await asyncio.sleep(0.2)

    assert all(len(batch) <= logfeed.MAX_EMBEDS for batch in ch.sends)
    assert sum(len(batch) for batch in ch.sends) == 25


async def test_order_is_preserved_across_batches():
    ch = _Channel()
    for n in range(15):
        logfeed.post(ch, _embed(n))
    await logfeed.drain()
    await asyncio.sleep(0.2)

    titles = [e.title for batch in ch.sends for e in batch]
    assert titles == [f"event {n}" for n in range(15)]


async def test_sends_to_one_channel_are_spaced():
    """Batches are only half the answer — consecutive sends keep a floor."""
    ch = _Channel()
    for n in range(25):
        logfeed.post(ch, _embed(n))
    await logfeed.drain()
    await asyncio.sleep(0.2)

    assert len(ch.times) >= 3
    gaps = [b - a for a, b in zip(ch.times, ch.times[1:])]
    assert all(gap >= logfeed.MIN_INTERVAL for gap in gaps)


async def test_two_channels_do_not_wait_on_each_other():
    """The limit is per channel, so the feeds have to be per channel too."""
    first, second = _Channel(1), _Channel(2)
    for n in range(10):
        logfeed.post(first, _embed(n))
    logfeed.post(second, _embed(99))
    await logfeed.drain()
    await asyncio.sleep(0.05)

    assert len(first.sends) == 1 and len(second.sends) == 1


async def test_one_channel_gets_one_feed_however_it_is_handed_over():
    """Two objects for the same channel must not each think they're alone."""
    first, second = _Channel(7), _Channel(7)
    logfeed.post(first, _embed(1))
    logfeed.post(second, _embed(2))
    await logfeed.drain()
    await asyncio.sleep(0.05)

    assert len(logfeed._feeds) == 1
    # Both embeds went out together, through whichever object opened the feed.
    assert sorted(len(c.sends) for c in (first, second)) == [0, 1]


async def test_a_hopeless_backlog_is_dropped_and_reported(monkeypatch):
    monkeypatch.setattr(logfeed, "MAX_QUEUE", 5)
    ch = _Channel()
    for n in range(40):
        logfeed.post(ch, _embed(n))
    await logfeed.drain()
    await asyncio.sleep(0.2)

    posted = [e for batch in ch.sends for e in batch]
    assert len(posted) < 40  # events were genuinely dropped
    # The gap is explained rather than left silent.
    assert any("skipped" in (e.title or "") for e in posted)


async def test_forbidden_clears_the_backlog_instead_of_retrying(monkeypatch):
    """Permissions pulled mid-feed must not become a retry loop per event."""
    ch = _Channel(fail=discord.Forbidden(_Resp(403), "no"))
    for n in range(30):
        logfeed.post(ch, _embed(n))
    feed = logfeed._feeds[ch.id]  # held: an emptied feed retires on its own
    await asyncio.sleep(0.15)

    assert ch.sends == []
    assert feed.queue.empty()


async def test_an_http_error_does_not_kill_the_feed():
    ch = _Channel(fail=discord.HTTPException(_Resp(500), "boom"))
    logfeed.post(ch, _embed(1))
    await asyncio.sleep(0.15)

    feed = logfeed._feeds[ch.id]
    assert not feed.closed
    assert not feed.task.done()


async def test_post_never_blocks_the_caller():
    """A listener must not wait on Discord — post() is sync by design."""
    ch = _Channel()
    logfeed.post(ch, _embed(1))
    assert ch.sends == []  # nothing sent yet: the worker owns the send


async def test_an_idle_feed_retires():
    ch = _Channel()
    logfeed.post(ch, _embed(1))
    await logfeed.drain()
    await asyncio.sleep(logfeed.IDLE_TIMEOUT + 0.2)

    assert ch.id not in logfeed._feeds


async def test_a_retired_feed_is_reopened_by_the_next_post():
    ch = _Channel()
    logfeed.post(ch, _embed(1))
    await logfeed.drain()
    await asyncio.sleep(logfeed.IDLE_TIMEOUT + 0.2)
    assert ch.id not in logfeed._feeds

    logfeed.post(ch, _embed(2))
    await logfeed.drain()
    await asyncio.sleep(0.05)

    assert len(ch.sends) == 2


async def test_shutdown_cancels_every_worker():
    first, second = _Channel(1), _Channel(2)
    logfeed.post(first, _embed(1))
    logfeed.post(second, _embed(2))
    tasks = [f.task for f in logfeed._feeds.values()]

    logfeed.shutdown()
    await asyncio.sleep(0)

    assert logfeed._feeds == {}
    assert all(t.cancelled() or t.cancelling() for t in tasks)


class _Resp:
    """The minimal aiohttp response discord.py's exceptions read."""

    def __init__(self, status):
        self.status = status
        self.reason = "test"
