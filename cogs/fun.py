"""
cogs/fun.py
Fun commands -- social interactions, solo reactions, ship, 8-ball, fml.

GIFs sourced from nekos.best (no API key required).
FML stories scraped from fmylife.com (no API key required).
Falls back gracefully (text-only) if the API is unavailable.

Slash (1 top-level slot, 5 subcommands):
  /fun social <action> [user]   -- autocomplete picker, 26 social actions
  /fun react <action>           -- autocomplete picker, 33 solo reactions
  /fun ship <user1> <user2>
  /fun 8ball <question>
  /fun fml

Prefix (flat):
  !hug, !slap, !cry, !dance, !ship, !8ball, !fml, etc.
"""

import asyncio
import hashlib
import logging
import random
import re
from html import unescape
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from utils import helpers as h

log = logging.getLogger("NanoBot.fun")

_NEKOS_BASE = "https://nekos.best/api/v2"
_PINK = 0xFF6EB4
_FML_URL = "https://www.fmylife.com/random"
_FML_BLUE = 0x00B2FF


# ══════════════════════════════════════════════════════════════════════════════
#  Action data -- single source of truth for slash AND prefix commands.
#  Adding a command = adding one dict entry + one factory line in the class.
# ══════════════════════════════════════════════════════════════════════════════

