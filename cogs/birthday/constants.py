"""Birthday cog constants: colours, default message, GIF pool, timezone choices
and region map, month tables, and the Happy Birthday note sequence + song path.
"""

import os

BIRTHDAY_COLOR = 0xFF6FB5

_DEFAULT_MESSAGE = (
    "🎉 It's {mention}'s birthday today! Everybody wish them a happy birthday! 🎂🎈"
)

_VARS_HELP = (
    "`{mention}` — ping  ·  `{user}` — display name  ·  "
    "`{username}` — full username  ·  `{server}` — server name  ·  "
    "`{age}` — age turning (if year known)"
)

# A handful of festive GIFs, picked at random per announcement. If one ever
# 404s Discord just drops the image — the text still lands.
_BIRTHDAY_GIFS = [
    "https://media.giphy.com/media/Os4Mk2lDWNXMI/giphy.gif",
    "https://media.giphy.com/media/7Js6gSMQVgT5C/giphy.gif",
    "https://media.giphy.com/media/26u4cqiYI30juCOGY/giphy.gif",
    "https://media.giphy.com/media/g5R9dok94mrIvplmZd/giphy.gif",
    "https://media.giphy.com/media/l0MYt5jPR6QX5pnqM/giphy.gif",
    "https://media.giphy.com/media/IRsBljLW2bWPYTb1IY/giphy.gif",
]

# ── Timezone picker ────────────────────────────────────────────────────────────
# Friendly label → IANA name. zoneinfo applies the right offset *and* DST from
# the name, so e.g. US Central is correct year-round (UTC-6 winter / -5 summer) —
# never hardcode a fixed offset. Offsets in the labels are winter-standard hints.
# Kept at 25 entries max (Discord select-menu limit).
_TZ_CHOICES: list[tuple[str, str, str]] = [
    # (label, IANA name, emoji)
    ("Hawaii (UTC−10)", "Pacific/Honolulu", "🏝️"),
    ("Alaska (UTC−9)", "America/Anchorage", "❄️"),
    ("US Pacific — LA, Seattle (UTC−8)", "America/Los_Angeles", "🌉"),
    ("US Mountain — Denver (UTC−7)", "America/Denver", "⛰️"),
    ("US Arizona (UTC−7, no DST)", "America/Phoenix", "🌵"),
    ("US Central — Chicago, Iowa, Texas (UTC−6)", "America/Chicago", "🌽"),
    ("US Eastern — New York, Miami (UTC−5)", "America/New_York", "🗽"),
    ("Atlantic — Halifax (UTC−4)", "America/Halifax", "🦞"),
    ("Brazil — São Paulo (UTC−3)", "America/Sao_Paulo", "🇧🇷"),
    ("Argentina — Buenos Aires (UTC−3)", "America/Argentina/Buenos_Aires", "🇦🇷"),
    ("UTC / GMT", "UTC", "🌐"),
    ("UK — London, Dublin (UTC+0)", "Europe/London", "🇬🇧"),
    ("Central Europe — Paris, Berlin (UTC+1)", "Europe/Paris", "🇪🇺"),
    ("Eastern Europe — Athens, Helsinki (UTC+2)", "Europe/Athens", "🏛️"),
    ("Moscow, Istanbul (UTC+3)", "Europe/Moscow", "🇷🇺"),
    ("Gulf — Dubai (UTC+4)", "Asia/Dubai", "🏜️"),
    ("India — Mumbai, Delhi (UTC+5:30)", "Asia/Kolkata", "🇮🇳"),
    ("SE Asia — Bangkok, Jakarta (UTC+7)", "Asia/Bangkok", "🛺"),
    ("China, Singapore (UTC+8)", "Asia/Singapore", "🇸🇬"),
    ("Japan, Korea (UTC+9)", "Asia/Tokyo", "🗾"),
    ("Australia East — Sydney (UTC+10)", "Australia/Sydney", "🦘"),
    ("New Zealand — Auckland (UTC+12)", "Pacific/Auckland", "🇳🇿"),
    ("South Africa — Johannesburg (UTC+2)", "Africa/Johannesburg", "🇿🇦"),
    ("West Africa — Lagos (UTC+1)", "Africa/Lagos", "🇳🇬"),
]

# Discord voice region code → best-effort IANA tz, used to pre-fill a guild's
# timezone at setup when a mod hasn't picked one. Rough (a region spans many
# zones) — only a starting guess the mod can override.
_REGION_TZ: dict[str, str] = {
    "us-west": "America/Los_Angeles",
    "us-east": "America/New_York",
    "us-central": "America/Chicago",
    "us-south": "America/Chicago",
    "brazil": "America/Sao_Paulo",
    "europe": "Europe/Paris",
    "rotterdam": "Europe/Amsterdam",
    "russia": "Europe/Moscow",
    "singapore": "Asia/Singapore",
    "hongkong": "Asia/Hong_Kong",
    "japan": "Asia/Tokyo",
    "south-korea": "Asia/Seoul",
    "india": "Asia/Kolkata",
    "dubai": "Asia/Dubai",
    "southafrica": "Africa/Johannesburg",
    "sydney": "Australia/Sydney",
}


_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]  # fmt: skip

_MONTHS = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}  # fmt: skip

# Max day per month (February allows 29 so leap-day birthdays register).
_MAX_DAY = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


_HB_NOTES = [
    (392, 0.25), (392, 0.25), (440, 0.50), (392, 0.50), (523, 0.50), (494, 1.00),
    (392, 0.25), (392, 0.25), (440, 0.50), (392, 0.50), (587, 0.50), (523, 1.00),
    (392, 0.25), (392, 0.25), (784, 0.50), (659, 0.50), (523, 0.50), (494, 0.50), (440, 1.00),
    (698, 0.25), (698, 0.25), (659, 0.50), (523, 0.50), (587, 0.50), (523, 1.00),
]  # fmt: skip

_SONG_DIR = os.path.join("data", "birthday_cache")
_SONG_PATH = os.path.join(_SONG_DIR, "happy_birthday.wav")
