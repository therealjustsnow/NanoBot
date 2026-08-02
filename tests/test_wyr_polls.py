"""
Tests for restart-safe Would-You-Rather polls.

A `/fun wyr` poll can be set to run for 24 hours, so it will normally outlive
the process that started it. Before `wyr_polls` existed the whole thing was an
in-memory `discord.ui.View` on a `timeout=`: a restart mid-poll silently threw
away every vote cast so far, left the buttons orphaned ("This interaction
failed"), and — because the only code that announced a winner was `on_timeout`
— guaranteed the poll would never announce anything at all. Nobody watching
could tell, which is what made it worth persisting rather than shortening.

Two halves are covered here: the `utils/db/polls.py` accessors against
in-memory SQLite, and the cog's restore path driven with a duck-typed
interaction (dpytest can't dispatch components).
"""

import time

import aiosqlite
import pytest
from discord.ext import test as dpytest

import utils.db as db
from utils import cache_db
from cogs.fun.views import _wyr_cid


# ══════════════════════════════════════════════════════════════════════════════
#  The accessors
# ══════════════════════════════════════════════════════════════════════════════
class TestPollAccessors:
    @pytest.fixture(autouse=True)
    async def database(self, monkeypatch):
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        monkeypatch.setattr(db, "_db", conn)
        await db._ensure_poll_tables()
        yield conn
        await conn.close()

    async def test_create_returns_an_id_and_the_row_reads_back(self):
        end = time.time() + 3600
        poll_id = await db.create_wyr_poll(10, 20, "Fly", "Swim", end)
        assert poll_id > 0

        (row,) = await db.get_open_wyr_polls()
        assert row["poll_id"] == poll_id
        assert row["guild_id"] == 10
        assert row["channel_id"] == 20
        assert row["option_a"] == "Fly"
        assert row["option_b"] == "Swim"
        assert row["votes"] == {}
        assert row["message_id"] is None  # not sent yet
        assert row["end_ts"] == pytest.approx(end)

    async def test_votes_round_trip_as_ints(self):
        poll_id = await db.create_wyr_poll(10, 20, "A", "B", time.time() + 60)
        await db.set_wyr_votes(poll_id, {111: "A", 222: "B", 333: "A"})

        (row,) = await db.get_open_wyr_polls()
        assert row["votes"] == {111: "A", 222: "B", 333: "A"}

    async def test_a_changed_vote_replaces_rather_than_appends(self):
        poll_id = await db.create_wyr_poll(10, 20, "A", "B", time.time() + 60)
        await db.set_wyr_votes(poll_id, {111: "A"})
        await db.set_wyr_votes(poll_id, {111: "B"})

        (row,) = await db.get_open_wyr_polls()
        assert row["votes"] == {111: "B"}

    async def test_message_id_is_written_separately(self):
        poll_id = await db.create_wyr_poll(10, 20, "A", "B", time.time() + 60)
        await db.set_wyr_message(poll_id, 999)
        (row,) = await db.get_open_wyr_polls()
        assert row["message_id"] == 999

    async def test_delete_closes_the_poll(self):
        poll_id = await db.create_wyr_poll(10, 20, "A", "B", time.time() + 60)
        await db.delete_wyr_poll(poll_id)
        assert await db.get_open_wyr_polls() == []

    async def test_a_dm_poll_keeps_a_null_guild(self):
        """A poll has a channel wherever it runs, but not always a guild."""
        await db.create_wyr_poll(None, 20, "A", "B", time.time() + 60)
        (row,) = await db.get_open_wyr_polls()
        assert row["guild_id"] is None

    async def test_a_corrupt_tally_loses_the_votes_not_the_poll(self, database):
        """The board still has to close and announce, even from zero."""
        poll_id = await db.create_wyr_poll(10, 20, "A", "B", time.time() + 60)
        await database.execute(
            "UPDATE wyr_polls SET votes='not json' WHERE poll_id=?", (poll_id,)
        )

        (row,) = await db.get_open_wyr_polls()
        assert row["poll_id"] == poll_id
        assert row["votes"] == {}

    async def test_polls_are_independent(self):
        first = await db.create_wyr_poll(10, 20, "A", "B", time.time() + 60)
        second = await db.create_wyr_poll(10, 21, "C", "D", time.time() + 60)
        await db.set_wyr_votes(first, {111: "A"})

        rows = {r["poll_id"]: r for r in await db.get_open_wyr_polls()}
        assert rows[first]["votes"] == {111: "A"}
        assert rows[second]["votes"] == {}


# ══════════════════════════════════════════════════════════════════════════════
#  The view + the cog's restore path
# ══════════════════════════════════════════════════════════════════════════════
def config():
    return dpytest.get_config()


