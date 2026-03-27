"""
cogs/fun.py
Fun social interaction commands — mobile-first.

GIFs sourced from nekos.best (no API key required).
Falls back gracefully (text-only) if the API is unavailable.

Commands (all hybrid — prefix + slash):
  hug         [user]          — give someone a hug
  kiss        [user]          — kiss someone
  cheekskiss  [user]          — give a cheek kiss
  pat         [user]          — head pat
  poke        [user]          — poke
  boop        [user]          — boop the snoot
  wave        [user]          — wave
  highfive    [user]          — high five
  ship        <user1> <user2> — compatibility rating
"""

import hashlib
import logging
import re
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from utils import helpers as h

log = logging.getLogger("NanoBot.fun")

_NEKOS_BASE = "https://nekos.best/api/v2"
_PINK = 0xFF6EB4

# nekos.best endpoint per action
# boop and cheekskiss have no direct endpoint — mapped to closest equivalent
_ENDPOINTS: dict[str, str] = {
    "hug": "hug",
    "kiss": "kiss",
    "cheekskiss": "kiss",
    "pat": "pat",
    "poke": "poke",
    "boop": "pat",
    "wave": "wave",
    "highfive": "highfive",
}


# ── Ship helpers ───────────────────────────────────────────────────────────────


def _ship_score(id1: int, id2: int) -> int:
    """Deterministic 0–100 score for a user pair. Same pair always gets same score."""
    key = f"{min(id1, id2)}x{max(id1, id2)}"
    digest = hashlib.md5(key.encode()).digest()
    return int.from_bytes(digest[:2], "big") % 101


