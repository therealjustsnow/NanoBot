"""
utils/helpers.py
Shared utilities:
  - Embed factory (consistently styled, mobile-optimized)
  - Duration parsing  ("30s", "5m", "2h", "1d" → seconds)
  - Duration formatting (seconds → "5m 30s")
"""

import re
from datetime import datetime, timezone

import discord

# ── Brand colours ──────────────────────────────────────────────────────────────
GREEN  = 0x57F287   # success
RED    = 0xED4245   # error
YELLOW = 0xFEE75C   # warning
BLUE   = 0x5865F2   # info / neutral
GREY   = 0x2B2D31   # default


# ── Embed Factory ──────────────────────────────────────────────────────────────
def embed(
    title: str = "",
    description: str = "",
    color: int = GREY,
    *,
    footer: str = "NanoBot",
) -> discord.Embed:
    """Create a base NanoBot embed with timestamp."""
    e = discord.Embed(title=title, description=description, color=color)
    e.set_footer(text=footer)
    e.timestamp = datetime.now(timezone.utc)
    return e


def ok(description: str, title: str = "✅ Done") -> discord.Embed:
    """Green success embed."""
    return embed(title, description, GREEN)


def err(description: str, title: str = "❌ Error") -> discord.Embed:
    """Red error embed."""
    return embed(title, description, RED)


def warn(description: str, title: str = "⚠️ Warning") -> discord.Embed:
    """Yellow warning embed."""
    return embed(title, description, YELLOW)


def info(description: str, title: str = "ℹ️ Info") -> discord.Embed:
    """Blue info embed."""
    return embed(title, description, BLUE)


# ── Duration Parsing ───────────────────────────────────────────────────────────
_UNITS_SHORT = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}

_UNITS_LONG = {
    "second": 1,  "seconds": 1,  "sec": 1,  "secs": 1,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600,
    "day": 86400, "days": 86400,
    "week": 604800, "weeks": 604800, "wk": 604800, "wks": 604800,
}

# Matches: "8h", "30m", "1d", "2w", "60s", "60" (bare = seconds)
_PATTERN_SHORT = re.compile(r"^(\d+)\s*([smhdw]?)$", re.IGNORECASE)
# Matches: "8 hours", "30 minutes", "1 day", "2 weeks"
_PATTERN_LONG  = re.compile(r"^(\d+)\s+(" + "|".join(_UNITS_LONG) + r")$", re.IGNORECASE)
# Matches duration at END of a string: "remind me 8h" or "do laundry in 2 hours"
_PATTERN_TAIL  = re.compile(
    r"(?:\s+in\s+|\s+)(\d+)\s*(" + "|".join(list(_UNITS_LONG) + list(_UNITS_SHORT)) + r")\s*$",
    re.IGNORECASE,
)


def parse_duration(s: str | None) -> int | None:
    """
    Parse a standalone duration string into seconds.
    Supports shorthand (8h, 30m, 2d, 1w) and natural language (8 hours, 30 minutes).
    Returns None for invalid/missing input.
    """
    if not s:
        return None
    s = s.strip()
    m = _PATTERN_SHORT.match(s)
    if m:
        value = int(m.group(1))
        unit  = (m.group(2) or "s").lower()
        return value * _UNITS_SHORT[unit]
    m = _PATTERN_LONG.match(s)
    if m:
        return int(m.group(1)) * _UNITS_LONG[m.group(2).lower()]
    return None


def parse_duration_from_end(text: str) -> tuple[str, int | None]:
    """
    Extract a duration from the END of a reminder string.
    Returns (cleaned_text, seconds) or (original_text, None).

    Examples:
        "go for a run in 2 hours"   → ("go for a run", 7200)
        "call mum 30m"              → ("call mum", 1800)
        "stand up in 45 minutes"    → ("stand up", 2700)
        "no duration here"          → ("no duration here", None)
    """
    m = _PATTERN_TAIL.search(text)
    if not m:
        return text, None
    raw_unit = m.group(2).lower()
    mult = _UNITS_LONG.get(raw_unit) or _UNITS_SHORT.get(raw_unit)
    if not mult:
        return text, None
    secs    = int(m.group(1)) * mult
    cleaned = text[:m.start()].strip()
    return cleaned, secs


def fmt_duration(seconds: int) -> str:
    """
    Format a number of seconds into a human-readable string.

    Examples:
        45    → "45s"
        90    → "1m 30s"
        3600  → "1h"
        90061 → "1d 1h"
    """
    if seconds <= 0:
        return "0s"

    parts = []
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if seconds >= size:
            val = seconds // size
            seconds %= size
            parts.append(f"{val}{unit}")

    return " ".join(parts[:2])  # cap at 2 units for readability (e.g. "1d 2h")


# ── Misc Helpers ───────────────────────────────────────────────────────────────
def user_display(member: discord.Member) -> str:
    """Return 'Display Name (@username) | ID' for consistent user references."""
    return f"**{member.display_name}** (`{member.name}` · `{member.id}`)"
