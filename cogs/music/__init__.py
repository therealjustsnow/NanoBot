"""Voice music player package. See cog.py for the command surface."""

from discord.ext import commands

from .cog import Music


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