def _ship_name(n1: str, n2: str) -> str:
    """Combine two display names into a ship name."""
    clean1 = re.sub(r"[^\w]", "", n1) or n1
    clean2 = re.sub(r"[^\w]", "", n2) or n2
    half1 = clean1[: max(1, len(clean1) // 2)]
    half2 = clean2[len(clean2) // 2 :] or clean2[-1:]
    return (half1 + half2).title()


def _progress_bar(pct: int, length: int = 10) -> str:
    filled = round(pct / 100 * length)
    return "▓" * filled + "░" * (length - filled)


def _ship_verdict(pct: int) -> str:
    if pct == 100:
        return "🌟 SOULMATES — a perfect match!"
    if pct >= 81:
        return "💖 Made for each other!"
    if pct >= 61:
        return "💕 A pretty good match!"
    if pct >= 41:
        return "👀 There's potential here..."
    if pct >= 21:
        return "😬 It's... complicated."
    return "💔 Not meant to be."


# ── GIF fetcher ────────────────────────────────────────────────────────────────


async def _fetch_gif(session: aiohttp.ClientSession, action: str) -> str | None:
    """Fetch one random GIF URL from nekos.best. Returns None on any failure."""
    endpoint = _ENDPOINTS.get(action, action)
    try:
        async with session.get(
            f"{_NEKOS_BASE}/{endpoint}",
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                results = data.get("results", [])
                if results:
                    return results[0]["url"]
    except Exception as exc:
        log.debug(f"GIF fetch failed for '{action}': {exc}")
    return None


# ══════════════════════════════════════════════════════════════════════════════
class Fun(commands.Cog):
    """Fun social interaction commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None

    async def cog_load(self):
        self._session = aiohttp.ClientSession()

    async def cog_unload(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ── Shared action handler ──────────────────────────────────────────────────

    async def _do_action(
        self,
        ctx: commands.Context,
        action: str,
        target: Optional[discord.Member],
        *,
        bot_msg: str,
        self_msg: str,
        action_msg: str,
        color: int = _PINK,
    ) -> None:
        """
        Build and send an interaction embed.

        action_msg supports {author} and {target} placeholders.
        If target is None or the same as the author, self_msg is used.
        If target is the bot, bot_msg is used.
        """
        author = ctx.author

        if target is None or target == author:
            desc = self_msg
        elif target == ctx.guild.me:
            desc = bot_msg
        else:
            desc = action_msg.replace("{author}", f"**{author.display_name}**").replace(
                "{target}", target.mention
            )

        e = discord.Embed(description=desc, color=color)

        if self._session:
            gif = await _fetch_gif(self._session, action)
            if gif:
                e.set_image(url=gif)

        e.set_footer(text="NanoBot Fun")
        await ctx.reply(embed=e)

    # ══════════════════════════════════════════════════════════════════════════
    #  hug
    # ══════════════════════════════════════════════════════════════════════════

    @commands.hybrid_command(
        name="hug",
        description="Give someone a warm hug.",
        extras={
            "category": "🎉 Fun",
            "short": "Give someone a hug",
            "usage": "hug [user]",
            "desc": "Hugs another user with a random anime GIF. Leave the user blank to receive a hug yourself.",
            "args": [("user", "Who to hug (optional)")],
            "perms": "None",
            "example": "!hug @Snow\n!hug",
        },
    )
    @app_commands.describe(user="Who to hug (leave blank for a hug yourself)")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def hug(self, ctx: commands.Context, user: Optional[discord.Member] = None):
        await self._do_action(
            ctx,
            "hug",
            user,
            bot_msg="I'm just a bot, but I'd never turn down a hug! Here, have one back! 🤗",
            self_msg="Awh, no one to hug? Don't worry, I've got you. 🤗",
            action_msg="{author} hugs {target}! 🤗",
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  kiss
    # ══════════════════════════════════════════════════════════════════════════

    @commands.hybrid_command(
        name="kiss",
        description="Kiss someone! 💋",
        extras={
            "category": "🎉 Fun",
            "short": "Kiss someone",
            "usage": "kiss [user]",
            "desc": "Kisses another user with a random anime GIF.",
            "args": [("user", "Who to kiss (optional)")],
            "perms": "None",
            "example": "!kiss @Snow",
        },
    )
    @app_commands.describe(user="Who to kiss")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def kiss(self, ctx: commands.Context, user: Optional[discord.Member] = None):
        await self._do_action(
            ctx,
            "kiss",
            user,
            bot_msg="I appreciate the affection, but I'm made of code! 💾💋",
            self_msg="Kissing yourself? Absolute power move. 💋",
            action_msg="{author} kisses {target}! 💋",
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  cheekskiss
    # ══════════════════════════════════════════════════════════════════════════

    @commands.hybrid_command(
        name="cheekskiss",
        description="Give someone a sweet cheek kiss! 😘",
        extras={
            "category": "🎉 Fun",
            "short": "Give a cheek kiss",
            "usage": "cheekskiss [user]",
            "desc": "Gives another user a cheek kiss with a random anime GIF.",
            "args": [("user", "Who to cheek kiss (optional)")],
            "perms": "None",
            "example": "!cheekskiss @Snow",
        },
    )
    @app_commands.describe(user="Who to give a cheek kiss")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def cheekskiss(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ):
        await self._do_action(
            ctx,
            "cheekskiss",
            user,
            bot_msg="A cheek kiss for a bot? How adorable! 😊",
            self_msg="Mwah! Loving yourself is important. 😘",
            action_msg="{author} gives {target} a little cheek kiss! 😘",
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  pat
    # ══════════════════════════════════════════════════════════════════════════

    @commands.hybrid_command(
        name="pat",
        description="Give someone a comforting head pat.",
        extras={
            "category": "🎉 Fun",
            "short": "Head pat someone",
            "usage": "pat [user]",
            "desc": "Pats another user on the head with a random anime GIF.",
            "args": [("user", "Who to pat (optional)")],
            "perms": "None",
            "example": "!pat @Snow",
        },
    )
    @app_commands.describe(user="Who to pat")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pat(self, ctx: commands.Context, user: Optional[discord.Member] = None):
        await self._do_action(
            ctx,
            "pat",
            user,
            bot_msg="*enjoys the headpats* ✨ Thank you!",
            self_msg="Pat yourself on the back — you deserve it! 🥲",
            action_msg="{author} gives {target} a comforting pat! 🥰",
            color=0xFFC0CB,
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  poke
    # ══════════════════════════════════════════════════════════════════════════

    @commands.hybrid_command(
        name="poke",
        description="Poke someone! 👉",
        extras={
            "category": "🎉 Fun",
            "short": "Poke someone",
            "usage": "poke [user]",
            "desc": "Pokes another user with a random anime GIF.",
            "args": [("user", "Who to poke (optional)")],
            "perms": "None",
            "example": "!poke @Snow",
        },
    )
    @app_commands.describe(user="Who to poke")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def poke(self, ctx: commands.Context, user: Optional[discord.Member] = None):
        await self._do_action(
            ctx,
            "poke",
            user,
            bot_msg="Hey! No poking the bot! 🤖👈",
            self_msg="...why are you poking yourself? Are you ok? 👈",
            action_msg="{author} pokes {target}! 👉",
            color=h.YELLOW,
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  boop
    # ══════════════════════════════════════════════════════════════════════════

    @commands.hybrid_command(
        name="boop",
        description="Boop someone's snoot! 👆",
        extras={
            "category": "🎉 Fun",
            "short": "Boop the snoot",
            "usage": "boop [user]",
            "desc": "Boops another user's snoot with a random anime GIF.",
            "args": [("user", "Who to boop (optional)")],
            "perms": "None",
            "example": "!boop @Snow",
        },
    )
    @app_commands.describe(user="Who to boop")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def boop(self, ctx: commands.Context, user: Optional[discord.Member] = None):
        await self._do_action(
            ctx,
            "boop",
            user,
            bot_msg="Boop accepted. Boop logged. Thank you for your boop. 🤖",
            self_msg="Booping your own snoot? Certified legend. 👆",
            action_msg="{author} boops {target}'s snoot! 👆",
            color=0xFFC0CB,
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  wave
    # ══════════════════════════════════════════════════════════════════════════

    @commands.hybrid_command(
        name="wave",
        description="Wave at someone! 👋",
        extras={
            "category": "🎉 Fun",
            "short": "Wave at someone",
            "usage": "wave [user]",
            "desc": "Waves at another user with a random anime GIF.",
            "args": [("user", "Who to wave at (optional)")],
            "perms": "None",
            "example": "!wave @Snow",
        },
    )
    @app_commands.describe(user="Who to wave at")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def wave(self, ctx: commands.Context, user: Optional[discord.Member] = None):
        await self._do_action(
            ctx,
            "wave",
            user,
            bot_msg="👋 Hello there! Hope your day is going great!",
            self_msg="Waving at yourself? I see you, and I wave back! 👋",
            action_msg="{author} waves at {target}! 👋",
            color=h.BLUE,
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  highfive
    # ══════════════════════════════════════════════════════════════════════════

    @commands.hybrid_command(
        name="highfive",
        description="High five someone! 🙌",
        extras={
            "category": "🎉 Fun",
            "short": "High five someone",
            "usage": "highfive [user]",
            "desc": "High fives another user with a random anime GIF.",
            "args": [("user", "Who to high five (optional)")],
            "perms": "None",
            "example": "!highfive @Snow",
        },
    )
    @app_commands.describe(user="Who to high five")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def highfive(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ):
        await self._do_action(
            ctx,
            "highfive",
            user,
            bot_msg="✋ Don't leave me hanging! *high fives back*",
            self_msg="A self-high-five? Respect the commitment. 🙌",
            action_msg="{author} high fives {target}! 🙌",
            color=h.GREEN,
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  ship
    # ══════════════════════════════════════════════════════════════════════════

    @commands.hybrid_command(
        name="ship",
        description="Check the compatibility between two users. 💕",
        extras={
            "category": "🎉 Fun",
            "short": "Ship two users and get a compatibility rating",
            "usage": "ship <user1> <user2>",
            "desc": (
                "Smashes two users' names together into a ship name and gives a "
                "compatibility score (0–100%). The same pair always gets the same score."
            ),
            "args": [
                ("user1", "First user"),
                ("user2", "Second user"),
            ],
            "perms": "None",
            "example": "!ship @Snow @Nano",
        },
    )
    @app_commands.describe(user1="First user", user2="Second user")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def ship(
        self,
        ctx: commands.Context,
        user1: discord.Member,
        user2: discord.Member,
    ):
        # Same user twice
        if user1 == user2:
            e = discord.Embed(
                title="💕 Ship",
                description=(
                    f"**{user1.display_name}** + **{user2.display_name}**\n\n"
                    "Loving yourself is valid, but this is next level. 💯"
                ),
                color=_PINK,
            )
            e.set_footer(text="NanoBot Fun · Results are totally scientific")
            return await ctx.reply(embed=e)

        # Bot is involved
        if ctx.guild.me in (user1, user2):
            e = discord.Embed(
                title="💕 Ship",
                description=(
                    "I'm flattered, but I'm in a committed relationship with my codebase. 💾"
                ),
                color=_PINK,
            )
            e.set_footer(text="NanoBot Fun · Results are totally scientific")
            return await ctx.reply(embed=e)

        score = _ship_score(user1.id, user2.id)
        name = _ship_name(user1.display_name, user2.display_name)
        bar = _progress_bar(score)
        verdict = _ship_verdict(score)

        e = discord.Embed(title=f"💕 {name}", color=_PINK)
        e.add_field(
            name=f"{user1.display_name} × {user2.display_name}",
            value=f"{bar} **{score}%**\n{verdict}",
            inline=False,
        )
        e.set_footer(text="NanoBot Fun · Results are totally scientific")
        await ctx.reply(embed=e)


# ── Setup ──────────────────────────────────────────────────────────────────────


async def setup(bot: commands.Bot):
    await bot.add_cog(Fun(bot))
