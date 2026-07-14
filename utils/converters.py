"""
Shared command-argument converters used across cogs.

SafeTextChannel exists because the built-in ``discord.TextChannel`` slash
annotation resolves the picked channel through the guild cache and raises
TransformerError on a miss — e.g. a channel created while the gateway was
disconnected, so the bot never received its CHANNEL_CREATE. Discord already
validated the pick (the option arrives as a real channel id of the right
type), so a cache miss is recoverable: fetch the channel over HTTP instead
of failing the whole command.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("NanoBot.converters")


class SafeTextChannel(commands.Converter, app_commands.Transformer):
    """Text-channel command option that survives a guild-cache miss.

    Drop-in replacement for a ``discord.TextChannel`` parameter annotation in
    slash, hybrid, and prefix commands (``SafeTextChannel`` or
    ``Optional[SafeTextChannel]``). The slash UI is unchanged — same channel
    picker, same text+news filter — and the command always receives a real
    ``discord.TextChannel``. On the prefix side it defers to the stock
    ``TextChannelConverter``; on the slash side it falls back to an HTTP
    fetch when the picked channel isn't in the guild cache.
    """

    @property
    def type(self) -> discord.AppCommandOptionType:
        return discord.AppCommandOptionType.channel

    @property
    def channel_types(self) -> list[discord.ChannelType]:
        # Mirror the built-in discord.TextChannel annotation's picker filter.
        return [discord.ChannelType.text, discord.ChannelType.news]

    async def convert(
        self, ctx: commands.Context, argument: str
    ) -> discord.TextChannel:
        return await commands.TextChannelConverter().convert(ctx, argument)

    async def transform(
        self,
        interaction: discord.Interaction,
        value: app_commands.AppCommandChannel,
    ) -> discord.TextChannel:
        channel = value.resolve()
        if channel is None:
            log.info(
                f"Channel {value.id} missing from guild {value.guild_id} cache; "
                "fetching over HTTP"
            )
            try:
                channel = await value.fetch()
            except discord.HTTPException as exc:
                raise app_commands.TransformerError(value, self.type, self) from exc
        if not isinstance(channel, discord.TextChannel):
            raise app_commands.TransformerError(value, self.type, self)
        return channel