class _Interaction:
    """The parts of an Interaction a vote press touches.

    dpytest can't dispatch components, so presses are driven by calling the
    handler directly — the same approach the fishing/adventure view tests take.
    """

    def __init__(self, user, message):
        self.user = user
        self.message = message
        self.response = self
        self.sent: list[tuple[str, bool]] = []

    async def send_message(self, content=None, *, ephemeral=False, **kw):
        self.sent.append((content, ephemeral))


class _Message:
    """A message that records what it was edited to."""

    def __init__(self, message_id=1234):
        self.id = message_id
        self.embeds = []
        self.view = None
        self.edits = 0

    async def edit(self, *, embed=None, view=None, **kw):
        self.edits += 1
        if embed is not None:
            self.embeds = [embed]
        if view is not None:
            self.view = view


async def _new_poll(cog, *, duration=3600, votes=None):
    """A live, armed poll bound to a fake message — what a `!wyr` leaves behind."""
    view = await cog._make_poll(1, 2, "Fly", "Swim", duration)
    if votes:
        view.votes.update(votes)
        await db.set_wyr_votes(view.poll_id, view.votes)
    message = _Message()
    await cog._send_poll(view, message)
    return view, message


@pytest.mark.cogs("cogs.fun")
async def test_a_vote_is_written_to_the_database(bot):
    cog = bot.get_cog("Fun")
    voter = config().members[0]
    view, message = await _new_poll(cog)

    await view._handle_vote(_Interaction(voter, message), "A")

    (row,) = await db.get_open_wyr_polls()
    assert row["votes"] == {voter.id: "A"}


@pytest.mark.cogs("cogs.fun")
async def test_a_changed_vote_is_written_too(bot):
    cog = bot.get_cog("Fun")
    voter = config().members[0]
    view, message = await _new_poll(cog)

    await view._handle_vote(_Interaction(voter, message), "A")
    await view._handle_vote(_Interaction(voter, message), "B")

    (row,) = await db.get_open_wyr_polls()
    assert row["votes"] == {voter.id: "B"}
    assert view._tally() == (0, 1)


@pytest.mark.cogs("cogs.fun")
async def test_the_poll_survives_a_restart_with_its_votes(bot):
    """The reported bug: restart mid-poll and the votes are still counted."""
    cog = bot.get_cog("Fun")
    a, b = config().members[0], config().members[1]
    view, message = await _new_poll(cog, votes={a.id: "A", b.id: "B"})
    poll_id = view.poll_id

    # A restart: nothing live in memory, everything read back from SQLite.
    for task in cog._poll_tasks.values():
        task.cancel()
    cog._polls.clear()
    cog._poll_tasks.clear()

    await cog.on_restore_schedules()

    assert poll_id in cog._polls
    assert poll_id in cog._poll_tasks
    restored = cog._polls[poll_id]
    assert restored.votes == {a.id: "A", b.id: "B"}
    assert restored.option_a == "Fly"
    assert restored.option_b == "Swim"
    assert restored.end_ts == view.end_ts
    # The buttons are still live: same per-poll custom_ids, re-registered.
    expected = {_wyr_cid(poll_id, "A"), _wyr_cid(poll_id, "B")}
    assert {c.custom_id for c in restored.children} == expected
    # Matched on custom_id rather than isinstance: loading a cog replaces the
    # module, so a class bound at collection time is a different object in a
    # full run (the same trap tests/test_fishing_views.py documents).
    assert any(
        {c.custom_id for c in view.children} == expected
        for view in bot.persistent_views
    )


@pytest.mark.cogs("cogs.fun")
async def test_a_restored_poll_keeps_counting(bot):
    cog = bot.get_cog("Fun")
    a, b = config().members[0], config().members[1]
    view, message = await _new_poll(cog, votes={a.id: "A"})

    for task in cog._poll_tasks.values():
        task.cancel()
    cog._polls.clear()
    cog._poll_tasks.clear()
    await cog.on_restore_schedules()

    restored = cog._polls[view.poll_id]
    restored.message = message
    await restored._handle_vote(_Interaction(b, message), "B")

    assert restored._tally() == (1, 1)
    (row,) = await db.get_open_wyr_polls()
    assert row["votes"] == {a.id: "A", b.id: "B"}


@pytest.mark.cogs("cogs.fun")
async def test_a_poll_that_ended_while_the_bot_was_down_announces_on_restore(bot):
    """The other half of the bug: the result still goes out, just late."""
    cog = bot.get_cog("Fun")
    a, b = config().members[0], config().members[1]
    poll_id = await db.create_wyr_poll(1, 2, "Fly", "Swim", time.time() - 5)
    await db.set_wyr_message(poll_id, 4321)
    await db.set_wyr_votes(poll_id, {a.id: "A", b.id: "A"})

    await cog.on_restore_schedules()
    view = cog._polls[poll_id]
    message = _Message(4321)
    view.message = message
    # The timer was armed with a zero delay; let it fire. (It clears its own
    # entry from _poll_tasks on the way out, so hold the task first.)
    task = cog._poll_tasks[poll_id]
    await task

    assert view.ended
    assert message.embeds and "Results" in message.embeds[0].title
    assert "Fly" in message.embeds[0].description  # the winner is named
    assert await db.get_open_wyr_polls() == []