# -- Social: target another user ----------------------------------------------
_SOCIAL_ACTIONS: dict[str, dict] = {
    "bite": {
        "endpoint": "bite",
        "label": "bite",
        "bot_msg": "Hope you like the taste of silicon. \U0001f9db",
        "self_msg": "Biting yourself? Ouch! \U0001f9db",
        "action_msg": "{author} bites {target}! \U0001f9db",
        "color": h.RED,
        "desc": "Bite someone!",
        "short": "Bite someone",
    },
    "blowkiss": {
        "endpoint": "blowkiss",
        "label": "blow kiss",
        "bot_msg": "A blown kiss for a bot? Caught it! \U0001f618",
        "self_msg": "Blowing yourself a kiss? Self-love! \U0001f618",
        "action_msg": "{author} blows a kiss at {target}! \U0001f618",
        "color": _PINK,
        "desc": "Blow someone a kiss!",
        "short": "Blow someone a kiss",
    },
    "bonk": {
        "endpoint": "bonk",
        "label": "bonk",
        "bot_msg": "You can't bonk the bot! \U0001f916",
        "self_msg": "Bonking yourself? Straight to horny jail. \U0001f528",
        "action_msg": "{author} bonks {target}! \U0001f528",
        "color": h.YELLOW,
        "desc": "Bonk someone!",
        "short": "Bonk someone",
    },
    "boop": {
        "endpoint": "pat",
        "label": "boop",
        "bot_msg": "Boop accepted. Boop logged. \U0001f916",
        "self_msg": "Booping your own snoot? Certified legend. \U0001f446",
        "action_msg": "{author} boops {target}'s snoot! \U0001f446",
        "color": 0xFFC0CB,
        "desc": "Boop someone's snoot!",
        "short": "Boop the snoot",
    },
    "cheekskiss": {
        "endpoint": "kiss",
        "label": "cheek kiss",
        "bot_msg": "A cheek kiss for a bot? How adorable! \U0001f60a",
        "self_msg": "Mwah! Loving yourself is important. \U0001f618",
        "action_msg": "{author} gives {target} a little cheek kiss! \U0001f618",
        "color": _PINK,
        "desc": "Give someone a sweet cheek kiss!",
        "short": "Give a cheek kiss",
    },
    "cuddle": {
        "endpoint": "cuddle",
        "label": "cuddle",
        "bot_msg": "Cuddling a bot? I'm flattered. \U0001f97a",
        "self_msg": "Self-cuddle activated. \U0001f97a",
        "action_msg": "{author} cuddles {target}! \U0001f97a",
        "color": _PINK,
        "desc": "Cuddle someone!",
        "short": "Cuddle someone",
    },
    "feed": {
        "endpoint": "feed",
        "label": "feed",
        "bot_msg": "I run on electricity, but thanks! \u26a1",
        "self_msg": "Feeding yourself? Self-care. \U0001f35c",
        "action_msg": "{author} feeds {target}! \U0001f35c",
        "color": _PINK,
        "desc": "Feed someone!",
        "short": "Feed someone",
    },
    "handhold": {
        "endpoint": "handhold",
        "label": "hand hold",
        "bot_msg": "H-holding hands?! How lewd! \U0001f633",
        "self_msg": "That's called clasping. \U0001f91d",
        "action_msg": "{author} holds {target}'s hand! \U0001f91d",
        "color": _PINK,
        "desc": "Hold someone's hand!",
        "short": "Hold someone's hand",
    },
    "handshake": {
        "endpoint": "handshake",
        "label": "handshake",
        "bot_msg": "*firm handshake* Pleasure doing business. \U0001f916",
        "self_msg": "Deal with yourself sealed. \U0001f91d",
        "action_msg": "{author} shakes {target}'s hand! \U0001f91d",
        "color": h.BLUE,
        "desc": "Shake someone's hand!",
        "short": "Shake someone's hand",
    },
    "highfive": {
        "endpoint": "highfive",
        "label": "high five",
        "bot_msg": "\u270b *high fives back*",
        "self_msg": "A self-high-five? Respect. \U0001f64c",
        "action_msg": "{author} high fives {target}! \U0001f64c",
        "color": h.GREEN,
        "desc": "High five someone!",
        "short": "High five someone",
    },
    "hug": {
        "endpoint": "hug",
        "label": "hug",
        "bot_msg": "I'm just a bot, but I'd never turn down a hug! \U0001f917",
        "self_msg": "Awh, no one to hug? Don't worry, I've got you. \U0001f917",
        "action_msg": "{author} hugs {target}! \U0001f917",
        "color": _PINK,
        "desc": "Give someone a warm hug.",
        "short": "Give someone a hug",
    },
    "kick": {
        "endpoint": "kick",
        "label": "kick",
        "bot_msg": "You can't kick me! I live in the cloud! \u2601\ufe0f",
        "self_msg": "Kicking yourself? Bold. \U0001f9b5",
        "action_msg": "{author} kicks {target}! \U0001f9b5",
        "color": h.YELLOW,
        "desc": "Kick someone (for fun)!",
        "short": "Fun-kick someone",
    },
    "kiss": {
        "endpoint": "kiss",
        "label": "kiss",
        "bot_msg": "I appreciate the affection, but I'm made of code! \U0001f4be\U0001f48b",
        "self_msg": "Kissing yourself? Absolute power move. \U0001f48b",
        "action_msg": "{author} kisses {target}! \U0001f48b",
        "color": _PINK,
        "desc": "Kiss someone!",
        "short": "Kiss someone",
    },
    "lappillow": {
        "endpoint": "lappillow",
        "label": "lap pillow",
        "bot_msg": "A bot lap pillow? I'll allow it. \U0001f60c",
        "self_msg": "Lap pillow for one? Cozy. \U0001f60c",
        "action_msg": "{author} offers {target} a lap pillow! \U0001f60c",
        "color": _PINK,
        "desc": "Offer someone a lap pillow!",
        "short": "Offer a lap pillow",
    },
    "nom": {
        "endpoint": "nom",
        "label": "nom",
        "bot_msg": "I taste like 1s and 0s. \U0001f916",
        "self_msg": "Snack attack! \U0001f60b",
        "action_msg": "{author} noms on {target}! \U0001f60b",
        "color": _PINK,
        "desc": "Nom on someone!",
        "short": "Nom on someone",
    },
    "pat": {
        "endpoint": "pat",
        "label": "pat",
        "bot_msg": "*enjoys the headpats* \u2728 Thank you!",
        "self_msg": "Pat yourself on the back -- you deserve it! \U0001f972",
        "action_msg": "{author} gives {target} a comforting pat! \U0001f970",
        "color": 0xFFC0CB,
        "desc": "Give someone a comforting head pat.",
        "short": "Head pat someone",
    },
    "peck": {
        "endpoint": "peck",
        "label": "peck",
        "bot_msg": "A peck for a bot? Sweet! \U0001f60a",
        "self_msg": "Pecking yourself? Cute. \U0001f617",
        "action_msg": "{author} gives {target} a quick peck! \U0001f617",
        "color": _PINK,
        "desc": "Give someone a quick peck!",
        "short": "Give a quick peck",
    },
    "poke": {
        "endpoint": "poke",
        "label": "poke",
        "bot_msg": "Hey! No poking the bot! \U0001f916\U0001f448",
        "self_msg": "...why are you poking yourself? \U0001f448",
        "action_msg": "{author} pokes {target}! \U0001f449",
        "color": h.YELLOW,
        "desc": "Poke someone!",
        "short": "Poke someone",
    },
    "punch": {
        "endpoint": "punch",
        "label": "punch",
        "bot_msg": "*dodges* Try harder! \U0001f916",
        "self_msg": "Punching yourself? Respect. \U0001f91c",
        "action_msg": "{author} punches {target}! \U0001f91c",
        "color": h.RED,
        "desc": "Punch someone!",
        "short": "Punch someone",
    },
    "shake": {
        "endpoint": "shake",
        "label": "shake",
        "bot_msg": "Stop shaking me! My circuits are rattling! \U0001fae8",
        "self_msg": "Shaking yourself? Everything okay? \U0001fae8",
        "action_msg": "{author} shakes {target}! \U0001fae8",
        "color": h.YELLOW,
        "desc": "Shake someone!",
        "short": "Shake someone",
    },
    "shoot": {
        "endpoint": "shoot",
        "label": "shoot",
        "bot_msg": "*deflects* No u. \U0001f916",
        "self_msg": "Pew pew at yourself! \U0001f449",
        "action_msg": "{author} shoots {target}! Pew pew! \U0001f449",
        "color": h.RED,
        "desc": "Finger guns!",
        "short": "Finger-gun someone",
    },
    "slap": {
        "endpoint": "slap",
        "label": "slap",
        "bot_msg": "You can't slap me, I'm intangible! \U0001f916",
        "self_msg": "Slapping yourself? That's rough. \U0001f612",
        "action_msg": "{author} slaps {target}! \U0001f44f",
        "color": h.RED,
        "desc": "Slap someone!",
        "short": "Slap someone",
    },
    "stare": {
        "endpoint": "stare",
        "label": "stare",
        "bot_msg": "*stares back in binary* \U0001f440",
        "self_msg": "Introspection is healthy. \U0001f440",
        "action_msg": "{author} stares at {target}! \U0001f440",
        "color": h.BLUE,
        "desc": "Stare at someone!",
        "short": "Stare at someone",
    },
    "tickle": {
        "endpoint": "tickle",
        "label": "tickle",
        "bot_msg": "I'm not ticklish! ...or am I? \U0001f914",
        "self_msg": "Tickling yourself doesn't work. \U0001f923",
        "action_msg": "{author} tickles {target}! \U0001f923",
        "color": h.YELLOW,
        "desc": "Tickle someone!",
        "short": "Tickle someone",
    },
    "wave": {
        "endpoint": "wave",
        "label": "wave",
        "bot_msg": "\U0001f44b Hello there!",
        "self_msg": "Waving at yourself? I wave back! \U0001f44b",
        "action_msg": "{author} waves at {target}! \U0001f44b",
        "color": h.BLUE,
        "desc": "Wave at someone!",
        "short": "Wave at someone",
    },
    "yeet": {
        "endpoint": "yeet",
        "label": "yeet",
        "bot_msg": "You can't yeet the un-yeetable! \U0001f916",
        "self_msg": "Yeeting yourself? Godspeed. \U0001f680",
        "action_msg": "{author} yeets {target} into orbit! \U0001f680",
        "color": h.YELLOW,
        "desc": "Yeet someone into orbit!",
        "short": "Yeet someone",
    },
}

