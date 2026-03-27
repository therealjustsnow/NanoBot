"""
cogs/fun.py
Fun social interaction commands -- mobile-first.

GIFs sourced from nekos.best (no API key required).
Falls back gracefully (text-only) if the API is unavailable.

Slash commands live under the /fun group (counts as 1 top-level slash).
Prefix commands remain flat: !hug, !slap, !8ball, etc.

Interaction commands (target a user):
  hug, kiss, cheekskiss, pat, poke, boop, wave, highfive,
  cuddle, slap, tickle, bite, kick, punch, yeet, feed,
  handhold, handshake, peck, nom, shoot, stare

Solo reaction commands (no target):
  cry, dance, blush, smile, laugh, smug, think, shrug,
  pout, facepalm, happy, bored, sleep, thumbsup, nod,
  nope, wink, yawn, lurk, baka, angry, run

Other:
  ship  <user1> <user2>  -- compatibility rating
  8ball <question>        -- magic 8-ball
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
    "cheekskiss": "kiss",
    "pat": "pat",
    "poke": "poke",
    "boop": "pat",
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
    digest = hashlib.md5(key.encode()).digest()
    return int.from_bytes(digest[:2], "big") % 101


def _ship_name(n1: str, n2: str) -> str:
    clean1 = re.sub(r"[^\w]", "", n1) or n1
    clean2 = re.sub(r"[^\w]", "", n2) or n2
    half1 = clean1[: max(1, len(clean1) // 2)]
    half2 = clean2[len(clean2) // 2 :] or clean2[-1:]
    return (half1 + half2).title()


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

    # ── shared handlers ───────────────────────────────────────────────────────

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

    async def _do_solo(
        self,
        ctx: commands.Context,
        action: str,
        *,
        msg: str,
        color: int = _PINK,
    ) -> None:
        e = discord.Embed(
            description=msg.replace("{author}", f"**{ctx.author.display_name}**"),
            color=color,
        )
        if self._session:
            gif = await _fetch_gif(self._session, action)
            if gif:
                e.set_image(url=gif)
        e.set_footer(text="NanoBot Fun")
        await ctx.reply(embed=e)

    # ══════════════════════════════════════════════════════════════════════════
    #  /fun group -- 1 top-level slash command slot
    # ══════════════════════════════════════════════════════════════════════════

    fun = commands.HybridGroup(
        name="fun",
        description="Fun social interaction commands.",
        fallback="help",
    )

    # ── interaction subcommands ───────────────────────────────────────────────

    @fun.command(name="hug", description="Give someone a warm hug.")
    @app_commands.describe(user="Who to hug (leave blank for a hug yourself)")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_hug(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ):
        await self._do_action(
            ctx,
            "hug",
            user,
            bot_msg="I'm just a bot, but I'd never turn down a hug! Here, have one back! \U0001f917",
            self_msg="Awh, no one to hug? Don't worry, I've got you. \U0001f917",
            action_msg="{author} hugs {target}! \U0001f917",
        )

    @fun.command(name="kiss", description="Kiss someone! \U0001f48b")
    @app_commands.describe(user="Who to kiss")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_kiss(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ):
        await self._do_action(
            ctx,
            "kiss",
            user,
            bot_msg="I appreciate the affection, but I'm made of code! \U0001f4be\U0001f48b",
            self_msg="Kissing yourself? Absolute power move. \U0001f48b",
            action_msg="{author} kisses {target}! \U0001f48b",
        )

    @fun.command(
        name="cheekskiss", description="Give someone a sweet cheek kiss! \U0001f618"
    )
    @app_commands.describe(user="Who to give a cheek kiss")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_cheekskiss(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ):
        await self._do_action(
            ctx,
            "cheekskiss",
            user,
            bot_msg="A cheek kiss for a bot? How adorable! \U0001f60a",
            self_msg="Mwah! Loving yourself is important. \U0001f618",
            action_msg="{author} gives {target} a little cheek kiss! \U0001f618",
        )

    @fun.command(name="pat", description="Give someone a comforting head pat.")
    @app_commands.describe(user="Who to pat")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_pat(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ):
        await self._do_action(
            ctx,
            "pat",
            user,
            bot_msg="*enjoys the headpats* \u2728 Thank you!",
            self_msg="Pat yourself on the back -- you deserve it! \U0001f972",
            action_msg="{author} gives {target} a comforting pat! \U0001f970",
            color=0xFFC0CB,
        )

    @fun.command(name="poke", description="Poke someone! \U0001f449")
    @app_commands.describe(user="Who to poke")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_poke(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ):
        await self._do_action(
            ctx,
            "poke",
            user,
            bot_msg="Hey! No poking the bot! \U0001f916\U0001f448",
            self_msg="...why are you poking yourself? Are you ok? \U0001f448",
            action_msg="{author} pokes {target}! \U0001f449",
            color=h.YELLOW,
        )

    @fun.command(name="boop", description="Boop someone's snoot! \U0001f446")
    @app_commands.describe(user="Who to boop")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_boop(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ):
        await self._do_action(
            ctx,
            "boop",
            user,
            bot_msg="Boop accepted. Boop logged. Thank you for your boop. \U0001f916",
            self_msg="Booping your own snoot? Certified legend. \U0001f446",
            action_msg="{author} boops {target}'s snoot! \U0001f446",
            color=0xFFC0CB,
        )

    @fun.command(name="wave", description="Wave at someone! \U0001f44b")
    @app_commands.describe(user="Who to wave at")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_wave(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ):
        await self._do_action(
            ctx,
            "wave",
            user,
            bot_msg="\U0001f44b Hello there! Hope your day is going great!",
            self_msg="Waving at yourself? I see you, and I wave back! \U0001f44b",
            action_msg="{author} waves at {target}! \U0001f44b",
            color=h.BLUE,
        )

    @fun.command(name="highfive", description="High five someone! \U0001f64c")
    @app_commands.describe(user="Who to high five")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_highfive(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ):
        await self._do_action(
            ctx,
            "highfive",
            user,
            bot_msg="\u270b Don't leave me hanging! *high fives back*",
            self_msg="A self-high-five? Respect the commitment. \U0001f64c",
            action_msg="{author} high fives {target}! \U0001f64c",
            color=h.GREEN,
        )

    @fun.command(name="cuddle", description="Cuddle someone! \U0001f97a")
    @app_commands.describe(user="Who to cuddle")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_cuddle(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ):
        await self._do_action(
            ctx,
            "cuddle",
            user,
            bot_msg="Cuddling a bot? I'm flattered, truly. \U0001f97a",
            self_msg="Self-cuddle activated. You deserve the warmth. \U0001f97a",
            action_msg="{author} cuddles {target}! \U0001f97a",
        )

    @fun.command(name="slap", description="Slap someone! \U0001f44f")
    @app_commands.describe(user="Who to slap")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_slap(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ):
        await self._do_action(
            ctx,
            "slap",
            user,
            bot_msg="You can't slap me, I'm intangible! \U0001f916",
            self_msg="Slapping yourself? That's rough. \U0001f612",
            action_msg="{author} slaps {target}! \U0001f44f",
            color=h.RED,
        )

    @fun.command(name="tickle", description="Tickle someone! \U0001f923")
    @app_commands.describe(user="Who to tickle")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_tickle(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ):
        await self._do_action(
            ctx,
            "tickle",
            user,
            bot_msg="I'm not ticklish! ...or am I? \U0001f914",
            self_msg="Tickling yourself doesn't work, trust me. \U0001f923",
            action_msg="{author} tickles {target}! \U0001f923",
            color=h.YELLOW,
        )

    @fun.command(name="bite", description="Bite someone! \U0001f9db")
    @app_commands.describe(user="Who to bite")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_bite(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ):
        await self._do_action(
            ctx,
            "bite",
            user,
            bot_msg="Biting a bot? Hope you like the taste of silicon. \U0001f916",
            self_msg="Biting yourself? Ouch! \U0001f9db",
            action_msg="{author} bites {target}! \U0001f9db",
            color=h.RED,
        )

    @fun.command(
        name="kick", description="Kick someone (for fun, not moderation)! \U0001f9b5"
    )
    @app_commands.describe(user="Who to kick (for fun!)")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_kick(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ):
        await self._do_action(
            ctx,
            "kick",
            user,
            bot_msg="You can't kick me! I live in the cloud! \u2601\ufe0f",
            self_msg="Kicking yourself? Bold move. \U0001f9b5",
            action_msg="{author} kicks {target}! \U0001f9b5",
            color=h.YELLOW,
        )

    @fun.command(name="punch", description="Punch someone! \U0001f91c")
    @app_commands.describe(user="Who to punch")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_punch(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ):
        await self._do_action(
            ctx,
            "punch",
            user,
            bot_msg="*dodges* You'll have to try harder than that! \U0001f916",
            self_msg="Punching yourself? I respect the dedication. \U0001f91c",
            action_msg="{author} punches {target}! \U0001f91c",
            color=h.RED,
        )

    @fun.command(name="yeet", description="Yeet someone into orbit! \U0001f680")
    @app_commands.describe(user="Who to yeet")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_yeet(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ):
        await self._do_action(
            ctx,
            "yeet",
            user,
            bot_msg="You can't yeet the un-yeetable! \U0001f916",
            self_msg="Yeeting yourself? Godspeed. \U0001f680",
            action_msg="{author} yeets {target} into orbit! \U0001f680",
            color=h.YELLOW,
        )

    @fun.command(name="feed", description="Feed someone! \U0001f35c")
    @app_commands.describe(user="Who to feed")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_feed(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ):
        await self._do_action(
            ctx,
            "feed",
            user,
            bot_msg="I run on electricity, but thanks for the thought! \u26a1",
            self_msg="Feeding yourself? Self-care at its finest. \U0001f35c",
            action_msg="{author} feeds {target}! \U0001f35c",
        )

    @fun.command(name="handhold", description="Hold someone's hand! \U0001f91d")
    @app_commands.describe(user="Who to hold hands with")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_handhold(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ):
        await self._do_action(
            ctx,
            "handhold",
            user,
            bot_msg="H-holding hands?! How lewd! \U0001f633",
            self_msg="Holding your own hand? That's called clasping. \U0001f91d",
            action_msg="{author} holds {target}'s hand! \U0001f91d",
        )

    @fun.command(name="handshake", description="Shake someone's hand! \U0001f91d")
    @app_commands.describe(user="Who to shake hands with")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_handshake(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ):
        await self._do_action(
            ctx,
            "handshake",
            user,
            bot_msg="*firm handshake* Pleasure doing business. \U0001f916",
            self_msg="Shaking your own hand? Deal with yourself sealed. \U0001f91d",
            action_msg="{author} shakes {target}'s hand! \U0001f91d",
            color=h.BLUE,
        )

    @fun.command(name="peck", description="Give someone a quick peck! \U0001f617")
    @app_commands.describe(user="Who to give a peck")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_peck(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ):
        await self._do_action(
            ctx,
            "peck",
            user,
            bot_msg="A peck for a bot? How sweet! \U0001f60a",
            self_msg="Pecking yourself in the mirror? Cute. \U0001f617",
            action_msg="{author} gives {target} a quick peck! \U0001f617",
        )

    @fun.command(name="nom", description="Nom on someone! \U0001f60b")
    @app_commands.describe(user="Who to nom on")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_nom(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ):
        await self._do_action(
            ctx,
            "nom",
            user,
            bot_msg="Nomming a bot? I taste like 1s and 0s. \U0001f916",
            self_msg="Nomming on yourself? Snack attack! \U0001f60b",
            action_msg="{author} noms on {target}! \U0001f60b",
        )

    @fun.command(
        name="shoot", description="Shoot someone (with finger guns)! \U0001f449"
    )
    @app_commands.describe(user="Who to shoot (finger guns!)")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_shoot(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ):
        await self._do_action(
            ctx,
            "shoot",
            user,
            bot_msg="*deflects with a mirror* No u. \U0001f916",
            self_msg="Shooting yourself... with finger guns? Pew pew! \U0001f449",
            action_msg="{author} shoots {target}! Pew pew! \U0001f449",
            color=h.RED,
        )

    @fun.command(name="stare", description="Stare at someone! \U0001f440")
    @app_commands.describe(user="Who to stare at")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_stare(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ):
        await self._do_action(
            ctx,
            "stare",
            user,
            bot_msg="*stares back in binary* 01001000 01001001 \U0001f440",
            self_msg="Staring at yourself? Introspection is healthy. \U0001f440",
            action_msg="{author} stares at {target}! \U0001f440",
            color=h.BLUE,
        )

    # ── solo reaction subcommands ─────────────────────────────────────────────

    @fun.command(name="cry", description="Have a good cry. \U0001f622")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_cry(self, ctx: commands.Context):
        await self._do_solo(ctx, "cry", msg="{author} is crying... \U0001f622")

    @fun.command(name="dance", description="Show off your moves! \U0001f57a")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_dance(self, ctx: commands.Context):
        await self._do_solo(
            ctx, "dance", msg="{author} is dancing! \U0001f57a", color=h.GREEN
        )

    @fun.command(name="blush", description="Blush! \U0001f633")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_blush(self, ctx: commands.Context):
        await self._do_solo(ctx, "blush", msg="{author} is blushing! \U0001f633")

    @fun.command(name="smile", description="Smile! \U0001f60a")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_smile(self, ctx: commands.Context):
        await self._do_solo(
            ctx, "smile", msg="{author} smiles! \U0001f60a", color=h.GREEN
        )

    @fun.command(name="laugh", description="Laugh out loud! \U0001f602")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_laugh(self, ctx: commands.Context):
        await self._do_solo(
            ctx, "laugh", msg="{author} is laughing! \U0001f602", color=h.YELLOW
        )

    @fun.command(name="smug", description="Look smug. \U0001f60f")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_smug(self, ctx: commands.Context):
        await self._do_solo(ctx, "smug", msg="{author} looks smug. \U0001f60f")

    @fun.command(name="think", description="Think hard about something. \U0001f914")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_think(self, ctx: commands.Context):
        await self._do_solo(
            ctx, "think", msg="{author} is thinking... \U0001f914", color=h.BLUE
        )

    @fun.command(name="shrug", description="Shrug it off. \U0001f937")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_shrug(self, ctx: commands.Context):
        await self._do_solo(ctx, "shrug", msg="{author} shrugs. \U0001f937")

    @fun.command(name="pout", description="Pout! \U0001f61e")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_pout(self, ctx: commands.Context):
        await self._do_solo(ctx, "pout", msg="{author} is pouting! \U0001f61e")

    @fun.command(name="facepalm", description="Facepalm. \U0001f926")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_facepalm(self, ctx: commands.Context):
        await self._do_solo(
            ctx, "facepalm", msg="{author} facepalms. \U0001f926", color=h.YELLOW
        )

    @fun.command(name="happy", description="Express your happiness! \U0001f60a")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_happy(self, ctx: commands.Context):
        await self._do_solo(
            ctx, "happy", msg="{author} is happy! \U0001f60a", color=h.GREEN
        )

    @fun.command(name="bored", description="Express your boredom. \U0001f971")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_bored(self, ctx: commands.Context):
        await self._do_solo(ctx, "bored", msg="{author} is bored... \U0001f971")

    @fun.command(name="sleep", description="Go to sleep. \U0001f634")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_sleep(self, ctx: commands.Context):
        await self._do_solo(
            ctx, "sleep", msg="{author} is sleeping... zzZ \U0001f634", color=h.BLUE
        )

    @fun.command(name="thumbsup", description="Give a thumbs up! \U0001f44d")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_thumbsup(self, ctx: commands.Context):
        await self._do_solo(
            ctx, "thumbsup", msg="{author} gives a thumbs up! \U0001f44d", color=h.GREEN
        )

    @fun.command(name="nod", description="Nod in agreement. \U0001f642")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_nod(self, ctx: commands.Context):
        await self._do_solo(ctx, "nod", msg="{author} nods. \U0001f642")

    @fun.command(name="nope", description="Nope out. \U0001f645")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_nope(self, ctx: commands.Context):
        await self._do_solo(
            ctx, "nope", msg="{author} says NOPE. \U0001f645", color=h.RED
        )

    @fun.command(name="wink", description="Wink! \U0001f609")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_wink(self, ctx: commands.Context):
        await self._do_solo(ctx, "wink", msg="{author} winks! \U0001f609")

    @fun.command(name="yawn", description="Yawn. \U0001f971")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_yawn(self, ctx: commands.Context):
        await self._do_solo(ctx, "yawn", msg="{author} yawns... \U0001f971")

    @fun.command(name="lurk", description="Lurk in the shadows. \U0001f440")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_lurk(self, ctx: commands.Context):
        await self._do_solo(
            ctx, "lurk", msg="{author} is lurking... \U0001f440", color=h.BLUE
        )

    @fun.command(name="baka", description="Call someone a baka! \U0001f621")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_baka(self, ctx: commands.Context):
        await self._do_solo(
            ctx, "baka", msg="{author} yells BAKA! \U0001f621", color=h.RED
        )

    @fun.command(name="angry", description="Express your anger! \U0001f620")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_angry(self, ctx: commands.Context):
        await self._do_solo(
            ctx, "angry", msg="{author} is angry! \U0001f620", color=h.RED
        )

    @fun.command(name="run", description="Run away! \U0001f3c3")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_run(self, ctx: commands.Context):
        await self._do_solo(
            ctx, "run", msg="{author} is running away! \U0001f3c3", color=h.YELLOW
        )

    # ── other fun subcommands ─────────────────────────────────────────────────

    @fun.command(
        name="ship", description="Check the compatibility between two users. \U0001f495"
    )
    @app_commands.describe(user1="First user", user2="Second user")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def fun_ship(
        self, ctx: commands.Context, user1: discord.Member, user2: discord.Member
    ):
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
        bar = _progress_bar(score)
        verdict = _ship_verdict(score)
        e = discord.Embed(title=f"\U0001f495 {name}", color=_PINK)
        e.add_field(
            name=f"{user1.display_name} \u00d7 {user2.display_name}",
            value=f"{bar} **{score}%**\n{verdict}",
            inline=False,
        )
        e.set_footer(text="NanoBot Fun \u00b7 Results are totally scientific")
        await ctx.reply(embed=e)

    @fun.command(
        name="8ball", description="Ask the magic 8-ball a question. \U0001f3b1"
    )
    @app_commands.describe(question="Your yes/no question")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fun_8ball(self, ctx: commands.Context, *, question: str):
        pool = random.choice([_8BALL_POSITIVE, _8BALL_NEUTRAL, _8BALL_NEGATIVE])
        answer = random.choice(pool)
        if pool is _8BALL_POSITIVE:
            color = h.GREEN
        elif pool is _8BALL_NEUTRAL:
            color = h.YELLOW
        else:
            color = h.RED
        e = discord.Embed(title="\U0001f3b1 Magic 8-Ball", color=color)
        e.add_field(name="Question", value=question[:256], inline=False)
        e.add_field(name="Answer", value=f"**{answer}**", inline=False)
        e.set_footer(text="NanoBot Fun")
        await ctx.reply(embed=e)

    # ══════════════════════════════════════════════════════════════════════════
    #  PREFIX-ONLY ALIASES
    #  !hug, !slap, etc. -- commands.command (not hybrid) so no slash slot.
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command(
        name="hug",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Give someone a hug",
            "usage": "hug [user]",
            "desc": "Hugs another user with a random anime GIF.",
            "args": [("user", "Who to hug (optional)")],
            "perms": "None",
            "example": "!hug @Snow\n!hug",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_hug(self, ctx, user: Optional[discord.Member] = None):
        await self.fun_hug(ctx, user=user)

    @commands.command(
        name="kiss",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Kiss someone",
            "usage": "kiss [user]",
            "desc": "Kisses another user with a random anime GIF.",
            "args": [("user", "Who to kiss (optional)")],
            "perms": "None",
            "example": "!kiss @Snow",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_kiss(self, ctx, user: Optional[discord.Member] = None):
        await self.fun_kiss(ctx, user=user)

    @commands.command(
        name="cheekskiss",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Give a cheek kiss",
            "usage": "cheekskiss [user]",
            "desc": "Gives another user a cheek kiss with a random anime GIF.",
            "args": [("user", "Who to cheek kiss (optional)")],
            "perms": "None",
            "example": "!cheekskiss @Snow",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_cheekskiss(self, ctx, user: Optional[discord.Member] = None):
        await self.fun_cheekskiss(ctx, user=user)

    @commands.command(
        name="pat",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Head pat someone",
            "usage": "pat [user]",
            "desc": "Pats another user on the head with a random anime GIF.",
            "args": [("user", "Who to pat (optional)")],
            "perms": "None",
            "example": "!pat @Snow",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_pat(self, ctx, user: Optional[discord.Member] = None):
        await self.fun_pat(ctx, user=user)

    @commands.command(
        name="poke",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Poke someone",
            "usage": "poke [user]",
            "desc": "Pokes another user with a random anime GIF.",
            "args": [("user", "Who to poke (optional)")],
            "perms": "None",
            "example": "!poke @Snow",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_poke(self, ctx, user: Optional[discord.Member] = None):
        await self.fun_poke(ctx, user=user)

    @commands.command(
        name="boop",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Boop the snoot",
            "usage": "boop [user]",
            "desc": "Boops another user's snoot with a random anime GIF.",
            "args": [("user", "Who to boop (optional)")],
            "perms": "None",
            "example": "!boop @Snow",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_boop(self, ctx, user: Optional[discord.Member] = None):
        await self.fun_boop(ctx, user=user)

    @commands.command(
        name="wave",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Wave at someone",
            "usage": "wave [user]",
            "desc": "Waves at another user with a random anime GIF.",
            "args": [("user", "Who to wave at (optional)")],
            "perms": "None",
            "example": "!wave @Snow",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_wave(self, ctx, user: Optional[discord.Member] = None):
        await self.fun_wave(ctx, user=user)

    @commands.command(
        name="highfive",
        extras={
            "category": "\U0001f389 Fun",
            "short": "High five someone",
            "usage": "highfive [user]",
            "desc": "High fives another user with a random anime GIF.",
            "args": [("user", "Who to high five (optional)")],
            "perms": "None",
            "example": "!highfive @Snow",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_highfive(self, ctx, user: Optional[discord.Member] = None):
        await self.fun_highfive(ctx, user=user)

    @commands.command(
        name="cuddle",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Cuddle someone",
            "usage": "cuddle [user]",
            "desc": "Cuddles another user with a random anime GIF.",
            "args": [("user", "Who to cuddle (optional)")],
            "perms": "None",
            "example": "!cuddle @Snow",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_cuddle(self, ctx, user: Optional[discord.Member] = None):
        await self.fun_cuddle(ctx, user=user)

    @commands.command(
        name="slap",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Slap someone",
            "usage": "slap [user]",
            "desc": "Slaps another user with a random anime GIF.",
            "args": [("user", "Who to slap (optional)")],
            "perms": "None",
            "example": "!slap @Snow",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_slap(self, ctx, user: Optional[discord.Member] = None):
        await self.fun_slap(ctx, user=user)

    @commands.command(
        name="tickle",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Tickle someone",
            "usage": "tickle [user]",
            "desc": "Tickles another user with a random anime GIF.",
            "args": [("user", "Who to tickle (optional)")],
            "perms": "None",
            "example": "!tickle @Snow",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_tickle(self, ctx, user: Optional[discord.Member] = None):
        await self.fun_tickle(ctx, user=user)

    @commands.command(
        name="bite",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Bite someone",
            "usage": "bite [user]",
            "desc": "Bites another user with a random anime GIF.",
            "args": [("user", "Who to bite (optional)")],
            "perms": "None",
            "example": "!bite @Snow",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_bite(self, ctx, user: Optional[discord.Member] = None):
        await self.fun_bite(ctx, user=user)

    @commands.command(
        name="funkick",
        aliases=["fk"],
        extras={
            "category": "\U0001f389 Fun",
            "short": "Fun-kick someone",
            "usage": "funkick [user]",
            "desc": "Kicks another user with a random anime GIF. Just for fun, not moderation.",
            "args": [("user", "Who to kick (optional)")],
            "perms": "None",
            "example": "!funkick @Snow",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_funkick(self, ctx, user: Optional[discord.Member] = None):
        await self.fun_kick(ctx, user=user)

    @commands.command(
        name="punch",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Punch someone",
            "usage": "punch [user]",
            "desc": "Punches another user with a random anime GIF.",
            "args": [("user", "Who to punch (optional)")],
            "perms": "None",
            "example": "!punch @Snow",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_punch(self, ctx, user: Optional[discord.Member] = None):
        await self.fun_punch(ctx, user=user)

    @commands.command(
        name="yeet",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Yeet someone",
            "usage": "yeet [user]",
            "desc": "Yeets another user with a random anime GIF.",
            "args": [("user", "Who to yeet (optional)")],
            "perms": "None",
            "example": "!yeet @Snow",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_yeet(self, ctx, user: Optional[discord.Member] = None):
        await self.fun_yeet(ctx, user=user)

    @commands.command(
        name="feed",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Feed someone",
            "usage": "feed [user]",
            "desc": "Feeds another user with a random anime GIF.",
            "args": [("user", "Who to feed (optional)")],
            "perms": "None",
            "example": "!feed @Snow",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_feed(self, ctx, user: Optional[discord.Member] = None):
        await self.fun_feed(ctx, user=user)

    @commands.command(
        name="handhold",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Hold someone's hand",
            "usage": "handhold [user]",
            "desc": "Holds another user's hand with a random anime GIF.",
            "args": [("user", "Who to hold hands with (optional)")],
            "perms": "None",
            "example": "!handhold @Snow",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_handhold(self, ctx, user: Optional[discord.Member] = None):
        await self.fun_handhold(ctx, user=user)

    @commands.command(
        name="handshake",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Shake someone's hand",
            "usage": "handshake [user]",
            "desc": "Shakes another user's hand with a random anime GIF.",
            "args": [("user", "Who to shake hands with (optional)")],
            "perms": "None",
            "example": "!handshake @Snow",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_handshake(self, ctx, user: Optional[discord.Member] = None):
        await self.fun_handshake(ctx, user=user)

    @commands.command(
        name="peck",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Give a quick peck",
            "usage": "peck [user]",
            "desc": "Gives another user a quick peck with a random anime GIF.",
            "args": [("user", "Who to peck (optional)")],
            "perms": "None",
            "example": "!peck @Snow",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_peck(self, ctx, user: Optional[discord.Member] = None):
        await self.fun_peck(ctx, user=user)

    @commands.command(
        name="nom",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Nom on someone",
            "usage": "nom [user]",
            "desc": "Noms on another user with a random anime GIF.",
            "args": [("user", "Who to nom on (optional)")],
            "perms": "None",
            "example": "!nom @Snow",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_nom(self, ctx, user: Optional[discord.Member] = None):
        await self.fun_nom(ctx, user=user)

    @commands.command(
        name="shoot",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Finger-gun someone",
            "usage": "shoot [user]",
            "desc": "Shoots another user with finger guns and a random anime GIF.",
            "args": [("user", "Who to shoot (optional)")],
            "perms": "None",
            "example": "!shoot @Snow",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_shoot(self, ctx, user: Optional[discord.Member] = None):
        await self.fun_shoot(ctx, user=user)

    @commands.command(
        name="stare",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Stare at someone",
            "usage": "stare [user]",
            "desc": "Stares at another user with a random anime GIF.",
            "args": [("user", "Who to stare at (optional)")],
            "perms": "None",
            "example": "!stare @Snow",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_stare(self, ctx, user: Optional[discord.Member] = None):
        await self.fun_stare(ctx, user=user)

    @commands.command(
        name="cry",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Cry",
            "usage": "cry",
            "desc": "Express your sadness with a random anime GIF.",
            "args": [],
            "perms": "None",
            "example": "!cry",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_cry(self, ctx):
        await self.fun_cry(ctx)

    @commands.command(
        name="dance",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Dance",
            "usage": "dance",
            "desc": "Show off your dance moves with a random anime GIF.",
            "args": [],
            "perms": "None",
            "example": "!dance",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_dance(self, ctx):
        await self.fun_dance(ctx)

    @commands.command(
        name="blush",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Blush",
            "usage": "blush",
            "desc": "Blush with a random anime GIF.",
            "args": [],
            "perms": "None",
            "example": "!blush",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_blush(self, ctx):
        await self.fun_blush(ctx)

    @commands.command(
        name="smile",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Smile",
            "usage": "smile",
            "desc": "Show a smile with a random anime GIF.",
            "args": [],
            "perms": "None",
            "example": "!smile",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_smile(self, ctx):
        await self.fun_smile(ctx)

    @commands.command(
        name="laugh",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Laugh",
            "usage": "laugh",
            "desc": "Laugh with a random anime GIF.",
            "args": [],
            "perms": "None",
            "example": "!laugh",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_laugh(self, ctx):
        await self.fun_laugh(ctx)

    @commands.command(
        name="smug",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Look smug",
            "usage": "smug",
            "desc": "Look smug with a random anime GIF.",
            "args": [],
            "perms": "None",
            "example": "!smug",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_smug(self, ctx):
        await self.fun_smug(ctx)

    @commands.command(
        name="think",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Think",
            "usage": "think",
            "desc": "Think with a random anime GIF.",
            "args": [],
            "perms": "None",
            "example": "!think",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_think(self, ctx):
        await self.fun_think(ctx)

    @commands.command(
        name="shrug",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Shrug",
            "usage": "shrug",
            "desc": "Shrug with a random anime GIF.",
            "args": [],
            "perms": "None",
            "example": "!shrug",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_shrug(self, ctx):
        await self.fun_shrug(ctx)

    @commands.command(
        name="pout",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Pout",
            "usage": "pout",
            "desc": "Pout with a random anime GIF.",
            "args": [],
            "perms": "None",
            "example": "!pout",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_pout(self, ctx):
        await self.fun_pout(ctx)

    @commands.command(
        name="facepalm",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Facepalm",
            "usage": "facepalm",
            "desc": "Facepalm with a random anime GIF.",
            "args": [],
            "perms": "None",
            "example": "!facepalm",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_facepalm(self, ctx):
        await self.fun_facepalm(ctx)

    @commands.command(
        name="happy",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Be happy",
            "usage": "happy",
            "desc": "Express happiness with a random anime GIF.",
            "args": [],
            "perms": "None",
            "example": "!happy",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_happy(self, ctx):
        await self.fun_happy(ctx)

    @commands.command(
        name="bored",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Be bored",
            "usage": "bored",
            "desc": "Express boredom with a random anime GIF.",
            "args": [],
            "perms": "None",
            "example": "!bored",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_bored(self, ctx):
        await self.fun_bored(ctx)

    @commands.command(
        name="sleep",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Sleep",
            "usage": "sleep",
            "desc": "Fall asleep with a random anime GIF.",
            "args": [],
            "perms": "None",
            "example": "!sleep",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_sleep(self, ctx):
        await self.fun_sleep(ctx)

    @commands.command(
        name="thumbsup",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Thumbs up",
            "usage": "thumbsup",
            "desc": "Give a thumbs up with a random anime GIF.",
            "args": [],
            "perms": "None",
            "example": "!thumbsup",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_thumbsup(self, ctx):
        await self.fun_thumbsup(ctx)

    @commands.command(
        name="nod",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Nod",
            "usage": "nod",
            "desc": "Nod with a random anime GIF.",
            "args": [],
            "perms": "None",
            "example": "!nod",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_nod(self, ctx):
        await self.fun_nod(ctx)

    @commands.command(
        name="nope",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Nope",
            "usage": "nope",
            "desc": "Nope out with a random anime GIF.",
            "args": [],
            "perms": "None",
            "example": "!nope",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_nope(self, ctx):
        await self.fun_nope(ctx)

    @commands.command(
        name="wink",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Wink",
            "usage": "wink",
            "desc": "Wink with a random anime GIF.",
            "args": [],
            "perms": "None",
            "example": "!wink",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_wink(self, ctx):
        await self.fun_wink(ctx)

    @commands.command(
        name="yawn",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Yawn",
            "usage": "yawn",
            "desc": "Yawn with a random anime GIF.",
            "args": [],
            "perms": "None",
            "example": "!yawn",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_yawn(self, ctx):
        await self.fun_yawn(ctx)

    @commands.command(
        name="lurk",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Lurk",
            "usage": "lurk",
            "desc": "Lurk with a random anime GIF.",
            "args": [],
            "perms": "None",
            "example": "!lurk",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_lurk(self, ctx):
        await self.fun_lurk(ctx)

    @commands.command(
        name="baka",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Baka!",
            "usage": "baka",
            "desc": "Call everyone a baka with a random anime GIF.",
            "args": [],
            "perms": "None",
            "example": "!baka",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_baka(self, ctx):
        await self.fun_baka(ctx)

    @commands.command(
        name="angry",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Be angry",
            "usage": "angry",
            "desc": "Express anger with a random anime GIF.",
            "args": [],
            "perms": "None",
            "example": "!angry",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_angry(self, ctx):
        await self.fun_angry(ctx)

    @commands.command(
        name="run",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Run away",
            "usage": "run",
            "desc": "Run away with a random anime GIF.",
            "args": [],
            "perms": "None",
            "example": "!run",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_run(self, ctx):
        await self.fun_run(ctx)

    @commands.command(
        name="ship",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Ship two users",
            "usage": "ship <user1> <user2>",
            "desc": "Smashes two users' names together into a ship name and gives a compatibility score (0-100%).",
            "args": [("user1", "First user"), ("user2", "Second user")],
            "perms": "None",
            "example": "!ship @Snow @Nano",
        },
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def pfx_ship(self, ctx, user1: discord.Member, user2: discord.Member):
        await self.fun_ship(ctx, user1=user1, user2=user2)

    @commands.command(
        name="8ball",
        aliases=["eightball", "magic8ball"],
        extras={
            "category": "\U0001f389 Fun",
            "short": "Ask the magic 8-ball",
            "usage": "8ball <question>",
            "desc": "Ask a yes/no question and the magic 8-ball will answer.",
            "args": [("question", "Your yes/no question")],
            "perms": "None",
            "example": "!8ball Will I pass my exam?",
        },
    )
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pfx_8ball(self, ctx, *, question: str):
        await self.fun_8ball(ctx, question=question)


async def setup(bot: commands.Bot):
    await bot.add_cog(Fun(bot))
