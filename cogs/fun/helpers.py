"""Pure helpers for the fun cog (ship math, WYR parsing, duration formatting)."""

import hashlib
import json
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


# A complete JSON string literal: opening quote, escape-aware body, closing
# quote. A truncated one has no closing quote, so it simply doesn't match --
# which is what makes the salvage below safe on a half-written array.
_JSON_STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*"')
_FENCE_OPEN_RE = re.compile(r"^```[a-zA-Z]*\s*")
_FENCE_CLOSE_RE = re.compile(r"\s*```$")


def _strip_code_fence(text: str) -> str:
    """Drop a markdown fence the model wrapped its JSON in."""
    text = _FENCE_OPEN_RE.sub("", text.strip())
    return _FENCE_CLOSE_RE.sub("", text).strip()


def _clean_wyr(item) -> str | None:
    """Normalise one candidate question, or None if it isn't one."""
    if not isinstance(item, str):
        return None
    q = " ".join(item.split())
    low = q.lower()
    if not low.startswith("would you rather") or " or " not in low:
        return None
    return q.rstrip("?").rstrip() + "?"


def _iter_strings(node):
    """Yield every string anywhere in a decoded JSON value."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, list):
        for child in node:
            yield from _iter_strings(child)
    elif isinstance(node, dict):
        for child in node.values():
            yield from _iter_strings(child)


def parse_wyr_json(text: str) -> list[str]:
    """Read WYR questions out of an LLM reply, surviving a truncated array.

    The happy path is a plain JSON array of strings. Two things go wrong often
    enough to design for: the model wraps the array in a fence or an object
    (handled by walking whatever decodes), and the reply is cut off mid-string
    because the completion hit its token ceiling. In the second case the array
    never closes, so json.loads gives up on the whole thing and every question
    that *did* arrive is thrown away -- so a failed decode falls back to reading
    the complete string literals out of the text.

    Returns the questions in order, de-duplicated, normalised to end in "?".
    """
    text = _strip_code_fence(text)
    if not text:
        return []

    candidates: list[str] = []
    try:
        candidates = list(_iter_strings(json.loads(text)))
    except ValueError:
        # Truncated (or otherwise malformed): take the literals that are whole.
        for match in _JSON_STRING_RE.findall(text):
            try:
                candidates.append(json.loads(match))
            except ValueError:
                continue

    questions: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        q = _clean_wyr(item)
        if q and q.lower() not in seen:
            seen.add(q.lower())
            questions.append(q)
    return questions


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