# -- Solo reactions: express yourself -----------------------------------------
_REACT_ACTIONS: dict[str, dict] = {
    "angry": {
        "endpoint": "angry",
        "label": "angry",
        "msg": "{author} is angry! \U0001f620",
        "color": h.RED,
        "desc": "Be angry!",
        "short": "Be angry",
    },
    "baka": {
        "endpoint": "baka",
        "label": "baka",
        "msg": "{author} yells BAKA! \U0001f621",
        "color": h.RED,
        "desc": "Yell BAKA!",
        "short": "Baka!",
    },
    "bleh": {
        "endpoint": "bleh",
        "label": "bleh",
        "msg": "{author} sticks their tongue out! \U0001f61d",
        "color": h.YELLOW,
        "desc": "Stick your tongue out!",
        "short": "Stick your tongue out",
    },
    "blush": {
        "endpoint": "blush",
        "label": "blush",
        "msg": "{author} is blushing! \U0001f633",
        "color": _PINK,
        "desc": "Blush!",
        "short": "Blush",
    },
    "bored": {
        "endpoint": "bored",
        "label": "bored",
        "msg": "{author} is bored... \U0001f971",
        "color": _PINK,
        "desc": "Be bored.",
        "short": "Be bored",
    },
    "clap": {
        "endpoint": "clap",
        "label": "clap",
        "msg": "{author} is clapping! \U0001f44f",
        "color": h.GREEN,
        "desc": "Clap!",
        "short": "Clap",
    },
    "confused": {
        "endpoint": "confused",
        "label": "confused",
        "msg": "{author} is confused... \U0001f615",
        "color": h.YELLOW,
        "desc": "Be confused.",
        "short": "Be confused",
    },
    "cry": {
        "endpoint": "cry",
        "label": "cry",
        "msg": "{author} is crying... \U0001f622",
        "color": _PINK,
        "desc": "Express your sadness.",
        "short": "Cry",
    },
    "dance": {
        "endpoint": "dance",
        "label": "dance",
        "msg": "{author} is dancing! \U0001f57a",
        "color": h.GREEN,
        "desc": "Show off your moves!",
        "short": "Dance",
    },
    "facepalm": {
        "endpoint": "facepalm",
        "label": "facepalm",
        "msg": "{author} facepalms. \U0001f926",
        "color": h.YELLOW,
        "desc": "Facepalm.",
        "short": "Facepalm",
    },
    "happy": {
        "endpoint": "happy",
        "label": "happy",
        "msg": "{author} is happy! \U0001f60a",
        "color": h.GREEN,
        "desc": "Be happy!",
        "short": "Be happy",
    },
    "laugh": {
        "endpoint": "laugh",
        "label": "laugh",
        "msg": "{author} is laughing! \U0001f602",
        "color": h.YELLOW,
        "desc": "Laugh out loud!",
        "short": "Laugh",
    },
    "lurk": {
        "endpoint": "lurk",
        "label": "lurk",
        "msg": "{author} is lurking... \U0001f440",
        "color": h.BLUE,
        "desc": "Lurk in the shadows.",
        "short": "Lurk",
    },
    "nod": {
        "endpoint": "nod",
        "label": "nod",
        "msg": "{author} nods. \U0001f642",
        "color": _PINK,
        "desc": "Nod.",
        "short": "Nod",
    },
    "nope": {
        "endpoint": "nope",
        "label": "nope",
        "msg": "{author} says NOPE. \U0001f645",
        "color": h.RED,
        "desc": "Nope!",
        "short": "Nope",
    },
    "nya": {
        "endpoint": "nya",
        "label": "nya",
        "msg": "{author} goes nya~! \U0001f431",
        "color": _PINK,
        "desc": "Go nya~!",
        "short": "Nya~!",
    },
    "pout": {
        "endpoint": "pout",
        "label": "pout",
        "msg": "{author} is pouting! \U0001f61e",
        "color": _PINK,
        "desc": "Pout!",
        "short": "Pout",
    },
    "run": {
        "endpoint": "run",
        "label": "run",
        "msg": "{author} is running away! \U0001f3c3",
        "color": h.YELLOW,
        "desc": "Run away!",
        "short": "Run away",
    },
    "salute": {
        "endpoint": "salute",
        "label": "salute",
        "msg": "{author} salutes! \U0001fae1",
        "color": h.BLUE,
        "desc": "Salute!",
        "short": "Salute",
    },
    "shocked": {
        "endpoint": "shocked",
        "label": "shocked",
        "msg": "{author} is shocked! \U0001f631",
        "color": h.YELLOW,
        "desc": "Be shocked!",
        "short": "Be shocked",
    },
    "shrug": {
        "endpoint": "shrug",
        "label": "shrug",
        "msg": "{author} shrugs. \U0001f937",
        "color": _PINK,
        "desc": "Shrug it off.",
        "short": "Shrug",
    },
    "sip": {
        "endpoint": "sip",
        "label": "sip",
        "msg": "{author} takes a sip... \u2615",
        "color": h.BLUE,
        "desc": "Take a sip.",
        "short": "Take a sip",
    },
    "sleep": {
        "endpoint": "sleep",
        "label": "sleep",
        "msg": "{author} is sleeping... zzZ \U0001f634",
        "color": h.BLUE,
        "desc": "Sleepy time.",
        "short": "Sleep",
    },
    "smile": {
        "endpoint": "smile",
        "label": "smile",
        "msg": "{author} smiles! \U0001f60a",
        "color": h.GREEN,
        "desc": "Smile!",
        "short": "Smile",
    },
    "smug": {
        "endpoint": "smug",
        "label": "smug",
        "msg": "{author} looks smug. \U0001f60f",
        "color": _PINK,
        "desc": "Look smug.",
        "short": "Look smug",
    },
    "spin": {
        "endpoint": "spin",
        "label": "spin",
        "msg": "{author} is spinning! \U0001f300",
        "color": h.GREEN,
        "desc": "Spin!",
        "short": "Spin",
    },
    "tableflip": {
        "endpoint": "tableflip",
        "label": "table flip",
        "msg": "{author} flips the table! (\u256f\u00b0\u25a1\u00b0)\u256f\ufe35 \u253b\u2501\u253b",
        "color": h.RED,
        "desc": "Flip the table!",
        "short": "Flip the table",
    },
    "teehee": {
        "endpoint": "teehee",
        "label": "teehee",
        "msg": "{author} giggles... teehee! \U0001f92d",
        "color": _PINK,
        "desc": "Giggle!",
        "short": "Teehee!",
    },
    "think": {
        "endpoint": "think",
        "label": "think",
        "msg": "{author} is thinking... \U0001f914",
        "color": h.BLUE,
        "desc": "Think hard.",
        "short": "Think",
    },
    "thumbsup": {
        "endpoint": "thumbsup",
        "label": "thumbs up",
        "msg": "{author} gives a thumbs up! \U0001f44d",
        "color": h.GREEN,
        "desc": "Give a thumbs up!",
        "short": "Thumbs up",
    },
    "wag": {
        "endpoint": "wag",
        "label": "wag",
        "msg": "{author} wags their tail! \U0001f415",
        "color": h.GREEN,
        "desc": "Wag your tail!",
        "short": "Wag your tail",
    },
    "wink": {
        "endpoint": "wink",
        "label": "wink",
        "msg": "{author} winks! \U0001f609",
        "color": _PINK,
        "desc": "Wink!",
        "short": "Wink",
    },
    "yawn": {
        "endpoint": "yawn",
        "label": "yawn",
        "msg": "{author} yawns... \U0001f971",
        "color": _PINK,
        "desc": "Yawn.",
        "short": "Yawn",
    },
}


