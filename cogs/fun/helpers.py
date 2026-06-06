"""Pure helpers for the fun cog (ship math, WYR parsing, duration formatting)."""

import hashlib
import re

from .constants import _SCRAPER_DEFAULTS, _WYR_SPLIT_RE, _DURATION_RE


def _scrape_cfg(bot, key: str):
    """Look up a [scraper] key on bot.config, falling back to the module default."""
    cfg = getattr(bot, "config", None) or {}
    val = cfg.get(key)
    if val is None or val == "":
        return _SCRAPER_DEFAULTS[key]
    return val


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
