"""
cogs/fun.py
Fun commands -- social interactions, solo reactions, ship, 8-ball, fml,
thigh, would-you-rather.

GIFs sourced from nekos.best (no API key required).
Thigh images sourced from Nekosia API (no API key required).
WYR questions from three sources (scraped/generated daily):
  1. truthordarebot.xyz API (PG + PG13 ratings, separate pools)
  2. Kaggle dataset one-time seed (~2700 questions on first run)
  3. Groq LLM generation (~20 fresh questions per day, if API key set)
FML stories cached from fmylife.com (scraped daily).
All image/GIF URLs cached in cache_db and served from cache.
Falls back to live API if cache is empty for a given endpoint.

Slash (1 top-level slot, 7 subcommands):
  /fun social <action> [user]   -- autocomplete picker, 26 social actions
  /fun react <action>           -- autocomplete picker, 33 solo reactions
  /fun ship <user1> <user2>
  /fun 8ball <question>
  /fun fml
  /fun thigh
  /fun wyr [duration]

Prefix (flat):
  !hug, !slap, !cry, !dance, !ship, !8ball, !fml, !thigh, !wyr, etc.
"""

import asyncio
import contextlib
import csv
import hashlib
import io
import json
import logging
import random
import re
import time
from html import unescape
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import cache_db
from utils import helpers as h

log = logging.getLogger("NanoBot.fun")

_NEKOS_BASE = "https://nekos.best/api/v2"
_NEKOSIA_BASE = "https://api.nekosia.cat/api/v1/images"
_WYR_URL = "https://api.truthordarebot.xyz/api/wyr"
_THIGH_TAGS = (
    "thighs",
    "thigh-high-socks",
    "white-thigh-high-socks",
    "black-thigh-high-socks",
    "knee-high-socks",
)
_PINK = 0xFF6EB4
_FML_URL = "https://www.fmylife.com/random"
_FML_BLUE = 0x00B2FF

# ── Kaggle WYR dataset (one-time seed) ────────────────────────────────────────
_KAGGLE_WYR_URL = (
    "https://www.kaggle.com/api/v1/datasets/download/charlieray668/would-you-rather"
)

# ── Groq WYR generation ──────────────────────────────────────────────────────
_GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
_GROQ_MODEL = "llama-3.1-8b-instant"
_GROQ_WYR_COUNT = 20  # questions to generate per daily scrape
_GROQ_WYR_SYSTEM = (
    "You generate Would You Rather questions for a Discord bot. "
    "Return ONLY a JSON array of strings. Each string must start with "
    '"Would you rather" and contain exactly two options separated by " or ". '
    "End each with a question mark. Make them fun, creative, and varied -- "
    "mix silly, deep, gross, impossible, and everyday scenarios. "
    "No numbered lists, no markdown, no explanation. Just the JSON array."
)

# ── WYR API ratings to scrape (separate question pools) ──────────────────────
_WYR_RATINGS = ("pg", "pg13")

# ── Scraper settings ─────────────────────────────────────────────────────────
_FML_PAGES_PER_SCRAPE = 100  # ~5-10 stories each = 500-1000 per run
_WYR_REQUESTS_PER_SCRAPE = 100  # 1 question each, deduped
_NEKOS_PER_ENDPOINT = 40  # GIFs/images per nekos.best endpoint per run
_NEKOSIA_PER_TAG = 40  # images per Nekosia tag per run
_REVALIDATE_AGE = 7 * 86400  # check URLs older than 7 days
_REVALIDATE_BATCH = 400  # max URLs to check per revalidation cycle


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


# ══════════════════════════════════════════════════════════════════════════════
#  Live API fetchers -- used as fallback when cache is empty AND for scraping
# ══════════════════════════════════════════════════════════════════════════════