@pytest.mark.cogs("cogs.fun")
async def test_a_crash_before_the_message_was_sent_drops_the_row(bot):
    """message_id NULL means there are no buttons to bind — nothing to restore."""
    cog = bot.get_cog("Fun")
    await db.create_wyr_poll(1, 2, "Fly", "Swim", time.time() + 3600)

    await cog.on_restore_schedules()

    assert cog._polls == {}
    assert await db.get_open_wyr_polls() == []


@pytest.mark.cogs("cogs.fun")
async def test_restoring_twice_leaves_one_timer(bot):
    """on_ready re-fires on every gateway re-identify."""
    cog = bot.get_cog("Fun")
    view, _message = await _new_poll(cog)

    await cog.on_restore_schedules()
    await cog.on_restore_schedules()

    assert len(cog._poll_tasks) == 1
    live = [t for t in cog._poll_tasks.values() if not t.done()]
    assert len(live) == 1
    # The original view is kept, not replaced by a second copy of itself.
    assert cog._polls[view.poll_id] is view


@pytest.mark.cogs("cogs.fun")
async def test_finish_announces_the_winner_and_closes_the_poll(bot):
    cog = bot.get_cog("Fun")
    a, b = config().members[0], config().members[1]
    view, message = await _new_poll(cog, votes={a.id: "B", b.id: "B"})

    await view.finish()

    assert view.ended
    assert all(child.disabled for child in view.children)
    embed = message.embeds[0]
    assert "Swim" in embed.description and "wins" in embed.description
    assert "2 total votes" in embed.footer.text
    assert await db.get_open_wyr_polls() == []
    assert view.poll_id not in cog._polls
    assert view.poll_id not in cog._poll_tasks


@pytest.mark.cogs("cogs.fun")
async def test_finish_is_idempotent(bot):
    """The closing timer and a late click can both reach it."""
    cog = bot.get_cog("Fun")
    view, message = await _new_poll(cog, votes={config().members[0].id: "A"})

    await view.finish()
    edits = message.edits
    await view.finish()

    assert message.edits == edits


@pytest.mark.cogs("cogs.fun")
async def test_a_tie_and_an_empty_poll_still_announce(bot):
    cog = bot.get_cog("Fun")
    a, b = config().members[0], config().members[1]

    view, message = await _new_poll(cog, votes={a.id: "A", b.id: "B"})
    await view.finish()
    assert "tie" in message.embeds[0].description.lower()

    view, message = await _new_poll(cog)
    await view.finish()
    assert "Nobody voted" in message.embeds[0].description


@pytest.mark.cogs("cogs.fun")
async def test_a_vote_after_the_deadline_closes_the_poll_instead_of_counting(bot):
    """A press that lands between the deadline and the timer firing."""
    cog = bot.get_cog("Fun")
    a, b = config().members[0], config().members[1]
    view, message = await _new_poll(cog, votes={a.id: "A"})
    view.end_ts = time.time() - 1  # deadline passed

    interaction = _Interaction(b, message)
    await view._handle_vote(interaction, "B")

    assert view.ended
    assert view.votes == {a.id: "A"}  # the late vote was not counted
    assert interaction.sent[0] == ("Voting has ended!", True)
    assert "Fly" in message.embeds[0].description
    assert await db.get_open_wyr_polls() == []


@pytest.mark.cogs("cogs.fun")
async def test_voting_the_same_way_twice_is_refused_without_a_write(bot):
    cog = bot.get_cog("Fun")
    voter = config().members[0]
    view, message = await _new_poll(cog)

    await view._handle_vote(_Interaction(voter, message), "A")
    interaction = _Interaction(voter, message)
    await view._handle_vote(interaction, "A")

    assert "already voted" in interaction.sent[0][0]
    assert view._tally() == (1, 0)


@pytest.fixture
async def question_cache(tmp_path, monkeypatch):
    """A throwaway cache.db holding one WYR question.

    Closed on the way out: aiosqlite's connection thread is not a daemon, so a
    cache left open keeps the whole pytest process alive after the run.
    """
    monkeypatch.setattr(cache_db, "_DB_PATH", str(tmp_path / "cache.db"))
    await cache_db.init()
    await cache_db.add_wyr_questions(["Would you rather fly or swim?"])
    yield
    await cache_db.close()


@pytest.mark.cogs("cogs.fun")
async def test_wyr_command_persists_its_poll(bot, question_cache):
    """End to end: the prefix command leaves a row behind, not just a view."""
    cog = bot.get_cog("Fun")

    await dpytest.message("!wyr 30m", member=config().members[0])

    (row,) = await db.get_open_wyr_polls()
    assert row["message_id"] is not None
    assert row["poll_id"] in cog._polls
    assert row["end_ts"] > time.time()
