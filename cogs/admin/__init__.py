"""Owner-only bot management commands package. See cog.py for the command surface."""

from discord.ext import commands

from .cog import Admin


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