async def _fetch_nekos_single(
    session: aiohttp.ClientSession, endpoint: str
) -> dict | None:
    """Fetch one result from nekos.best. Returns full result dict or None."""
    try:
        async with session.get(
            f"{_NEKOS_BASE}/{endpoint}",
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                results = data.get("results", [])
                if results:
                    return results[0]
    except Exception as exc:
        log.debug(f"nekos.best fetch failed for '{endpoint}': {exc}")
    return None


async def _fetch_nekos_batch(
    session: aiohttp.ClientSession, endpoint: str, amount: int
) -> list[dict]:
    """Fetch up to `amount` results from nekos.best in one request (API supports amount param)."""
    # nekos.best supports ?amount=N (max 20 per request)
    results: list[dict] = []
    remaining = amount
    while remaining > 0:
        batch = min(remaining, 20)
        try:
            async with session.get(
                f"{_NEKOS_BASE}/{endpoint}",
                params={"amount": batch},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    batch_results = data.get("results", [])
                    results.extend(batch_results)
                    remaining -= len(batch_results)
                    if len(batch_results) < batch:
                        break  # API gave us less than requested, stop
                else:
                    break
        except Exception as exc:
            log.debug(f"nekos.best batch fetch failed for '{endpoint}': {exc}")
            break
        if remaining > 0:
            await asyncio.sleep(0.3)
    return results


async def _fetch_nekosia_single(
    session: aiohttp.ClientSession, category: str
) -> tuple[str | None, str | None]:
    """Fetch a random SFW image from Nekosia. Returns (image_url, source_url)."""
    try:
        async with session.get(
            f"{_NEKOSIA_BASE}/{category}",
            timeout=aiohttp.ClientTimeout(total=6),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("success"):
                    img = data.get("image", {}).get("compressed", {}).get(
                        "url"
                    ) or data.get("image", {}).get("original", {}).get("url")
                    src = data.get("source", {}).get("url")
                    return img, src
    except Exception as exc:
        log.debug(f"Nekosia fetch failed for '{category}': {exc}")
    return None, None


# ── FML story scraper (bulk, for daily cache refresh) ─────────────────────────
_FML_RE = re.compile(r"(?:Today|I)\s.+?FML")
_FML_TAG_RE = re.compile(r"<[^>]+>")


async def _scrape_fml_page(session: aiohttp.ClientSession) -> list[str]:
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
        log.debug(f"FML scrape failed: {exc}")
        return []

    clean = _FML_TAG_RE.sub(" ", html)
    clean = unescape(clean)

    raw_matches = _FML_RE.findall(clean)
    stories: list[str] = []
    seen: set[str] = set()
    for raw in raw_matches:
        text = re.sub(r"\s+", " ", raw).strip()
        if len(text) > 40 and text not in seen:
            seen.add(text)
            stories.append(text)
    return stories


async def _scrape_fml_bulk(
    session: aiohttp.ClientSession, pages: int = _FML_PAGES_PER_SCRAPE
) -> list[str]:
    """Hit fmylife.com/random multiple times and return all unique stories."""
    all_stories: list[str] = []
    seen: set[str] = set()
    for i in range(pages):
        page_stories = await _scrape_fml_page(session)
        for s in page_stories:
            if s not in seen:
                seen.add(s)
                all_stories.append(s)
        if i < pages - 1:
            await asyncio.sleep(1)
    return all_stories


# ── WYR question fetcher (bulk, for daily cache refresh) ──────────────────────
async def _fetch_wyr_single(
    session: aiohttp.ClientSession, rating: str = "pg13"
) -> str | None:
    """Fetch a single Would You Rather question from truthordarebot.xyz."""
    try:
        async with session.get(
            _WYR_URL,
            params={"rating": rating},
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("question")
    except Exception as exc:
        log.debug(f"WYR fetch failed: {exc}")
    return None


async def _scrape_wyr_bulk(
    session: aiohttp.ClientSession, count: int = _WYR_REQUESTS_PER_SCRAPE
) -> list[str]:
    """Fetch many WYR questions across all ratings, deduplicating as we go."""
    questions: list[str] = []
    seen: set[str] = set()
    for rating in _WYR_RATINGS:
        for i in range(count):
            q = await _fetch_wyr_single(session, rating=rating)
            if q and q not in seen:
                seen.add(q)
                questions.append(q)
            if i < count - 1:
                await asyncio.sleep(0.5)
    return questions


# ── Kaggle WYR dataset seed (one-time bulk import) ────────────────────────────
async def _seed_kaggle_wyr(session: aiohttp.ClientSession) -> list[str]:
    """Download the Kaggle WYR CSV and return formatted questions.

    The CSV has columns: option_a, votes_a, option_b, votes_b.
    We format each row as 'Would you rather X or Y?'.
    """
    try:
        async with session.get(
            _KAGGLE_WYR_URL,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                log.warning(f"Kaggle WYR download failed: HTTP {resp.status}")
                return []
            zip_bytes = await resp.read()
    except Exception as exc:
        log.warning(f"Kaggle WYR download error: {exc}")
        return []

    # The zip contains all_unique.csv
    import zipfile

    questions: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for name in zf.namelist():
                if name.endswith(".csv"):
                    with zf.open(name) as csvfile:
                        reader = csv.DictReader(io.TextIOWrapper(csvfile, "utf-8"))
                        for row in reader:
                            a = row.get("option_a", "").strip()
                            b = row.get("option_b", "").strip()
                            if a and b:
                                # Lowercase the first letter of each option
                                a_fmt = a[0].lower() + a[1:] if len(a) > 1 else a.lower()
                                b_fmt = b[0].lower() + b[1:] if len(b) > 1 else b.lower()
                                questions.append(
                                    f"Would you rather {a_fmt} or {b_fmt}?"
                                )
                    break
    except Exception as exc:
        log.warning(f"Kaggle WYR parse error: {exc}")
        return []

    return questions


# ── Groq WYR generation ──────────────────────────────────────────────────────
async def _generate_wyr_groq(
    session: aiohttp.ClientSession,
    api_key: str,
    count: int = _GROQ_WYR_COUNT,
) -> list[str]:
    """Use Groq LLM to generate fresh WYR questions. Returns list of strings."""
    try:
        payload = {
            "model": _GROQ_MODEL,
            "messages": [
                {"role": "system", "content": _GROQ_WYR_SYSTEM},
                {
                    "role": "user",
                    "content": f"Generate {count} unique Would You Rather questions.",
                },
            ],
            "temperature": 1.0,
            "max_tokens": 2048,
        }
        async with session.post(
            _GROQ_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                log.warning(f"Groq WYR generation failed: HTTP {resp.status} {body[:200]}")
                return []
            data = await resp.json()

        text = data["choices"][0]["message"]["content"].strip()
        # Strip markdown fences if present
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        raw_list = json.loads(text)
        if not isinstance(raw_list, list):
            log.warning("Groq WYR: response was not a JSON array")
            return []

        # Validate format
        questions: list[str] = []
        for item in raw_list:
            if (
                isinstance(item, str)
                and item.lower().startswith("would you rather")
                and " or " in item.lower()
            ):
                q = item.strip().rstrip("?") + "?"
                questions.append(q)
        return questions

    except json.JSONDecodeError as exc:
        log.warning(f"Groq WYR: failed to parse JSON: {exc}")
        return []
    except Exception as exc:
        log.warning(f"Groq WYR generation error: {exc}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
#  Cache-aware image getter -- used by commands
# ══════════════════════════════════════════════════════════════════════════════


async def _get_gif(session: aiohttp.ClientSession | None, endpoint: str) -> str | None:
    """Get a GIF URL for a nekos.best endpoint, cache-first with live fallback."""
    cached = await cache_db.get_random_image("nekos", endpoint)
    if cached:
        return cached["url"]

    # Cache miss -- fall back to live API
    if not session:
        return None
    result = await _fetch_nekos_single(session, endpoint)
    if result:
        # Store it for next time
        await cache_db.add_images("nekos", endpoint, [{"url": result["url"]}])
        return result["url"]
    return None


async def _get_nekosia(
    session: aiohttp.ClientSession | None, tag: str
) -> tuple[str | None, str | None]:
    """Get a Nekosia image, cache-first with live fallback."""
    cached = await cache_db.get_random_image("nekosia", tag)
    if cached:
        return cached["url"], cached.get("source_url")

    # Cache miss -- fall back to live API
    if not session:
        return None, None
    img, src = await _fetch_nekosia_single(session, tag)
    if img:
        await cache_db.add_images("nekosia", tag, [{"url": img, "source_url": src}])
    return img, src


async def _get_nekos_image(
    session: aiohttp.ClientSession | None, endpoint: str
) -> dict | None:
    """Get a nekos.best static image (for images cog), cache-first with live fallback.

    Returns dict with url, source_url, artist -- or None.
    """
    cached = await cache_db.get_random_image("nekos", endpoint)
    if cached:
        return cached

    # Cache miss -- fall back to live API
    if not session:
        return None
    result = await _fetch_nekos_single(session, endpoint)
    if result:
        img_data = {
            "url": result["url"],
            "source_url": result.get("source_url"),
            "artist": result.get("artist_name"),
        }
        await cache_db.add_images("nekos", endpoint, [img_data])
        return img_data
    return None


# ── Safe typing indicator ─────────────────────────────────────────────────────
@contextlib.asynccontextmanager
async def _safe_typing(ctx: commands.Context):
    """Wrapper around ctx.typing() that swallows Discord HTTP errors."""
    try:
        ctx_mgr = ctx.typing()
        await ctx_mgr.__aenter__()
    except discord.HTTPException:
        yield
        return
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            await ctx_mgr.__aexit__(None, None, None)


# ── WYR question splitter ─────────────────────────────────────────────────────
_WYR_SPLIT_RE = re.compile(
    r"^would you rather\s+(.+?)\s+or\s+(.+?)\??$",
    re.IGNORECASE,
)


def _split_wyr(question: str) -> tuple[str, str]:
    """Split 'Would you rather X or Y?' into (X, Y). Capitalizes each."""
    m = _WYR_SPLIT_RE.match(question.strip())
    if m:
        a = m.group(1).strip().capitalize()
        b = m.group(2).strip().capitalize()
        return a, b
    parts = question.split(" or ", 1)
    if len(parts) == 2:
        a = parts[0].replace("Would you rather ", "").strip().capitalize()
        b = parts[1].rstrip("?").strip().capitalize()
        return a, b
    return question, "???"


# ── Duration parser ───────────────────────────────────────────────────────────
_DURATION_RE = re.compile(
    r"(?:(\d+)\s*h(?:ours?|r)?)?[\s,]*(?:(\d+)\s*m(?:in(?:utes?)?)?)?",
    re.IGNORECASE,
)


def _parse_duration(text: str | None) -> int:
    if not text:
        return 3600
    text = text.strip()
    if text.isdigit():
        mins = int(text)
        return max(60, min(mins * 60, 86400))
    m = _DURATION_RE.match(text)
    if m and (m.group(1) or m.group(2)):
        hours = int(m.group(1) or 0)
        mins = int(m.group(2) or 0)
        total = hours * 3600 + mins * 60
        return max(60, min(total, 86400))
    return 3600


def _fmt_duration(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    parts = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    return " ".join(parts) or "1m"


# ── WYR persistent view ──────────────────────────────────────────────────────
class WyrView(discord.ui.View):
    """Two-button vote view for Would You Rather. Edits itself on expiry."""

    def __init__(self, option_a: str, option_b: str, duration: int = 3600):
        super().__init__(timeout=duration)
        self.option_a = option_a
        self.option_b = option_b
        self.votes: dict[int, str] = {}
        self.ended = False
        self.message: discord.Message | None = None
        self.end_ts = int(time.time() + duration)

    def _tally(self) -> tuple[int, int]:
        a = sum(1 for v in self.votes.values() if v == "A")
        b = sum(1 for v in self.votes.values() if v == "B")
        return a, b

    def _results_embed(self) -> discord.Embed:
        a, b = self._tally()
        total = a + b
        pct_a = round(a / total * 100) if total else 0
        pct_b = 100 - pct_a if total else 0
        bar_a = "\u2593" * round(pct_a / 10) + "\u2591" * (10 - round(pct_a / 10))
        bar_b = "\u2593" * round(pct_b / 10) + "\u2591" * (10 - round(pct_b / 10))
        e = discord.Embed(
            title="\U0001f914 Would You Rather -- Results!",
            color=0x5865F2,
        )
        e.add_field(
            name=f"\U0001f1e6 {self.option_a}",
            value=f"{bar_a} **{pct_a}%** ({a} vote{'s' if a != 1 else ''})",
            inline=False,
        )
        e.add_field(
            name=f"\U0001f1e7 {self.option_b}",
            value=f"{bar_b} **{pct_b}%** ({b} vote{'s' if b != 1 else ''})",
            inline=False,
        )
        e.set_footer(
            text=f"NanoBot Fun \u00b7 {total} total vote{'s' if total != 1 else ''}"
        )
        return e

    def _voting_embed(self) -> discord.Embed:
        total = len(self.votes)
        e = discord.Embed(
            title="\U0001f914 Would You Rather...",
            color=0x5865F2,
        )
        e.add_field(name="\U0001f1e6", value=self.option_a, inline=False)
        e.add_field(name="\U0001f1e7", value=self.option_b, inline=False)
        e.add_field(
            name="",
            value=f"\U0001f4ca {total} vote{'s' if total != 1 else ''} so far \u00b7 Results <t:{self.end_ts}:R>",
            inline=False,
        )
        e.set_footer(text="NanoBot Fun \u00b7 Tap a button to vote!")
        return e

    async def on_timeout(self):
        self.ended = True
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(embed=self._results_embed(), view=self)
            except discord.HTTPException:
                pass

    async def _handle_vote(self, interaction: discord.Interaction, choice: str):
        if self.ended:
            return await interaction.response.send_message(
                "Voting has ended!", ephemeral=True
            )
        uid = interaction.user.id
        previous = self.votes.get(uid)
        if previous == choice:
            return await interaction.response.send_message(
                f"You already voted for **{self.option_a if choice == 'A' else self.option_b}**!",
                ephemeral=True,
            )
        self.votes[uid] = choice
        label = self.option_a if choice == "A" else self.option_b
        if previous:
            msg = f"Changed your vote to **{label}**!"
        else:
            msg = f"Voted for **{label}**!"
        await interaction.response.send_message(msg, ephemeral=True)
        try:
            await interaction.message.edit(embed=self._voting_embed())
        except discord.HTTPException:
            pass

    @discord.ui.button(
        label="Option A", style=discord.ButtonStyle.blurple, emoji="\U0001f1e6"
    )
    async def btn_a(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_vote(interaction, "A")

    @discord.ui.button(
        label="Option B", style=discord.ButtonStyle.blurple, emoji="\U0001f1e7"
    )
    async def btn_b(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_vote(interaction, "B")


# ══════════════════════════════════════════════════════════════════════════════
#  Collect all unique nekos.best endpoints to scrape
# ══════════════════════════════════════════════════════════════════════════════

# Images cog endpoints (static images, not GIFs -- scraped the same way)
_IMAGE_ENDPOINTS = ("husbando", "kitsune", "neko", "waifu")

# All unique nekos.best endpoints across social + react + images
_ALL_NEKOS_ENDPOINTS: tuple[str, ...] = tuple(
    sorted(
        {d["endpoint"] for d in _SOCIAL_ACTIONS.values()}
        | {d["endpoint"] for d in _REACT_ACTIONS.values()}
        | set(_IMAGE_ENDPOINTS)
    )
)


# ══════════════════════════════════════════════════════════════════════════════
class Fun(commands.Cog):
    """Fun social interaction and reaction commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None

    async def cog_load(self):
        self._session = aiohttp.ClientSession()
        self._dynamic_cmds: list[commands.Command] = []
        self._register_prefix_commands()
        self._scrape_loop.start()
        self._revalidate_loop.start()

    async def cog_unload(self):
        self._scrape_loop.cancel()
        self._revalidate_loop.cancel()
        for cmd in self._dynamic_cmds:
            self.bot.remove_command(cmd.name)
        if self._session and not self._session.closed:
            await self._session.close()

    # ══════════════════════════════════════════════════════════════════════════
    #  Daily content scraper -- fills cache_db
    # ══════════════════════════════════════════════════════════════════════════

    @tasks.loop(hours=24)
    async def _scrape_loop(self):
        """Scrape FML, WYR, nekos.best, and Nekosia into cache_db."""
        if not self._session or self._session.closed:
            return

        start = time.monotonic()

        # ── FML ───────────────────────────────────────────────────────────
        try:
            fml_stories = await _scrape_fml_bulk(self._session)
            if fml_stories:
                added = await cache_db.add_fml_stories(fml_stories)
                total = await cache_db.count_fml()
                log.info(
                    f"FML scrape: {len(fml_stories)} scraped, "
                    f"{added} new, {total} total"
                )
            else:
                log.warning("FML scrape: 0 stories (site may be down)")
        except Exception as exc:
            log.error(f"FML scrape error: {exc}")

        # ── WYR (truthordarebot API -- PG + PG13) ─────────────────────────
        try:
            wyr_questions = await _scrape_wyr_bulk(self._session)
            if wyr_questions:
                added = await cache_db.add_wyr_questions(wyr_questions)
                total = await cache_db.count_wyr()
                log.info(
                    f"WYR scrape: {len(wyr_questions)} fetched, "
                    f"{added} new, {total} total"
                )
            else:
                log.warning("WYR scrape: 0 questions (API may be down)")
        except Exception as exc:
            log.error(f"WYR scrape error: {exc}")

        # ── WYR Kaggle seed (one-time) ────────────────────────────────────
        kaggle_done = await cache_db.get_meta("kaggle_wyr_seeded")
        if not kaggle_done:
            try:
                kaggle_qs = await _seed_kaggle_wyr(self._session)
                if kaggle_qs:
                    added = await cache_db.add_wyr_questions(kaggle_qs)
                    await cache_db.set_meta("kaggle_wyr_seeded", "1")
                    total = await cache_db.count_wyr()
                    log.info(
                        f"WYR Kaggle seed: {len(kaggle_qs)} parsed, "
                        f"{added} new, {total} total"
                    )
                else:
                    log.warning("WYR Kaggle seed: 0 questions (download failed)")
            except Exception as exc:
                log.error(f"WYR Kaggle seed error: {exc}")

        # ── WYR Groq generation ───────────────────────────────────────────
        groq_key = getattr(self.bot, "groq_api_key", None)
        if groq_key:
            try:
                groq_qs = await _generate_wyr_groq(self._session, groq_key)
                if groq_qs:
                    added = await cache_db.add_wyr_questions(groq_qs)
                    total = await cache_db.count_wyr()
                    log.info(
                        f"WYR Groq: {len(groq_qs)} generated, "
                        f"{added} new, {total} total"
                    )
            except Exception as exc:
                log.error(f"WYR Groq generation error: {exc}")
        else:
            log.debug("WYR Groq: no API key, skipping generation")

        # ── nekos.best (GIFs + static images) ─────────────────────────────
        nekos_total_added = 0
        for ep in _ALL_NEKOS_ENDPOINTS:
            try:
                results = await _fetch_nekos_batch(
                    self._session, ep, _NEKOS_PER_ENDPOINT
                )
                if results:
                    img_dicts = [
                        {
                            "url": r["url"],
                            "source_url": r.get("source_url"),
                            "artist": r.get("artist_name"),
                        }
                        for r in results
                    ]
                    added = await cache_db.add_images("nekos", ep, img_dicts)
                    nekos_total_added += added
            except Exception as exc:
                log.debug(f"nekos.best scrape error for '{ep}': {exc}")
            await asyncio.sleep(0.3)

        nekos_total = await cache_db.count_images("nekos")
        log.info(
            f"nekos.best scrape: {len(_ALL_NEKOS_ENDPOINTS)} endpoints, "
            f"{nekos_total_added} new, {nekos_total} total"
        )

        # ── Nekosia (thigh tags) ──────────────────────────────────────────
        nekosia_total_added = 0
        for tag in _THIGH_TAGS:
            for _ in range(_NEKOSIA_PER_TAG):
                try:
                    img, src = await _fetch_nekosia_single(self._session, tag)
                    if img:
                        added = await cache_db.add_images(
                            "nekosia",
                            tag,
                            [{"url": img, "source_url": src}],
                        )
                        nekosia_total_added += added
                except Exception as exc:
                    log.debug(f"Nekosia scrape error for '{tag}': {exc}")
                await asyncio.sleep(0.5)

        nekosia_total = await cache_db.count_images("nekosia")
        log.info(
            f"Nekosia scrape: {len(_THIGH_TAGS)} tags, "
            f"{nekosia_total_added} new, {nekosia_total} total"
        )

        elapsed = time.monotonic() - start
        await cache_db.set_meta("last_scrape", str(time.time()))
        log.info(f"Daily scrape complete in {elapsed:.0f}s")

    @_scrape_loop.before_loop
    async def _before_scrape(self):
        """Wait for bot ready, log cache state."""
        await self.bot.wait_until_ready()
        fml_count = await cache_db.count_fml()
        wyr_count = await cache_db.count_wyr()
        img_count = await cache_db.count_images()
        if fml_count == 0 or wyr_count == 0 or img_count == 0:
            log.info(
                f"Cache sparse (FML={fml_count}, WYR={wyr_count}, "
                f"images={img_count}), initial scrape starting..."
            )
        else:
            log.info(
                f"Cache loaded: {fml_count} FML, {wyr_count} WYR, "
                f"{img_count} images"
            )

    # ══════════════════════════════════════════════════════════════════════════
    #  URL revalidation -- prune dead image URLs every 6 hours
    # ══════════════════════════════════════════════════════════════════════════

    @tasks.loop(hours=6)
    async def _revalidate_loop(self):
        """HEAD-check stale image URLs and remove dead ones."""
        if not self._session or self._session.closed:
            return

        stale = await cache_db.get_stale_images(
            max_age_seconds=_REVALIDATE_AGE,
            limit=_REVALIDATE_BATCH,
        )
        if not stale:
            return

        removed = 0
        verified = 0
        for entry in stale:
            try:
                async with self._session.head(
                    entry["url"],
                    timeout=aiohttp.ClientTimeout(total=5),
                    allow_redirects=True,
                ) as resp:
                    if resp.status in (200, 301, 302, 304):
                        await cache_db.mark_verified(entry["hash"])
                        verified += 1
                    else:
                        await cache_db.remove_image(entry["hash"])
                        removed += 1
            except Exception:
                # Network error -- don't remove, just skip this round
                pass
            await asyncio.sleep(0.2)

        if removed or verified:
            log.info(
                f"Revalidation: {verified} verified, {removed} removed "
                f"(of {len(stale)} checked)"
            )

    @_revalidate_loop.before_loop
    async def _before_revalidate(self):
        await self.bot.wait_until_ready()
        # Stagger so it doesn't overlap with the scrape loop start
        await asyncio.sleep(300)

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
        gif = await _get_gif(self._session, data["endpoint"])
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
        gif = await _get_gif(self._session, data["endpoint"])
        if gif:
            e.set_image(url=gif)
        e.set_footer(text="NanoBot Fun")
        return e

    # ══════════════════════════════════════════════════════════════════════════
    #  SLASH: /fun group  (7 subcommands, 1 top-level slot)
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
        story = await cache_db.get_random_fml()
        if not story:
            return await i.response.send_message(
                "No FML stories cached yet -- try again in a few minutes!",
                ephemeral=True,
            )
        e = discord.Embed(description=story, color=_FML_BLUE)
        e.set_footer(text="NanoBot Fun \u00b7 fmylife.com")
        await i.response.send_message(embed=e)

    # ── /fun thigh ─────────────────────────────────────────────────────────

    @fun_group.command(name="thigh", description="Random anime thigh pic (SFW)")
    async def s_thigh(self, i: discord.Interaction):
        tag = random.choice(_THIGH_TAGS)
        img, src = await _get_nekosia(self._session, tag)
        if not img:
            return await i.response.send_message(
                "No thigh images cached yet -- try again in a few minutes!",
                ephemeral=True,
            )
        e = discord.Embed(color=_PINK)
        e.set_image(url=img)
        if src:
            e.description = f"[\U0001f517 Source]({src})"
        e.set_footer(text="NanoBot Fun \u00b7 nekosia.cat")
        await i.response.send_message(embed=e)

    # ── /fun wyr ───────────────────────────────────────────────────────────

    @fun_group.command(name="wyr", description="Would You Rather -- vote with buttons!")
    @app_commands.describe(
        duration="How long voting lasts (e.g. 30m, 2h, 1h30m). Default: 1h"
    )
    async def s_wyr(self, i: discord.Interaction, duration: str | None = None):
        secs = _parse_duration(duration)
        question = await cache_db.get_random_wyr()
        if not question:
            return await i.response.send_message(
                "No WYR questions cached yet -- try again in a few minutes!",
                ephemeral=True,
            )
        opt_a, opt_b = _split_wyr(question)
        view = WyrView(opt_a, opt_b, duration=secs)
        await i.response.send_message(embed=view._voting_embed(), view=view)
        view.message = await i.original_response()

    # ══════════════════════════════════════════════════════════════════════════
    #  PREFIX: flat commands  (!hug, !cry, !ship, !8ball, etc.)
    # ══════════════════════════════════════════════════════════════════════════

    def _register_prefix_commands(self):
        """Build and register all factory prefix commands on the bot."""
        cog = self

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
                async def social_cmd(ctx, user: Optional[discord.Member] = None):
                    e = await cog._action_embed(ctx.guild.me, ctx.author, user, data)
                    await ctx.reply(embed=e)

                return social_cmd

            social_cmd = _make_social(name, aliases, extras, data)
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
                async def react_cmd(ctx):
                    e = await cog._react_embed(ctx.author, data)
                    await ctx.reply(embed=e)

                return react_cmd

            react_cmd = _make_react(action, extras, data)
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
        story = await cache_db.get_random_fml()
        if not story:
            return await ctx.reply(
                "No FML stories cached yet -- try again in a few minutes!"
            )
        e = discord.Embed(description=story, color=_FML_BLUE)
        e.set_footer(text="NanoBot Fun \u00b7 fmylife.com")
        await ctx.reply(embed=e)

    @commands.command(
        name="thigh",
        aliases=["thighs", "legs", "leg"],
        extras={
            "category": "\U0001f389 Fun",
            "short": "Random anime thigh pic",
            "usage": "thigh",
            "desc": "Get a random anime thigh pic (SFW).",
            "args": [],
            "perms": "None",
            "example": "!thigh",
        },
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def pfx_thigh(self, ctx):
        tag = random.choice(_THIGH_TAGS)
        img, src = await _get_nekosia(self._session, tag)
        if not img:
            return await ctx.reply(
                "No thigh images cached yet -- try again in a few minutes!"
            )
        e = discord.Embed(color=_PINK)
        e.set_image(url=img)
        if src:
            e.description = f"[\U0001f517 Source]({src})"
        e.set_footer(text="NanoBot Fun \u00b7 nekosia.cat")
        await ctx.reply(embed=e)

    @commands.command(
        name="wyr",
        aliases=["wouldyourather"],
        extras={
            "category": "\U0001f389 Fun",
            "short": "Would You Rather",
            "usage": "wyr [duration]",
            "desc": "Start a Would You Rather poll with buttons. Duration examples: 30m, 2h, 1h30m. Default: 1h. Max: 24h.",
            "args": [("duration", "How long voting lasts (optional, default 1h)")],
            "perms": "None",
            "example": "!wyr 30m",
        },
    )
    @commands.cooldown(1, 10, commands.BucketType.channel)
    async def pfx_wyr(self, ctx, *, duration: str | None = None):
        secs = _parse_duration(duration)
        question = await cache_db.get_random_wyr()
        if not question:
            return await ctx.reply(
                "No WYR questions cached yet -- try again in a few minutes!"
            )
        opt_a, opt_b = _split_wyr(question)
        view = WyrView(opt_a, opt_b, duration=secs)
        msg = await ctx.reply(embed=view._voting_embed(), view=view)
        view.message = msg


async def setup(bot: commands.Bot):
    await bot.add_cog(Fun(bot))
