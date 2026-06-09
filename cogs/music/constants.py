"""Module-level constants and compiled patterns for the music player."""

import os
import re

ACCENT = 0xEB459E

# ── Loop modes ──────────────────────────────────────────────────────────────────
LOOP_OFF = "off"
LOOP_TRACK = "track"
LOOP_QUEUE = "queue"
_LOOP_NEXT = {LOOP_OFF: LOOP_TRACK, LOOP_TRACK: LOOP_QUEUE, LOOP_QUEUE: LOOP_OFF}
_LOOP_LABEL = {LOOP_OFF: "Off", LOOP_TRACK: "🔂 Track", LOOP_QUEUE: "🔁 Queue"}

# ── Audio-effect presets (ffmpeg -af chains, comma-separated, no spaces) ────────
FILTERS: dict[str, str] = {
    "none": "",
    "bassboost": "bass=g=15",
    "treble": "treble=g=12",
    "nightcore": "asetrate=48000*1.25,aresample=48000",
    "vaporwave": "asetrate=48000*0.8,aresample=48000",
    "8d": "apulsator=hz=0.09",
    "muffle": "lowpass=f=600",
}

NP_REFRESH = 15  # seconds between live progress-bar refreshes
SEARCH_RESULTS = 5  # results shown by the search picker
PAGE_SIZE_QUEUE = 15  # tracks per page in queue display
PAGE_SIZE_APL = 25  # entries per page in autoplaylist display
NP_UP_NEXT = 5  # upcoming tracks shown on the Now Playing card

# ── Base yt-dlp options (cookies/limits merged in per call) ─────────────────────
_YTDL_BASE = {
    "format": "bestaudio[acodec!=none]/best[acodec!=none]/best",
    "noplaylist": False,
    "nocheckcertificate": False,
    "ignoreerrors": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "skip_download": True,
}

_FFMPEG_BEFORE = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"

# Where predownloaded tracks are cached (per-guild subdirs). Wiped on startup.
_MUSIC_CACHE_DIR = os.path.join("data", "music_cache")

# Upper bound for the Opus encode bitrate (kbps). The voice channel's own
# bitrate is the real ceiling; this just caps absurd values. Discord allows up
# to 384k on boosted servers, 512 covers any future bump.
_OPUS_MAX_KBITRATE = 512
_OPUS_MIN_KBITRATE = 48

# ── Spotify (no API key — metadata is scraped, then searched on YouTube) ────────
_SPOTIFY_RE = re.compile(
    r"open\.spotify\.com/(?:intl-\w+/)?(track|album|playlist)/(\w+)"
    r"|spotify:(track|album|playlist):(\w+)",
    re.IGNORECASE,
)
_SPOTIFY_NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# ── Strip common noise from YouTube titles when looking up lyrics ───────────────
_LYRICS_NOISE = re.compile(
    r"\([^)]*\)"  # ( … )
    r"|\[[^\]]*\]"  # [ … ]
    r"|\s+(?:ft|feat)\.?\s+.*$"  # " ft. …" / " feat. …"
    r"|\b(?:official\s+)?(?:music\s+)?video\b"
    r"|\blyrics?\b"
    r"|\baudio\b"
    r"|\bhd\b|\b4k\b|\bm/?v\b",
    re.IGNORECASE,
)


_YTID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?.*?v=|shorts/|embed/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)


_GUILDPLAY_REQUESTER = "📻 Guild Play"
_AUTOPLAY_REQUESTER = "✨ Autoplay"

# ── SponsorBlock (skip non-music / sponsor segments via yt-dlp postprocessors) ──
# Only the categories yt-dlp can remove. "music_offtopic" is the non-music
# section of a music video (the default — what most people want skipped).
_SPONSORBLOCK_CATEGORIES = {
    "sponsor",
    "intro",
    "outro",
    "selfpromo",
    "preview",
    "filler",
    "interaction",
    "music_offtopic",
    "poi_highlight",
}
_SPONSORBLOCK_DEFAULT = ["music_offtopic"]
