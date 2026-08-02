"""utils/logfeed — the shared, rate-limit-aware poster for log-channel feeds.

Both the audit log and AutoMod post one message per event, straight from the
listener that noticed it. That is fine for one event and wrong for a burst,
which is exactly what these feeds see: a mass-role change, a purge, a raid of
joins and leaves, an AutoMod rule catching a spammer mid-flood. Discord allows
roughly **5 messages per 5 seconds in one channel**, so a dozen events landing
together means a 429 and discord.py sitting in a retry loop on the bot's behalf.

Two things are worth being precise about, because they decide the design:

* **A sleep in the sender does nothing.** Each listener runs in its own task, so
  they are concurrent, not sequential — sleeping in every one of them delays
  each by the same amount and they still arrive together. A delay only bounds a
  rate when the posts go through *one* path, which is what the per-channel
  worker here is for.
* **Batching beats spacing.** A message carries up to 10 embeds, so a burst of
  30 events can be 3 requests instead of 30 stretched over half a minute. The
  worker therefore waits a short coalescing window, drains what has arrived, and
  posts it as one message. Spacing is the floor underneath that, not the plan.

The feed is deliberately lossy at the far end: a bounded queue drops events once
a channel is hopelessly behind and says so in the next batch, because an
unbounded queue turns a spam wave into unbounded memory and a feed that is still
posting yesterday's events an hour later. It is also *not* a delivery guarantee
— a log feed is a courtesy, and nothing in the bot may block on it.

Both cogs go through `post()`; it never raises and never blocks the caller.
"""

import asyncio
import logging

import discord

log = logging.getLogger("NanoBot.logfeed")

# Discord's per-message embed cap — the whole reason batching is worth more than
# spacing here.
MAX_EMBEDS = 10

# How long a worker holds the first embed of a batch, waiting for more. Long
# enough that a burst arrives together, short enough that a single event still
# feels immediate in the channel.
COALESCE_WINDOW = 0.75

# Floor between two sends to one channel. The bucket is ~5-per-5s; one per
# second leaves headroom for the occasional message the bot posts to the same
# channel from somewhere else.
MIN_INTERVAL = 1.0

# Per-channel backlog cap. At 10 embeds a second this is ~20 seconds behind
# before anything is dropped, which no ordinary burst reaches.
MAX_QUEUE = 200

# How long a worker waits on an empty queue before retiring, so a bot in many
# guilds doesn't hold a task per log channel forever.
IDLE_TIMEOUT = 60.0


class _ChannelFeed:
    """One serialized, batched, spaced posting worker for one channel."""

    def __init__(self, channel: discord.abc.Messageable):
        self.channel = channel
        self.queue: asyncio.Queue[discord.Embed] = asyncio.Queue()
        self.dropped = 0
        self.closed = False
        self.task = asyncio.create_task(self._run())

    def put(self, embed: discord.Embed) -> None:
        """Enqueue an embed, or count it as dropped once the backlog is absurd."""
        if self.queue.qsize() >= MAX_QUEUE:
            self.dropped += 1
            return
        self.queue.put_nowait(embed)

    def _take_batch(self, first: discord.Embed) -> list[discord.Embed]:
        """`first` plus whatever else is already waiting, up to the embed cap."""
        batch = [first]
        while len(batch) < MAX_EMBEDS:
            try:
                batch.append(self.queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return batch

    def _drop_notice(self) -> discord.Embed | None:
        """Say what was lost, once, rather than letting the gap go unexplained."""
        if not self.dropped:
            return None
        count = self.dropped
        self.dropped = 0
        return discord.Embed(
            title="⚠️ Log events skipped",
            description=(
                f"{count} event{'s' if count != 1 else ''} were not logged — "
                "this channel was receiving them faster than Discord allows a "
                "bot to post. Everything else in this feed is complete."
            ),
            color=0xF1C40F,
        )

    async def _run(self) -> None:
        try:
            while True:
                try:
                    first = await asyncio.wait_for(
                        self.queue.get(), timeout=IDLE_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    # Nothing for a minute. Retire — but only if the slot is
                    # still ours and nothing slipped in while we decided.
                    if _feeds.get(_key(self.channel)) is self and self.queue.empty():
                        self.closed = True
                        _feeds.pop(_key(self.channel), None)
                        return
                    continue
                # Hold the first event briefly so a burst travels as one message.
                await asyncio.sleep(COALESCE_WINDOW)
                batch = self._take_batch(first)
                notice = self._drop_notice()
                if notice is not None:
                    batch = batch[: MAX_EMBEDS - 1] + [notice]
                await self._send(batch)
                await asyncio.sleep(MIN_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A worker dying silently would stop a feed for the rest of the
            # process; log it and let the next post start a fresh one.
            log.exception("log feed worker for %s failed", self.channel)
            self.closed = True
            if _feeds.get(_key(self.channel)) is self:
                _feeds.pop(_key(self.channel), None)

    async def _send(self, batch: list[discord.Embed]) -> None:
        try:
            await self.channel.send(embeds=batch)
        except discord.Forbidden:
            # Permissions were taken away mid-feed. Drop the backlog rather than
            # retrying into a wall for every event the server generates.
            log.debug("log feed forbidden in %s — clearing backlog", self.channel)
            while not self.queue.empty():
                self.queue.get_nowait()
        except discord.HTTPException as exc:
            log.debug("log feed send failed in %s: %s", self.channel, exc)


# One feed per channel, keyed by the channel's *snowflake* rather than the
# Python object: the same channel can be handed to us as different objects
# (a cache hit, a fetch, a PartialMessageable), and two feeds for one channel
# would each think they were the only one spacing their sends.
_feeds: dict[int, _ChannelFeed] = {}


def _key(channel: discord.abc.Messageable) -> int:
    return getattr(channel, "id", None) or id(channel)


def post(channel: discord.abc.Messageable, embed: discord.Embed) -> None:
    """Queue an embed for a log channel. Never blocks, never raises.

    Deliberately not a coroutine: a listener that noticed a deleted message has
    no business awaiting a log post, and making the call sync is what guarantees
    no caller can accidentally serialize itself behind the feed.
    """
    key = _key(channel)
    feed = _feeds.get(key)
    if feed is None or feed.closed:
        feed = _ChannelFeed(channel)
        _feeds[key] = feed
    feed.put(embed)


async def drain(timeout: float = 5.0) -> None:
    """Wait for every feed to empty. For tests and shutdown, not for listeners."""
    deadline = asyncio.get_running_loop().time() + timeout
    while any(not f.queue.empty() for f in list(_feeds.values())):
        if asyncio.get_running_loop().time() >= deadline:
            return
        await asyncio.sleep(0.05)


def shutdown() -> None:
    """Cancel every worker. Called from NanoBot.close()."""
    for feed in list(_feeds.values()):
        feed.closed = True
        feed.task.cancel()
    _feeds.clear()
