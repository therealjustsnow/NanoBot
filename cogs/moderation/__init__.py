"""Core moderation commands package. See cog.py for the command surface."""

from discord.ext import commands

from .cog import Moderation


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