# ── 8-ball pools ──────────────────────────────────────────────────────────────
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


# ── Ship helpers ──────────────────────────────────────────────────────────────
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


# ── GIF fetcher ───────────────────────────────────────────────────────────────
async def _fetch_gif(session: aiohttp.ClientSession, endpoint: str) -> str | None:
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
        log.debug(f"GIF fetch failed for '{endpoint}': {exc}")
    return None


# ── FML story scraper ────────────────────────────────────────────────────
_FML_RE = re.compile(r"Today,\s.+?FML", re.DOTALL)
_FML_TAG_RE = re.compile(r"<[^>]+>")


async def _fetch_fml_stories(
    session: aiohttp.ClientSession,
) -> list[str]:
    """Scrape fmylife.com/random and return a list of story strings."""
    try:
        async with session.get(
            _FML_URL,
            headers={"User-Agent": "Mozilla/5.0 (compatible; NanoBot)"},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as resp:
            if resp.status != 200:
                return []
            html = await resp.text()
    except Exception as exc:
        log.debug(f"FML fetch failed: {exc}")
        return []

    raw_matches = _FML_RE.findall(html)
    stories: list[str] = []
    seen: set[str] = set()
    for raw in raw_matches:
        text = _FML_TAG_RE.sub("", raw)
        text = unescape(text)
        text = re.sub(r"\s+", " ", text).strip()
        # Skip duplicates and fragments that leaked into share widgets
        if len(text) > 40 and text not in seen:
            seen.add(text)
            stories.append(text)
    return stories


# ══════════════════════════════════════════════════════════════════════════════
class Fun(commands.Cog):
    """Fun social interaction and reaction commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None
        self._fml_buffer: list[str] = []
        self._fml_lock = asyncio.Lock()

    async def cog_load(self):
        self._session = aiohttp.ClientSession()
        self._dynamic_cmds: list[commands.Command] = []
        self._register_prefix_commands()

    async def cog_unload(self):
        for cmd in self._dynamic_cmds:
            self.bot.remove_command(cmd.name)
        if self._session and not self._session.closed:
            await self._session.close()

    # ── FML helper ────────────────────────────────────────────────────────────

    async def _get_fml(self) -> str | None:
        """Pop a story from the buffer, refilling from the site if needed."""
        async with self._fml_lock:
            if not self._fml_buffer and self._session:
                self._fml_buffer = await _fetch_fml_stories(self._session)
                random.shuffle(self._fml_buffer)
            return self._fml_buffer.pop() if self._fml_buffer else None

    # ── Shared embed builders ─────────────────────────────────────────────────

    async def _action_embed(self, guild_me, author, target, data, *, color=None):
        """Build an embed for a social action using a _SOCIAL_ACTIONS entry."""
        c = color or data.get("color", _PINK)
        if target is None or target == author:
            desc = data["self_msg"]
        elif target == guild_me:
            desc = data["bot_msg"]
        else:
            desc = (
                data["action_msg"]
                .replace("{author}", f"**{author.display_name}**")
                .replace("{target}", target.mention)
            )
        e = discord.Embed(description=desc, color=c)
        if self._session:
            gif = await _fetch_gif(self._session, data["endpoint"])
            if gif:
                e.set_image(url=gif)
        e.set_footer(text="NanoBot Fun")
        return e

    async def _react_embed(self, author, data, *, color=None):
        """Build an embed for a solo reaction using a _REACT_ACTIONS entry."""
        c = color or data.get("color", _PINK)
        e = discord.Embed(
            description=data["msg"].replace("{author}", f"**{author.display_name}**"),
            color=c,
        )
        if self._session:
            gif = await _fetch_gif(self._session, data["endpoint"])
            if gif:
                e.set_image(url=gif)
        e.set_footer(text="NanoBot Fun")
        return e

    # ══════════════════════════════════════════════════════════════════════════
    #  SLASH: /fun group  (4 subcommands, 1 top-level slot)
    # ══════════════════════════════════════════════════════════════════════════

    fun_group = app_commands.Group(
        name="fun",
        description="Fun commands -- social interactions, reactions, ship, 8-ball, fml!",
        guild_only=True,
    )

    # ── /fun social ───────────────────────────────────────────────────────────

    @fun_group.command(
        name="social",
        description="Social interactions -- hug, kiss, slap, and more!",
    )
    @app_commands.describe(action="What to do", user="Who to target")
    async def s_social(
        self,
        i: discord.Interaction,
        action: str,
        user: Optional[discord.Member] = None,
    ):
        data = _SOCIAL_ACTIONS.get(action.lower())
        if not data:
            return await i.response.send_message(
                "Unknown action. Pick one from the list!", ephemeral=True
            )
        e = await self._action_embed(i.guild.me, i.user, user, data)
        await i.response.send_message(embed=e)

    @s_social.autocomplete("action")
    async def _social_ac(self, i: discord.Interaction, current: str):
        q = current.lower()
        return [
            app_commands.Choice(name=v["label"], value=k)
            for k, v in _SOCIAL_ACTIONS.items()
            if q in k or q in v["label"]
        ][:25]

    # ── /fun react ────────────────────────────────────────────────────────────

    @fun_group.command(
        name="react",
        description="Express yourself -- cry, dance, laugh, and more!",
    )
    @app_commands.describe(action="How to react")
    async def s_react(self, i: discord.Interaction, action: str):
        data = _REACT_ACTIONS.get(action.lower())
        if not data:
            return await i.response.send_message(
                "Unknown reaction. Pick one from the list!", ephemeral=True
            )
        e = await self._react_embed(i.user, data)
        await i.response.send_message(embed=e)

    @s_react.autocomplete("action")
    async def _react_ac(self, i: discord.Interaction, current: str):
        q = current.lower()
        return [
            app_commands.Choice(name=v["label"], value=k)
            for k, v in _REACT_ACTIONS.items()
            if q in k or q in v["label"]
        ][:25]

    # ── /fun ship ─────────────────────────────────────────────────────────────

    @fun_group.command(name="ship", description="Ship two users! \U0001f495")
    @app_commands.describe(user1="First user", user2="Second user")
    async def s_ship(
        self, i: discord.Interaction, user1: discord.Member, user2: discord.Member
    ):
        if user1 == user2:
            e = discord.Embed(
                title="\U0001f495 Ship",
                description=(
                    f"**{user1.display_name}** + **{user2.display_name}**\n\n"
                    "Loving yourself is valid, but this is next level. \U0001f4af"
                ),
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

    # ── /fun 8ball ────────────────────────────────────────────────────────────

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

    # ── /fun fml ──────────────────────────────────────────────────────────

    @fun_group.command(
        name="fml", description="Get a random FML story from fmylife.com"
    )
    async def s_fml(self, i: discord.Interaction):
        await i.response.defer()
        story = await self._get_fml()
        if not story:
            return await i.followup.send(
                "Couldn't fetch an FML story right now. Try again later!",
                ephemeral=True,
            )
        e = discord.Embed(description=story, color=_FML_BLUE)
        e.set_footer(text="NanoBot Fun \u00b7 fmylife.com")
        await i.followup.send(embed=e)

    # ══════════════════════════════════════════════════════════════════════════
    #  PREFIX: flat commands  (!hug, !cry, !ship, !8ball, etc.)
    #  Registered dynamically in cog_load so .cog binding is not needed.
    # ══════════════════════════════════════════════════════════════════════════

    def _register_prefix_commands(self):
        """Build and register all factory prefix commands on the bot."""
        cog = self  # captured by every closure below

        for action, data in _SOCIAL_ACTIONS.items():
            name = "funkick" if action == "kick" else action
            aliases = ["fk"] if action == "kick" else []
            extras = {
                "category": "\U0001f389 Fun",
                "short": data["short"],
                "usage": f"{name} [user]",
                "desc": data["short"] + " with a random anime GIF.",
                "args": [("user", "Who to target (optional)")],
                "perms": "None",
                "example": f"!{name} @Snow",
            }

            def _make_social(name, aliases, extras, data):
                @commands.command(name=name, aliases=aliases, extras=extras)
                @commands.cooldown(1, 3, commands.BucketType.user)
                async def social_cmd(_self, ctx, user: Optional[discord.Member] = None):
                    e = await cog._action_embed(ctx.guild.me, ctx.author, user, data)
                    await ctx.reply(embed=e)

                return social_cmd

            social_cmd = _make_social(name, aliases, extras, data)
            social_cmd.cog = cog
            self.bot.add_command(social_cmd)
            self._dynamic_cmds.append(social_cmd)

        for action, data in _REACT_ACTIONS.items():
            extras = {
                "category": "\U0001f604 React",
                "short": data["short"],
                "usage": action,
                "desc": data["short"] + " with a random anime GIF.",
                "args": [],
                "perms": "None",
                "example": f"!{action}",
            }

            def _make_react(action, extras, data):
                @commands.command(name=action, extras=extras)
                @commands.cooldown(1, 3, commands.BucketType.user)
                async def react_cmd(_self, ctx):
                    e = await cog._react_embed(ctx.author, data)
                    await ctx.reply(embed=e)

                return react_cmd

            react_cmd = _make_react(action, extras, data)
            react_cmd.cog = cog
            self.bot.add_command(react_cmd)
            self._dynamic_cmds.append(react_cmd)

    # ── ship & 8ball prefix ───────────────────────────────────────────────────

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
                description=(
                    f"**{user1.display_name}** + **{user2.display_name}**\n\n"
                    "Loving yourself is valid, but this is next level. \U0001f4af"
                ),
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

    @commands.command(
        name="fml",
        extras={
            "category": "\U0001f389 Fun",
            "short": "Random FML story",
            "usage": "fml",
            "desc": "Get a random FML story from fmylife.com.",
            "args": [],
            "perms": "None",
            "example": "!fml",
        },
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def pfx_fml(self, ctx):
        async with ctx.typing():
            story = await self._get_fml()
        if not story:
            return await ctx.reply(
                "Couldn't fetch an FML story right now. Try again later!"
            )
        e = discord.Embed(description=story, color=_FML_BLUE)
        e.set_footer(text="NanoBot Fun \u00b7 fmylife.com")
        await ctx.reply(embed=e)


async def setup(bot: commands.Bot):
    await bot.add_cog(Fun(bot))
