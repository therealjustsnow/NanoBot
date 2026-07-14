"""
Tests for utils/converters.py (no live Discord dependency).

SafeTextChannel is the guild-cache-miss-proof replacement for a
``discord.TextChannel`` command parameter. The built-in transform raises
TransformerError when the picked channel isn't cached (e.g. a channel
created while the gateway was down), which aborted commands like
`/level announce` and `/ticket setup`.
"""

import typing
from types import SimpleNamespace
from unittest.mock import MagicMock

import discord
import pytest
from discord import app_commands
from discord.ext import commands

from utils.converters import SafeTextChannel


class _FakeAppCommandChannel:
    """Stands in for app_commands.AppCommandChannel: resolve() + fetch()."""

    def __init__(self, resolved=None, fetched=None, fetch_exc=None):
        self.id = 1234567890
        self.guild_id = 42
        self._resolved = resolved
        self._fetched = fetched
        self._fetch_exc = fetch_exc
        self.fetch_calls = 0

    def resolve(self):
        return self._resolved

    async def fetch(self):
        self.fetch_calls += 1
        if self._fetch_exc is not None:
            raise self._fetch_exc
        return self._fetched


def _text_channel():
    return MagicMock(spec=discord.TextChannel)


async def test_transform_prefers_cached_channel():
    cached = _text_channel()
    value = _FakeAppCommandChannel(resolved=cached)
    out = await SafeTextChannel().transform(None, value)
    assert out is cached
    assert value.fetch_calls == 0


async def test_transform_fetches_on_cache_miss():
    fetched = _text_channel()
    value = _FakeAppCommandChannel(resolved=None, fetched=fetched)
    out = await SafeTextChannel().transform(None, value)
    assert out is fetched
    assert value.fetch_calls == 1


async def test_transform_raises_transformer_error_when_fetch_fails():
    exc = discord.HTTPException(
        SimpleNamespace(status=404, reason="Not Found"), "Unknown Channel"
    )
    value = _FakeAppCommandChannel(resolved=None, fetch_exc=exc)
    with pytest.raises(app_commands.TransformerError):
        await SafeTextChannel().transform(None, value)


async def test_transform_rejects_non_text_channel():
    value = _FakeAppCommandChannel(resolved=object())
    with pytest.raises(app_commands.TransformerError):
        await SafeTextChannel().transform(None, value)


def test_is_a_text_channel_option():
    t = SafeTextChannel()
    assert t.type is discord.AppCommandOptionType.channel
    assert t.channel_types == [discord.ChannelType.text, discord.ChannelType.news]


def test_hybrid_command_keeps_channel_picker_and_prefix_converter():
    # Regression shape of the /level announce bug: the annotation must build a
    # real channel-picker slash option AND stay a valid ext.commands converter
    # for the prefix side of a hybrid command.
    @commands.hybrid_command()
    async def dummy(ctx, channel: typing.Optional[SafeTextChannel] = None):
        pass

    (param,) = dummy.app_command.parameters
    assert param.type is discord.AppCommandOptionType.channel
    assert param.channel_types == [
        discord.ChannelType.text,
        discord.ChannelType.news,
    ]
    assert issubclass(SafeTextChannel, commands.Converter)
