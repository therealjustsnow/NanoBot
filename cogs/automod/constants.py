"""AutoMod constants: rule/action labels, compiled regexes, ReDoS guards,
and the in-memory spam tracker (shared by helpers + the cog).
"""

import re
from collections import defaultdict, deque

TIMEOUT_SECONDS = 600  # 10 minutes for the "timeout" action

RULE_LABELS: dict[str, str] = {
    "spam": "💬 Spam",
    "invites": "📨 Invite Links",
    "links": "🔗 External Links",
    "caps": "🔠 Caps Abuse",
    "mentions": "📣 Mass Mentions",
    "badwords": "🤬 Bad Words",
    "regex": "🔍 Regex Filter",
    "attachment_word": "📎 Word + Attachment",
}

ACTION_LABELS: dict[str, str] = {
    "delete": "🗑️ Delete",
    "warn": "⚠️ Delete + Warn",
    "timeout": "🔇 Delete + Timeout",
    "kick": "👢 Delete + Kick",
    "softban": "🔨 Delete + Softban",
}

# Pre-compiled regex patterns
_RE_INVITE = re.compile(
    r"(discord\.(gg|com/invite)|discordapp\.com/invite)/[a-zA-Z0-9\-]+",
    re.IGNORECASE,
)
_RE_URL = re.compile(
    r"https?://[^\s<>\"]+|www\.[^\s<>\"]+",
    re.IGNORECASE,
)

# Cache for user-defined per-guild regex patterns (keyed by raw pattern string).
# Avoids recompiling the same pattern on every incoming message. Bounded so a
# guild that churns through many patterns can't grow it without limit.
_user_regex_cache: dict[str, re.Pattern] = {}
_REGEX_CACHE_MAX = 512

# Guard against catastrophic backtracking (ReDoS) from admin-supplied patterns:
# matching runs in a worker thread with a hard wall-clock timeout, and we only
# scan a bounded slice of the message so the event loop can never freeze.
_REGEX_TIMEOUT = 0.5
_MAX_REGEX_INPUT = 2000


# ── In-memory spam tracker ─────────────────────────────────────────────────────
# Structure: {guild_id: {user_id: deque[float(timestamp)]}}
# Timestamps older than the window are pruned on every check.
_spam_tracker: dict[int, dict[int, deque]] = defaultdict(lambda: defaultdict(deque))


# Catches the most common catastrophic-backtracking shape: a quantifier applied
# to a group that itself ends in a quantifier, e.g. (a+)+ , (a*)* , ([a-z]+)* .
# Not exhaustive, but blocks the easy footguns at add-time.
_REDOS_RE = re.compile(r"\([^)]*[+*}]\s*\)\s*[+*{]")
_MAX_PATTERN_LEN = 400
