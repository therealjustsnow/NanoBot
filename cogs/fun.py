"""
cogs/fun.py
Fun social interaction commands -- mobile-first.

GIFs sourced from nekos.best (no API key required).
Falls back gracefully (text-only) if the API is unavailable.

Slash: /fun hug, /fun slap, /fun ship, /fun 8ball, etc.  (1 top-level slot, 24 subcommands)
Prefix: !hug, !slap, !cry, !dance, !ship, !8ball, etc.   (flat, all 46 commands)

React commands (cry, dance, blush, etc.) are prefix-only to stay under
Discord's 25-subcommand-per-group limit.
"""

import hashlib
import logging
import random
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

_ENDPOINTS: dict[str, str] = {
    "hug": "hug",
    "kiss": "kiss",
    "cheekskiss": "blowkiss",
    "pat": "pat",
    "poke": "poke",
    "boop": "poke",
    "wave": "wave",
    "highfive": "highfive",
    "cuddle": "cuddle",
    "slap": "slap",
    "tickle": "tickle",
    "bite": "bite",
    "kick": "kick",
    "punch": "punch",
    "yeet": "yeet",
    "feed": "feed",
    "handhold": "handhold",
    "handshake": "handshake",
    "peck": "peck",
    "nom": "nom",
    "shoot": "shoot",
    "stare": "stare",
    "cry": "cry",
    "dance": "dance",
    "blush": "blush",
    "smile": "smile",
    "laugh": "laugh",
    "smug": "smug",
    "think": "think",
    "shrug": "shrug",
    "pout": "pout",
    "facepalm": "facepalm",
    "happy": "happy",
    "bored": "bored",
    "sleep": "sleep",
    "thumbsup": "thumbsup",
    "nod": "nod",
    "nope": "nope",
    "wink": "wink",
    "yawn": "yawn",
    "lurk": "lurk",
    "baka": "baka",
    "angry": "angry",
    "run": "run",
}

_8BALL_POSITIVE = [
    "It is certain.",
    "It is decidedly so.",
    "Without a doubt.",
    "Yes, definitely.",
    "You may rely on it.",
    "As I see it, yes.",
    "Most likely.",
    "Outlook good.",
    "Yes.",
    "Signs point to yes.",
]
_8BALL_NEUTRAL = [
    "Reply hazy, try again.",
    "Ask again later.",
    "Better not tell you now.",
    "Cannot predict now.",
    "Concentrate and ask again.",
]
_8BALL_NEGATIVE = [
    "Don't count on it.",
    "My reply is no.",
    "My sources say no.",
    "Outlook not so good.",
    "Very doubtful.",
]


def _ship_score(id1: int, id2: int) -> int:
    key = f"{min(id1, id2)}x{max(id1, id2)}"
    return int.from_bytes(hashlib.md5(key.encode()).digest()[:2], "big") % 101


