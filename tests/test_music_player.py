"""Tests for voice-state recovery logic in the music package.

These cover two behaviours added to handle a flaky voice region, both of which
hinge only on ``guild.voice_client`` / ``guild.me.voice`` and so can be driven
with light fakes — no live gateway (which dpytest can't simulate anyway):

- ``GuildPlayer.skip()`` reports whether a track was actually stopped, so the
  skip/forceskip commands stop falsely claiming "Skipped" when the voice
  connection has gone away underneath the player.
- ``Music._clear_ghost_voice()`` issues a gateway voice-state leave only when
  Discord still thinks the bot is connected while discord.py holds no
  VoiceClient (the "ghost" state).
- ``Music.on_voice_state_update`` tears the player down when the bot itself is
  disconnected from voice, so a stale "Streaming" presence can't stick.

Both objects are built with ``__new__`` to skip ``__init__`` (which spins up
asyncio loop tasks and needs a live bot).
"""

import asyncio
import types

from cogs.music.player import GuildPlayer
from cogs.music.cog import Music

# ---------------------------------------------------------------------------
# GuildPlayer.skip — honest return contract
# ---------------------------------------------------------------------------


class _FakeVoice:
    def __init__(self, *, playing=False, paused=False):
        self._playing = playing
        self._paused = paused
        self.stopped = False

    def is_playing(self):
        return self._playing

    def is_paused(self):
        return self._paused

    def stop(self):
        self.stopped = True


def _make_player(voice_client):
    player = GuildPlayer.__new__(GuildPlayer)
    player.guild = types.SimpleNamespace(voice_client=voice_client)
    return player


class TestSkipContract:
    def test_no_voice_client_returns_false(self):
        player = _make_player(None)
        assert player.skip() is False

    def test_idle_voice_returns_false_without_stopping(self):
        voice = _FakeVoice(playing=False, paused=False)
        player = _make_player(voice)
        assert player.skip() is False
        assert voice.stopped is False

    def test_playing_stops_and_returns_true(self):
        voice = _FakeVoice(playing=True)
        player = _make_player(voice)
        assert player.skip() is True
        assert voice.stopped is True

    def test_paused_stops_and_returns_true(self):
        voice = _FakeVoice(paused=True)
        player = _make_player(voice)
        assert player.skip() is True
        assert voice.stopped is True


# ---------------------------------------------------------------------------
# Music._clear_ghost_voice — gateway leave only for a real ghost
# ---------------------------------------------------------------------------


def _make_guild(*, voice_client, me_channel):
    calls = {"left": False, "count": 0}

    async def change_voice_state(*, channel, **_kwargs):
        calls["count"] += 1
        calls["left"] = channel is None

    me_voice = (
        None if me_channel is _MISSING else types.SimpleNamespace(channel=me_channel)
    )
    guild = types.SimpleNamespace(
        id=123,
        voice_client=voice_client,
        me=types.SimpleNamespace(voice=me_voice),
        change_voice_state=change_voice_state,
    )
    return guild, calls


_MISSING = object()  # sentinel: member has no voice state at all


async def _noop_sleep(*_args, **_kwargs):
    return None


class TestClearGhostVoice:
    async def test_no_ghost_when_voice_client_present(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", _noop_sleep)
        cog = Music.__new__(Music)
        connected = object()  # a present (non-None) voice client
        guild, calls = _make_guild(voice_client=connected, me_channel=object())
        assert await cog._clear_ghost_voice(guild) is False
        assert calls["count"] == 0

    async def test_no_ghost_when_member_has_no_voice_state(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", _noop_sleep)
        cog = Music.__new__(Music)
        guild, calls = _make_guild(voice_client=None, me_channel=_MISSING)
        assert await cog._clear_ghost_voice(guild) is False
        assert calls["count"] == 0

    async def test_no_ghost_when_member_voice_has_no_channel(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", _noop_sleep)
        cog = Music.__new__(Music)
        guild, calls = _make_guild(voice_client=None, me_channel=None)
        assert await cog._clear_ghost_voice(guild) is False
        assert calls["count"] == 0

    async def test_ghost_triggers_gateway_leave(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", _noop_sleep)
        cog = Music.__new__(Music)
        guild, calls = _make_guild(voice_client=None, me_channel=object())
        assert await cog._clear_ghost_voice(guild) is True
        assert calls["count"] == 1
        assert calls["left"] is True

    async def test_leave_failure_returns_false(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", _noop_sleep)
        cog = Music.__new__(Music)

        async def boom(*, channel, **_kwargs):
            raise RuntimeError("gateway down")

        guild = types.SimpleNamespace(
            id=123,
            voice_client=None,
            me=types.SimpleNamespace(voice=types.SimpleNamespace(channel=object())),
            change_voice_state=boom,
        )
        assert await cog._clear_ghost_voice(guild) is False


# ---------------------------------------------------------------------------
# Music.on_voice_state_update — bot's own disconnect tears the player down so a
# stale Streaming presence can't stick after the bot leaves voice.
# ---------------------------------------------------------------------------


def _make_cog_with_bot(players):
    cog = Music.__new__(Music)
    cog.bot = types.SimpleNamespace(user=types.SimpleNamespace(id=999))
    cog.players = players
    refreshed = {"count": 0}

    async def _refresh():
        refreshed["count"] += 1

    cog._refresh_presence = _refresh
    return cog, refreshed


def _vs(channel):
    return types.SimpleNamespace(channel=channel)


class TestSelfDisconnectTeardown:
    async def test_bot_disconnect_destroys_live_player(self):
        destroyed = {"called": False}

        async def _destroy(*_a, **_k):
            destroyed["called"] = True

        player = types.SimpleNamespace(_destroyed=False, destroy=_destroy)
        cog, _ = _make_cog_with_bot({7: player})
        member = types.SimpleNamespace(id=999, guild=types.SimpleNamespace(id=7))
        await cog.on_voice_state_update(member, _vs(object()), _vs(None))
        assert destroyed["called"] is True

    async def test_bot_disconnect_without_player_refreshes_presence(self):
        cog, refreshed = _make_cog_with_bot({})
        member = types.SimpleNamespace(id=999, guild=types.SimpleNamespace(id=7))
        await cog.on_voice_state_update(member, _vs(object()), _vs(None))
        assert refreshed["count"] == 1

    async def test_bot_move_between_channels_is_not_a_teardown(self):
        destroyed = {"called": False}

        async def _destroy(*_a, **_k):
            destroyed["called"] = True

        player = types.SimpleNamespace(_destroyed=False, destroy=_destroy)
        cog, refreshed = _make_cog_with_bot({7: player})
        member = types.SimpleNamespace(id=999, guild=types.SimpleNamespace(id=7))
        # after.channel is not None → moved, not disconnected.
        await cog.on_voice_state_update(member, _vs(object()), _vs(object()))
        assert destroyed["called"] is False
        assert refreshed["count"] == 0
