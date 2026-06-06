"""Fun commands package: social/react GIFs, ship, 8-ball, FML, WYR, RPS.

See cog.py for the command surface and the daily scraper/revalidate loops.
"""

from discord.ext import commands

from .cog import Fun


async def setup(bot: commands.Bot):
    await bot.add_cog(Fun(bot))