def _ship_name(n1: str, n2: str) -> str:
    c1 = re.sub(r"[^\w]", "", n1) or n1
    c2 = re.sub(r"[^\w]", "", n2) or n2
    return (c1[: max(1, len(c1) // 2)] + (c2[len(c2) // 2 :] or c2[-1:])).title()


def _progress_bar(pct: int, length: int = 10) -> str:
    filled = round(pct / 100 * length)
    return "\u2593" * filled + "\u2591" * (length - filled)


def _ship_verdict(pct: int) -> str:
    if pct == 100:
        return "\U0001f31f SOULMATES -- a perfect match!"
    if pct >= 81:
        return "\U0001f496 Made for each other!"
    if pct >= 61:
        return "\U0001f495 A pretty good match!"
    if pct >= 41:
        return "\U0001f440 There's potential here..."
    if pct >= 21:
        return "\U0001f62c It's... complicated."
    return "\U0001f494 Not meant to be."


async def _fetch_gif(session: aiohttp.ClientSession, action: str) -> str | None:
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

    # ── shared helpers ────────────────────────────────────────────────────────

    async def _action_embed(
        self,
        guild_me,
        author,
        target,
        action,
        *,
        bot_msg,
        self_msg,
        action_msg,
        color=_PINK,
    ):
        if target is None or target == author:
            desc = self_msg
        elif target == guild_me:
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
        return e

    async def _solo_embed(self, author, action, *, msg, color=_PINK):
        e = discord.Embed(
            description=msg.replace("{author}", f"**{author.display_name}**"),
            color=color,
        )
        if self._session:
            gif = await _fetch_gif(self._session, action)
            if gif:
                e.set_image(url=gif)
        e.set_footer(text="NanoBot Fun")
        return e

    # ══════════════════════════════════════════════════════════════════════════
    #  SLASH: /fun group (24 subcommands, 1 top-level slot)
    #  Social interactions + ship + 8ball
    #  React commands are prefix-only (keeps us under 25 limit)
    # ══════════════════════════════════════════════════════════════════════════

    fun_group = app_commands.Group(
        name="fun",
        description="Fun interaction commands -- hug, kiss, slap, ship, and more!",
        guild_only=True,
    )

    @fun_group.command(name="hug", description="Give someone a warm hug.")
    @app_commands.describe(user="Who to hug")
    async def s_hug(
        self, i: discord.Interaction, user: Optional[discord.Member] = None
    ):
        e = await self._action_embed(
            i.guild.me,
            i.user,
            user,
            "hug",
            bot_msg="I'm just a bot, but I'd never turn down a hug! \U0001f917",
            self_msg="Awh, no one to hug? Don't worry, I've got you. \U0001f917",
            action_msg="{author} hugs {target}! \U0001f917",
        )
        await i.response.send_message(embed=e)

    @fun_group.command(name="kiss", description="Kiss someone! \U0001f48b")
    @app_commands.describe(user="Who to kiss")
    async def s_kiss(
        self, i: discord.Interaction, user: Optional[discord.Member] = None
    ):
        e = await self._action_embed(
            i.guild.me,
            i.user,
            user,
            "kiss",
            bot_msg="I appreciate the affection, but I'm made of code! \U0001f4be\U0001f48b",
            self_msg="Kissing yourself? Absolute power move. \U0001f48b",
            action_msg="{author} kisses {target}! \U0001f48b",
        )
        await i.response.send_message(embed=e)

    @fun_group.command(
        name="cheekskiss", description="Give someone a sweet cheek kiss! \U0001f618"
    )
    @app_commands.describe(user="Who to give a cheek kiss")
    async def s_cheekskiss(
        self, i: discord.Interaction, user: Optional[discord.Member] = None
    ):
        e = await self._action_embed(
            i.guild.me,
            i.user,
            user,
            "cheekskiss",
            bot_msg="A cheek kiss for a bot? How adorable! \U0001f60a",
            self_msg="Mwah! Loving yourself is important. \U0001f618",
            action_msg="{author} gives {target} a little cheek kiss! \U0001f618",
        )
        await i.response.send_message(embed=e)

    @fun_group.command(name="pat", description="Give someone a comforting head pat.")
    @app_commands.describe(user="Who to pat")
    async def s_pat(
        self, i: discord.Interaction, user: Optional[discord.Member] = None
    ):
        e = await self._action_embed(
            i.guild.me,
            i.user,
            user,
            "pat",
            bot_msg="*enjoys the headpats* \u2728 Thank you!",
            self_msg="Pat yourself on the back -- you deserve it! \U0001f972",
            action_msg="{author} gives {target} a comforting pat! \U0001f970",
            color=0xFFC0CB,
        )
        await i.response.send_message(embed=e)

    @fun_group.command(name="poke", description="Poke someone! \U0001f449")
    @app_commands.describe(user="Who to poke")
    async def s_poke(
        self, i: discord.Interaction, user: Optional[discord.Member] = None
    ):
        e = await self._action_embed(
            i.guild.me,
            i.user,
            user,
            "poke",
            bot_msg="Hey! No poking the bot! \U0001f916\U0001f448",
            self_msg="...why are you poking yourself? \U0001f448",
            action_msg="{author} pokes {target}! \U0001f449",
            color=h.YELLOW,
        )
        await i.response.send_message(embed=e)

    @fun_group.command(name="boop", description="Boop someone's snoot! \U0001f446")
    @app_commands.describe(user="Who to boop")
    async def s_boop(
        self, i: discord.Interaction, user: Optional[discord.Member] = None
    ):
        e = await self._action_embed(
            i.guild.me,
            i.user,
            user,
            "boop",
            bot_msg="Boop accepted. Boop logged. \U0001f916",
            self_msg="Booping your own snoot? Certified legend. \U0001f446",
            action_msg="{author} boops {target}'s snoot! \U0001f446",
            color=0xFFC0CB,
        )
        await i.response.send_message(embed=e)

    @fun_group.command(name="wave", description="Wave at someone! \U0001f44b")
    @app_commands.describe(user="Who to wave at")
    async def s_wave(
        self, i: discord.Interaction, user: Optional[discord.Member] = None
    ):
        e = await self._action_embed(
            i.guild.me,
            i.user,
            user,
            "wave",
            bot_msg="\U0001f44b Hello there!",
            self_msg="Waving at yourself? I wave back! \U0001f44b",
            action_msg="{author} waves at {target}! \U0001f44b",
            color=h.BLUE,
        )
        await i.response.send_message(embed=e)

    @fun_group.command(name="highfive", description="High five someone! \U0001f64c")
    @app_commands.describe(user="Who to high five")
    async def s_highfive(
        self, i: discord.Interaction, user: Optional[discord.Member] = None
    ):
        e = await self._action_embed(
            i.guild.me,
            i.user,
            user,
            "highfive",
            bot_msg="\u270b *high fives back*",
            self_msg="A self-high-five? Respect. \U0001f64c",
            action_msg="{author} high fives {target}! \U0001f64c",
            color=h.GREEN,
        )
        await i.response.send_message(embed=e)

    @fun_group.command(name="cuddle", description="Cuddle someone! \U0001f97a")
    @app_commands.describe(user="Who to cuddle")
    async def s_cuddle(
        self, i: discord.Interaction, user: Optional[discord.Member] = None
    ):
        e = await self._action_embed(
            i.guild.me,
            i.user,
            user,
            "cuddle",
            bot_msg="Cuddling a bot? I'm flattered. \U0001f97a",
            self_msg="Self-cuddle activated. \U0001f97a",
            action_msg="{author} cuddles {target}! \U0001f97a",
        )
        await i.response.send_message(embed=e)

    @fun_group.command(name="slap", description="Slap someone! \U0001f44f")
    @app_commands.describe(user="Who to slap")
    async def s_slap(
        self, i: discord.Interaction, user: Optional[discord.Member] = None
    ):
        e = await self._action_embed(
            i.guild.me,
            i.user,
            user,
            "slap",
            bot_msg="You can't slap me, I'm intangible! \U0001f916",
            self_msg="Slapping yourself? That's rough. \U0001f612",
            action_msg="{author} slaps {target}! \U0001f44f",
            color=h.RED,
        )
        await i.response.send_message(embed=e)

    @fun_group.command(name="tickle", description="Tickle someone! \U0001f923")
    @app_commands.describe(user="Who to tickle")
    async def s_tickle(
        self, i: discord.Interaction, user: Optional[discord.Member] = None
    ):
        e = await self._action_embed(
            i.guild.me,
            i.user,
            user,
            "tickle",
            bot_msg="I'm not ticklish! ...or am I? \U0001f914",
            self_msg="Tickling yourself doesn't work. \U0001f923",
            action_msg="{author} tickles {target}! \U0001f923",
            color=h.YELLOW,
        )
        await i.response.send_message(embed=e)

    @fun_group.command(name="bite", description="Bite someone! \U0001f9db")
    @app_commands.describe(user="Who to bite")
    async def s_bite(
        self, i: discord.Interaction, user: Optional[discord.Member] = None
    ):
        e = await self._action_embed(
            i.guild.me,
            i.user,
            user,
            "bite",
            bot_msg="Hope you like the taste of silicon. \U0001f916",
            self_msg="Biting yourself? Ouch! \U0001f9db",
            action_msg="{author} bites {target}! \U0001f9db",
            color=h.RED,
        )
        await i.response.send_message(embed=e)

    @fun_group.command(name="kick", description="Kick someone (for fun)! \U0001f9b5")
    @app_commands.describe(user="Who to kick (for fun!)")
    async def s_kick(
        self, i: discord.Interaction, user: Optional[discord.Member] = None
    ):
        e = await self._action_embed(
            i.guild.me,
            i.user,
            user,
            "kick",
            bot_msg="You can't kick me! I live in the cloud! \u2601\ufe0f",
            self_msg="Kicking yourself? Bold. \U0001f9b5",
            action_msg="{author} kicks {target}! \U0001f9b5",
            color=h.YELLOW,
        )
        await i.response.send_message(embed=e)

    @fun_group.command(name="punch", description="Punch someone! \U0001f91c")
    @app_commands.describe(user="Who to punch")
    async def s_punch(
        self, i: discord.Interaction, user: Optional[discord.Member] = None
    ):
        e = await self._action_embed(
            i.guild.me,
            i.user,
            user,
            "punch",
            bot_msg="*dodges* Try harder! \U0001f916",
            self_msg="Punching yourself? Respect. \U0001f91c",
            action_msg="{author} punches {target}! \U0001f91c",
            color=h.RED,
        )
        await i.response.send_message(embed=e)

    @fun_group.command(name="yeet", description="Yeet someone into orbit! \U0001f680")
    @app_commands.describe(user="Who to yeet")
    async def s_yeet(
        self, i: discord.Interaction, user: Optional[discord.Member] = None
    ):
        e = await self._action_embed(
            i.guild.me,
            i.user,
            user,
            "yeet",
            bot_msg="You can't yeet the un-yeetable! \U0001f916",
            self_msg="Yeeting yourself? Godspeed. \U0001f680",
            action_msg="{author} yeets {target} into orbit! \U0001f680",
            color=h.YELLOW,
        )
        await i.response.send_message(embed=e)

    @fun_group.command(name="feed", description="Feed someone! \U0001f35c")
    @app_commands.describe(user="Who to feed")
    async def s_feed(
        self, i: discord.Interaction, user: Optional[discord.Member] = None
    ):
        e = await self._action_embed(
            i.guild.me,
            i.user,
            user,
            "feed",
            bot_msg="I run on electricity, but thanks! \u26a1",
            self_msg="Feeding yourself? Self-care. \U0001f35c",
            action_msg="{author} feeds {target}! \U0001f35c",
        )
        await i.response.send_message(embed=e)

    @fun_group.command(name="handhold", description="Hold someone's hand! \U0001f91d")
    @app_commands.describe(user="Who to hold hands with")
    async def s_handhold(
        self, i: discord.Interaction, user: Optional[discord.Member] = None
    ):
        e = await self._action_embed(
            i.guild.me,
            i.user,
            user,
            "handhold",
            bot_msg="H-holding hands?! How lewd! \U0001f633",
            self_msg="That's called clasping. \U0001f91d",
            action_msg="{author} holds {target}'s hand! \U0001f91d",
        )
        await i.response.send_message(embed=e)

    @fun_group.command(name="handshake", description="Shake someone's hand! \U0001f91d")
    @app_commands.describe(user="Who to shake hands with")
    async def s_handshake(
        self, i: discord.Interaction, user: Optional[discord.Member] = None
    ):
        e = await self._action_embed(
            i.guild.me,
            i.user,
            user,
            "handshake",
            bot_msg="*firm handshake* Pleasure doing business. \U0001f916",
            self_msg="Deal with yourself sealed. \U0001f91d",
            action_msg="{author} shakes {target}'s hand! \U0001f91d",
            color=h.BLUE,
        )
        await i.response.send_message(embed=e)

    @fun_group.command(name="peck", description="Give someone a quick peck! \U0001f617")
    @app_commands.describe(user="Who to give a peck")
    async def s_peck(
        self, i: discord.Interaction, user: Optional[discord.Member] = None
    ):
        e = await self._action_embed(
            i.guild.me,
            i.user,
            user,
            "peck",
            bot_msg="A peck for a bot? Sweet! \U0001f60a",
            self_msg="Pecking yourself? Cute. \U0001f617",
            action_msg="{author} gives {target} a quick peck! \U0001f617",
        )
        await i.response.send_message(embed=e)

    @fun_group.command(name="nom", description="Nom on someone! \U0001f60b")
    @app_commands.describe(user="Who to nom on")
    async def s_nom(
        self, i: discord.Interaction, user: Optional[discord.Member] = None
    ):
        e = await self._action_embed(
            i.guild.me,
            i.user,
            user,
            "nom",
            bot_msg="I taste like 1s and 0s. \U0001f916",
            self_msg="Snack attack! \U0001f60b",
            action_msg="{author} noms on {target}! \U0001f60b",
        )
        await i.response.send_message(embed=e)

    @fun_group.command(name="shoot", description="Finger guns! \U0001f449")
    @app_commands.describe(user="Who to shoot")
    async def s_shoot(
        self, i: discord.Interaction, user: Optional[discord.Member] = None
    ):
        e = await self._action_embed(
            i.guild.me,
            i.user,
            user,
            "shoot",
            bot_msg="*deflects* No u. \U0001f916",
            self_msg="Pew pew at yourself! \U0001f449",
            action_msg="{author} shoots {target}! Pew pew! \U0001f449",
            color=h.RED,
        )
        await i.response.send_message(embed=e)

    @fun_group.command(name="stare", description="Stare at someone! \U0001f440")
    @app_commands.describe(user="Who to stare at")
    async def s_stare(
        self, i: discord.Interaction, user: Optional[discord.Member] = None
    ):
        e = await self._action_embed(
            i.guild.me,
            i.user,
            user,
            "stare",
            bot_msg="*stares back in binary* \U0001f440",
            self_msg="Introspection is healthy. \U0001f440",
            action_msg="{author} stares at {target}! \U0001f440",
            color=h.BLUE,
        )
        await i.response.send_message(embed=e)

    @fun_group.command(name="ship", description="Ship two users! \U0001f495")
    @app_commands.describe(user1="First user", user2="Second user")
    async def s_ship(
        self, i: discord.Interaction, user1: discord.Member, user2: discord.Member
    ):
        if user1 == user2:
            e = discord.Embed(
                title="\U0001f495 Ship",
                description=f"**{user1.display_name}** + **{user2.display_name}**\n\nLoving yourself is valid, but this is next level. \U0001f4af",
                color=_PINK,
            )
            e.set_footer(text="NanoBot Fun \u00b7 Results are totally scientific")
            return await i.response.send_message(embed=e)
        if i.guild.me in (user1, user2):
            e = discord.Embed(
                title="\U0001f495 Ship",
                description="I'm flattered, but I'm in a committed relationship with my codebase. \U0001f4be",
                color=_PINK,
            )
            e.set_footer(text="NanoBot Fun \u00b7 Results are totally scientific")
            return await i.response.send_message(embed=e)
        score = _ship_score(user1.id, user2.id)
        name = _ship_name(user1.display_name, user2.display_name)
        e = discord.Embed(title=f"\U0001f495 {name}", color=_PINK)
        e.add_field(
            name=f"{user1.display_name} \u00d7 {user2.display_name}",
            value=f"{_progress_bar(score)} **{score}%**\n{_ship_verdict(score)}",
            inline=False,
        )
        e.set_footer(text="NanoBot Fun \u00b7 Results are totally scientific")
        await i.response.send_message(embed=e)

    @fun_group.command(name="8ball", description="Ask the magic 8-ball. \U0001f3b1")
    @app_commands.describe(question="Your yes/no question")
    async def s_8ball(self, i: discord.Interaction, question: str):
        pool = random.choice([_8BALL_POSITIVE, _8BALL_NEUTRAL, _8BALL_NEGATIVE])
        answer = random.choice(pool)
        color = (
            h.GREEN
            if pool is _8BALL_POSITIVE
            else (h.YELLOW if pool is _8BALL_NEUTRAL else h.RED)
        )
        e = discord.Embed(title="\U0001f3b1 Magic 8-Ball", color=color)
        e.add_field(name="Question", value=question[:256], inline=False)
        e.add_field(name="Answer", value=f"**{answer}**", inline=False)
        e.set_footer(text="NanoBot Fun")
        await i.response.send_message(embed=e)

    # ══════════════════════════════════════════════════════════════════════════
    #  PREFIX COMMANDS -- all 46 commands flat (!hug, !cry, !ship, etc.)
    # ══════════════════════════════════════════════════════════════════════════

    def _pfx_action(action, *, bot_msg, self_msg, action_msg, color=_PINK, extras):
        @commands.command(
            name=action if action != "kick" else "funkick",
            aliases=["fk"] if action == "kick" else [],
            extras=extras,
        )
        @commands.cooldown(1, 3, commands.BucketType.user)
        async def cmd(self, ctx, user: Optional[discord.Member] = None):
            e = await self._action_embed(
                ctx.guild.me,
                ctx.author,
                user,
                action,
                bot_msg=bot_msg,
                self_msg=self_msg,
                action_msg=action_msg,
                color=color,
            )
            await ctx.reply(embed=e)

        cmd.__qualname__ = f"Fun.pfx_{action}"
        return cmd

    def _pfx_solo(action, *, msg, color=_PINK, extras):
        @commands.command(name=action, extras=extras)
        @commands.cooldown(1, 3, commands.BucketType.user)
        async def cmd(self, ctx):
            e = await self._solo_embed(ctx.author, action, msg=msg, color=color)
            await ctx.reply(embed=e)

        cmd.__qualname__ = f"Fun.pfx_{action}"
        return cmd

    def _e(short, usage, action_name=None, args=None):
        n = action_name or usage.split()[0]
        return {
            "category": "\U0001f389 Fun",
            "short": short,
            "usage": usage,
            "desc": short + " with a random anime GIF.",
            "args": args or [("user", "Who to target (optional)")],
            "perms": "None",
            "example": f"!{n} @Snow" if args is None else f"!{n}",
        }

    # -- social --
    pfx_hug = _pfx_action(
        "hug",
        bot_msg="I'm just a bot, but I'd never turn down a hug! \U0001f917",
        self_msg="Awh, no one to hug? Don't worry, I've got you. \U0001f917",
        action_msg="{author} hugs {target}! \U0001f917",
        extras=_e("Give someone a hug", "hug [user]"),
    )
    pfx_kiss = _pfx_action(
        "kiss",
        bot_msg="I appreciate the affection, but I'm made of code! \U0001f4be\U0001f48b",
        self_msg="Kissing yourself? Absolute power move. \U0001f48b",
        action_msg="{author} kisses {target}! \U0001f48b",
        extras=_e("Kiss someone", "kiss [user]"),
    )
    pfx_cheekskiss = _pfx_action(
        "cheekskiss",
        bot_msg="A cheek kiss for a bot? How adorable! \U0001f60a",
        self_msg="Mwah! Loving yourself is important. \U0001f618",
        action_msg="{author} gives {target} a little cheek kiss! \U0001f618",
        extras=_e("Give a cheek kiss", "cheekskiss [user]"),
    )
    pfx_pat = _pfx_action(
        "pat",
        bot_msg="*enjoys the headpats* \u2728 Thank you!",
        self_msg="Pat yourself on the back -- you deserve it! \U0001f972",
        action_msg="{author} gives {target} a comforting pat! \U0001f970",
        color=0xFFC0CB,
        extras=_e("Head pat someone", "pat [user]"),
    )
    pfx_poke = _pfx_action(
        "poke",
        bot_msg="Hey! No poking the bot! \U0001f916\U0001f448",
        self_msg="...why are you poking yourself? \U0001f448",
        action_msg="{author} pokes {target}! \U0001f449",
        color=h.YELLOW,
        extras=_e("Poke someone", "poke [user]"),
    )
    pfx_boop = _pfx_action(
        "boop",
        bot_msg="Boop accepted. Boop logged. \U0001f916",
        self_msg="Booping your own snoot? Certified legend. \U0001f446",
        action_msg="{author} boops {target}'s snoot! \U0001f446",
        color=0xFFC0CB,
        extras=_e("Boop the snoot", "boop [user]"),
    )
    pfx_wave = _pfx_action(
        "wave",
        bot_msg="\U0001f44b Hello there!",
        self_msg="Waving at yourself? I wave back! \U0001f44b",
        action_msg="{author} waves at {target}! \U0001f44b",
        color=h.BLUE,
        extras=_e("Wave at someone", "wave [user]"),
    )
    pfx_highfive = _pfx_action(
        "highfive",
        bot_msg="\u270b *high fives back*",
        self_msg="A self-high-five? Respect. \U0001f64c",
        action_msg="{author} high fives {target}! \U0001f64c",
        color=h.GREEN,
        extras=_e("High five someone", "highfive [user]"),
    )
    pfx_cuddle = _pfx_action(
        "cuddle",
        bot_msg="Cuddling a bot? I'm flattered. \U0001f97a",
        self_msg="Self-cuddle activated. \U0001f97a",
        action_msg="{author} cuddles {target}! \U0001f97a",
        extras=_e("Cuddle someone", "cuddle [user]"),
    )
    pfx_slap = _pfx_action(
        "slap",
        bot_msg="You can't slap me, I'm intangible! \U0001f916",
        self_msg="Slapping yourself? That's rough. \U0001f612",
        action_msg="{author} slaps {target}! \U0001f44f",
        color=h.RED,
        extras=_e("Slap someone", "slap [user]"),
    )
    pfx_tickle = _pfx_action(
        "tickle",
        bot_msg="I'm not ticklish! ...or am I? \U0001f914",
        self_msg="Tickling yourself doesn't work. \U0001f923",
        action_msg="{author} tickles {target}! \U0001f923",
        color=h.YELLOW,
        extras=_e("Tickle someone", "tickle [user]"),
    )
    pfx_bite = _pfx_action(
        "bite",
        bot_msg="Hope you like the taste of silicon. \U0001f916",
        self_msg="Biting yourself? Ouch! \U0001f9db",
        action_msg="{author} bites {target}! \U0001f9db",
        color=h.RED,
        extras=_e("Bite someone", "bite [user]"),
    )
    pfx_funkick = _pfx_action(
        "kick",
        bot_msg="You can't kick me! I live in the cloud! \u2601\ufe0f",
        self_msg="Kicking yourself? Bold. \U0001f9b5",
        action_msg="{author} kicks {target}! \U0001f9b5",
        color=h.YELLOW,
        extras=_e("Fun-kick someone", "funkick [user]", "funkick"),
    )
    pfx_punch = _pfx_action(
        "punch",
        bot_msg="*dodges* Try harder! \U0001f916",
        self_msg="Punching yourself? Respect. \U0001f91c",
        action_msg="{author} punches {target}! \U0001f91c",
        color=h.RED,
        extras=_e("Punch someone", "punch [user]"),
    )
    pfx_yeet = _pfx_action(
        "yeet",
        bot_msg="You can't yeet the un-yeetable! \U0001f916",
        self_msg="Yeeting yourself? Godspeed. \U0001f680",
        action_msg="{author} yeets {target} into orbit! \U0001f680",
        color=h.YELLOW,
        extras=_e("Yeet someone", "yeet [user]"),
    )
    pfx_feed = _pfx_action(
        "feed",
        bot_msg="I run on electricity, but thanks! \u26a1",
        self_msg="Feeding yourself? Self-care. \U0001f35c",
        action_msg="{author} feeds {target}! \U0001f35c",
        extras=_e("Feed someone", "feed [user]"),
    )
    pfx_handhold = _pfx_action(
        "handhold",
        bot_msg="H-holding hands?! How lewd! \U0001f633",
        self_msg="That's called clasping. \U0001f91d",
        action_msg="{author} holds {target}'s hand! \U0001f91d",
        extras=_e("Hold someone's hand", "handhold [user]"),
    )
    pfx_handshake = _pfx_action(
        "handshake",
        bot_msg="*firm handshake* Pleasure doing business. \U0001f916",
        self_msg="Deal with yourself sealed. \U0001f91d",
        action_msg="{author} shakes {target}'s hand! \U0001f91d",
        color=h.BLUE,
        extras=_e("Shake someone's hand", "handshake [user]"),
    )
    pfx_peck = _pfx_action(
        "peck",
        bot_msg="A peck for a bot? Sweet! \U0001f60a",
        self_msg="Pecking yourself? Cute. \U0001f617",
        action_msg="{author} gives {target} a quick peck! \U0001f617",
        extras=_e("Give a quick peck", "peck [user]"),
    )
    pfx_nom = _pfx_action(
        "nom",
        bot_msg="I taste like 1s and 0s. \U0001f916",
        self_msg="Snack attack! \U0001f60b",
        action_msg="{author} noms on {target}! \U0001f60b",
        extras=_e("Nom on someone", "nom [user]"),
    )
    pfx_shoot = _pfx_action(
        "shoot",
        bot_msg="*deflects* No u. \U0001f916",
        self_msg="Pew pew at yourself! \U0001f449",
        action_msg="{author} shoots {target}! Pew pew! \U0001f449",
        color=h.RED,
        extras=_e("Finger-gun someone", "shoot [user]"),
    )
    pfx_stare = _pfx_action(
        "stare",
        bot_msg="*stares back in binary* \U0001f440",
        self_msg="Introspection is healthy. \U0001f440",
        action_msg="{author} stares at {target}! \U0001f440",
        color=h.BLUE,
        extras=_e("Stare at someone", "stare [user]"),
    )

    # -- react (prefix-only) --
    pfx_cry = _pfx_solo(
        "cry", msg="{author} is crying... \U0001f622", extras=_e("Cry", "cry", args=[])
    )
    pfx_dance = _pfx_solo(
        "dance",
        msg="{author} is dancing! \U0001f57a",
        color=h.GREEN,
        extras=_e("Dance", "dance", args=[]),
    )
    pfx_blush = _pfx_solo(
        "blush",
        msg="{author} is blushing! \U0001f633",
        extras=_e("Blush", "blush", args=[]),
    )
    pfx_smile = _pfx_solo(
        "smile",
        msg="{author} smiles! \U0001f60a",
        color=h.GREEN,
        extras=_e("Smile", "smile", args=[]),
    )
    pfx_laugh = _pfx_solo(
        "laugh",
        msg="{author} is laughing! \U0001f602",
        color=h.YELLOW,
        extras=_e("Laugh", "laugh", args=[]),
    )
    pfx_smug = _pfx_solo(
        "smug",
        msg="{author} looks smug. \U0001f60f",
        extras=_e("Look smug", "smug", args=[]),
    )
    pfx_think = _pfx_solo(
        "think",
        msg="{author} is thinking... \U0001f914",
        color=h.BLUE,
        extras=_e("Think", "think", args=[]),
    )
    pfx_shrug = _pfx_solo(
        "shrug", msg="{author} shrugs. \U0001f937", extras=_e("Shrug", "shrug", args=[])
    )
    pfx_pout = _pfx_solo(
        "pout",
        msg="{author} is pouting! \U0001f61e",
        extras=_e("Pout", "pout", args=[]),
    )
    pfx_facepalm = _pfx_solo(
        "facepalm",
        msg="{author} facepalms. \U0001f926",
        color=h.YELLOW,
        extras=_e("Facepalm", "facepalm", args=[]),
    )
    pfx_happy = _pfx_solo(
        "happy",
        msg="{author} is happy! \U0001f60a",
        color=h.GREEN,
        extras=_e("Be happy", "happy", args=[]),
    )
    pfx_bored = _pfx_solo(
        "bored",
        msg="{author} is bored... \U0001f971",
        extras=_e("Be bored", "bored", args=[]),
    )
    pfx_sleep = _pfx_solo(
        "sleep",
        msg="{author} is sleeping... zzZ \U0001f634",
        color=h.BLUE,
        extras=_e("Sleep", "sleep", args=[]),
    )
    pfx_thumbsup = _pfx_solo(
        "thumbsup",
        msg="{author} gives a thumbs up! \U0001f44d",
        color=h.GREEN,
        extras=_e("Thumbs up", "thumbsup", args=[]),
    )
    pfx_nod = _pfx_solo(
        "nod", msg="{author} nods. \U0001f642", extras=_e("Nod", "nod", args=[])
    )
    pfx_nope = _pfx_solo(
        "nope",
        msg="{author} says NOPE. \U0001f645",
        color=h.RED,
        extras=_e("Nope", "nope", args=[]),
    )
    pfx_wink = _pfx_solo(
        "wink", msg="{author} winks! \U0001f609", extras=_e("Wink", "wink", args=[])
    )
    pfx_yawn = _pfx_solo(
        "yawn", msg="{author} yawns... \U0001f971", extras=_e("Yawn", "yawn", args=[])
    )
    pfx_lurk = _pfx_solo(
        "lurk",
        msg="{author} is lurking... \U0001f440",
        color=h.BLUE,
        extras=_e("Lurk", "lurk", args=[]),
    )
    pfx_baka = _pfx_solo(
        "baka",
        msg="{author} yells BAKA! \U0001f621",
        color=h.RED,
        extras=_e("Baka!", "baka", args=[]),
    )
    pfx_angry = _pfx_solo(
        "angry",
        msg="{author} is angry! \U0001f620",
        color=h.RED,
        extras=_e("Be angry", "angry", args=[]),
    )
    pfx_run = _pfx_solo(
        "run",
        msg="{author} is running away! \U0001f3c3",
        color=h.YELLOW,
        extras=_e("Run away", "run", args=[]),
    )

    # -- other --
    @commands.command(
        name="ship",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Ship two users",
            "usage": "ship <user1> <user2>",
            "desc": "Smashes two users' names together and gives a compatibility score.",
            "args": [("user1", "First user"), ("user2", "Second user")],
            "perms": "None",
            "example": "!ship @Snow @Nano",
        },
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def pfx_ship(self, ctx, user1: discord.Member, user2: discord.Member):
        if user1 == user2:
            e = discord.Embed(
                title="\U0001f495 Ship",
                description=f"**{user1.display_name}** + **{user2.display_name}**\n\nLoving yourself is valid, but this is next level. \U0001f4af",
                color=_PINK,
            )
            e.set_footer(text="NanoBot Fun \u00b7 Results are totally scientific")
            return await ctx.reply(embed=e)
        if ctx.guild.me in (user1, user2):
            e = discord.Embed(
                title="\U0001f495 Ship",
                description="I'm flattered, but I'm in a committed relationship with my codebase. \U0001f4be",
                color=_PINK,
            )
            e.set_footer(text="NanoBot Fun \u00b7 Results are totally scientific")
            return await ctx.reply(embed=e)
        score = _ship_score(user1.id, user2.id)
        name = _ship_name(user1.display_name, user2.display_name)
        e = discord.Embed(title=f"\U0001f495 {name}", color=_PINK)
        e.add_field(
            name=f"{user1.display_name} \u00d7 {user2.display_name}",
            value=f"{_progress_bar(score)} **{score}%**\n{_ship_verdict(score)}",
            inline=False,
        )
        e.set_footer(text="NanoBot Fun \u00b7 Results are totally scientific")
        await ctx.reply(embed=e)

    @commands.command(
        name="8ball",
        aliases=["eightball", "magic8ball"],
        extras={
            "category": "\U0001f389 Fun",
            "short": "Ask the magic 8-ball",
            "usage": "8ball <question>",
            "desc": "Ask a yes/no question and the magic 8-ball will answer.",
            "args": [("question", "Your question")],
            "perms": "None",
            "example": "!8ball Will I pass my exam?",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_8ball(self, ctx, *, question: str):
        pool = random.choice([_8BALL_POSITIVE, _8BALL_NEUTRAL, _8BALL_NEGATIVE])
        answer = random.choice(pool)
        color = (
            h.GREEN
            if pool is _8BALL_POSITIVE
            else (h.YELLOW if pool is _8BALL_NEUTRAL else h.RED)
        )
        e = discord.Embed(title="\U0001f3b1 Magic 8-Ball", color=color)
        e.add_field(name="Question", value=question[:256], inline=False)
        e.add_field(name="Answer", value=f"**{answer}**", inline=False)
        e.set_footer(text="NanoBot Fun")
        await ctx.reply(embed=e)


async def setup(bot: commands.Bot):
    await bot.add_cog(Fun(bot))
