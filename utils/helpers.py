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
_UNITS = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
}

_PATTERN = re.compile(r"^(\d+)\s*([smhd]?)$", re.IGNORECASE)


def parse_duration(s: str | None) -> int | None:
    """
    Parse a duration string into seconds.

    Examples:
        "30s"  → 30
        "5m"   → 300
        "2h"   → 7200
        "1d"   → 86400
        "60"   → 60  (bare number = seconds)
        None / invalid → None
    """
    if not s:
        return None
    m = _PATTERN.match(s.strip())
    if not m:
        return None
    value = int(m.group(1))
    unit  = (m.group(2) or "s").lower()
    return value * _UNITS[unit]


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
