"""
cogs/images.py
Random anime image commands -- mobile-first.

Images sourced from nekos.best (no API key required).
URLs cached in cache_db; scraping handled by the Fun cog's daily loop.
Falls back to live API if cache is empty for an endpoint.

Slash:  /husbando, /kitsune, /neko, /waifu  (4 top-level slots)
Prefix: !husbando, !kitsune, !neko, !waifu
"""

import logging

import aiohttp
import discord
from discord.ext import commands

from utils import helpers as h

log = logging.getLogger("NanoBot.images")

_PINK = 0xFF6EB4

_ENDPOINTS: dict[str, dict] = {
    "husbando": {
        "endpoint": "husbando",
        "title": "Random Husbando",
        "emoji": "\U0001f468",
        "color": h.BLUE,
    },
    "kitsune": {
        "endpoint": "kitsune",
        "title": "Random Kitsune",
        "emoji": "\U0001f98a",
        "color": 0xFFA500,
    },
    "neko": {
        "endpoint": "neko",
        "title": "Random Neko",
        "emoji": "\U0001f431",
        "color": _PINK,
    },
    "waifu": {
        "endpoint": "waifu",
        "title": "Random Waifu",
        "emoji": "\U0001f467",
        "color": _PINK,
    },
}


# ══════════════════════════════════════════════════════════════════════════════
class Images(commands.Cog):
    """Random anime image commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None

    async def cog_load(self):
        self._session = aiohttp.ClientSession()

    async def cog_unload(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ── shared builder ────────────────────────────────────────────────────────

    async def _image_cmd(self, ctx_or_i, key: str):
        """Handle both prefix and slash invocations."""
        # Import here to avoid circular import at module level.
        # fun.py owns the cache-aware getter; images.py just calls it.
        from cogs.fun import _get_nekos_image

        info = _ENDPOINTS[key]
        result = await _get_nekos_image(self._session, info["endpoint"])

        if not result:
            e = discord.Embed(
                description="Couldn't fetch an image right now. Try again in a moment!",
                color=h.RED,
            )
            e.set_footer(text="NanoBot Images")
            if isinstance(ctx_or_i, discord.Interaction):
                return await ctx_or_i.response.send_message(embed=e, ephemeral=True)
            return await ctx_or_i.reply(embed=e)

        e = discord.Embed(
            title=f"{info['emoji']} {info['title']}",
            color=info["color"],
        )
        e.set_image(url=result["url"])

        # Credit the artist if available
        artist = result.get("artist")
        source_url = result.get("source_url")
        footer_parts = ["NanoBot Images"]
        if artist:
            footer_parts.append(f"Art by {artist}")
        e.set_footer(text=" \u00b7 ".join(footer_parts))
        if source_url:
            e.description = f"[\U0001f517 Source]({source_url})"

        if isinstance(ctx_or_i, discord.Interaction):
            await ctx_or_i.response.send_message(embed=e)
        else:
            await ctx_or_i.reply(embed=e)

    # ── commands ──────────────────────────────────────────────────────────────

    @commands.hybrid_command(
        name="husbando",
        description="Get a random husbando image.",
        extras={
            "category": "\U0001f5bc\ufe0f Images",
            "short": "Random husbando image",
            "usage": "husbando",
            "desc": "Fetches a random anime husbando image from nekos.best.",
            "args": [],
            "perms": "None",
            "example": "!husbando",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def husbando(self, ctx: commands.Context):
        await self._image_cmd(ctx, "husbando")

    @commands.hybrid_command(
        name="kitsune",
        description="Get a random kitsune image.",
        extras={
            "category": "\U0001f5bc\ufe0f Images",
            "short": "Random kitsune image",
            "usage": "kitsune",
            "desc": "Fetches a random anime kitsune image from nekos.best.",
            "args": [],
            "perms": "None",
            "example": "!kitsune",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def kitsune(self, ctx: commands.Context):
        await self._image_cmd(ctx, "kitsune")

    @commands.hybrid_command(
        name="neko",
        description="Get a random neko image.",
        extras={
            "category": "\U0001f5bc\ufe0f Images",
            "short": "Random neko image",
            "usage": "neko",
            "desc": "Fetches a random anime neko image from nekos.best.",
            "args": [],
            "perms": "None",
            "example": "!neko",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def neko(self, ctx: commands.Context):
        await self._image_cmd(ctx, "neko")

    @commands.hybrid_command(
        name="waifu",
        description="Get a random waifu image.",
        extras={
            "category": "\U0001f5bc\ufe0f Images",
            "short": "Random waifu image",
            "usage": "waifu",
            "desc": "Fetches a random anime waifu image from nekos.best.",
            "args": [],
            "perms": "None",
            "example": "!waifu",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def waifu(self, ctx: commands.Context):
        await self._image_cmd(ctx, "waifu")


async def setup(bot: commands.Bot):
    await bot.add_cog(Images(bot))
