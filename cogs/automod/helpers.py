"""Pure-ish AutoMod rule-check helpers: spam tracking, content matchers, and
the ReDoS-bounded regex matchers. No cog/DB state beyond the shared tracker.
"""

import asyncio
import logging
import re
import time

import discord

from .constants import (
    _MAX_PATTERN_LEN,
    _MAX_REGEX_INPUT,
    _RE_INVITE,
    _RE_URL,
    _REDOS_RE,
    _REGEX_CACHE_MAX,
    _REGEX_TIMEOUT,
    _spam_tracker,
    _user_regex_cache,
)

log = logging.getLogger("NanoBot.automod")


def _check_spam(guild_id: int, user_id: int, count: int, window: int) -> bool:
    """
    Record this message and return True if the user has exceeded the spam threshold
    (sent `count` or more messages within the last `window` seconds).
    """
    now = time.monotonic()
    q = _spam_tracker[guild_id][user_id]
    q.append(now)
    cutoff = now - window
    while q and q[0] < cutoff:
        q.popleft()
    return len(q) >= count


def _clear_spam(guild_id: int, user_id: int) -> None:
    """Reset the spam counter for a user after taking action."""
    _spam_tracker[guild_id].pop(user_id, None)


# ── Rule-check helpers ─────────────────────────────────────────────────────────


def _has_invite(content: str) -> bool:
    return bool(_RE_INVITE.search(content))


def _has_link(content: str) -> bool:
    return bool(_RE_URL.search(content))


def _caps_percent(content: str) -> float:
    letters = [c for c in content if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if c.isupper()) / len(letters) * 100


def _mention_count(message: discord.Message) -> int:
    return len(message.mentions) + len(message.role_mentions)


def _has_badword(content: str, words: list[str]) -> str | None:
    """Return the first matched bad word (whole-word, case-insensitive), or None."""
    for word in words:
        if re.search(r"\b" + re.escape(word) + r"\b", content, re.IGNORECASE):
            return word
    return None


def _matches_regex(content: str, patterns: list[dict]) -> str | None:
    """
    Test content against each stored regex pattern (case-insensitive).
    Returns the label or pattern string of the first match, or None.
    Silently skips any pattern that fails to compile (shouldn't happen since
    we validate on add, but safe to guard here too).

    Compiled patterns are cached in _user_regex_cache to avoid recompiling
    the same pattern string on every incoming message.
    """
    for p in patterns:
        raw = p["pattern"]
        try:
            compiled = _user_regex_cache.get(raw)
            if compiled is None:
                compiled = re.compile(raw, re.IGNORECASE)
                if len(_user_regex_cache) >= _REGEX_CACHE_MAX:
                    _user_regex_cache.clear()
                _user_regex_cache[raw] = compiled
            if compiled.search(content):
                return p["label"] or p["pattern"]
        except re.error:
            pass
    return None


def _is_risky_regex(pattern: str) -> bool:
    """Heuristic: True if the pattern looks prone to catastrophic backtracking."""
    return len(pattern) > _MAX_PATTERN_LEN or bool(_REDOS_RE.search(pattern))


async def _matches_regex_safe(content: str, patterns: list[dict]) -> str | None:
    """Run _matches_regex in a worker thread under a wall-clock timeout.

    A malicious or accidental catastrophic-backtracking pattern can hang the
    regex engine; offloading to a thread keeps the event loop responsive, and
    the timeout bounds how long any single check can run.
    """
    if not patterns:
        return None
    snippet = content[:_MAX_REGEX_INPUT]
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, _matches_regex, snippet, patterns),
            timeout=_REGEX_TIMEOUT,
        )
    except asyncio.TimeoutError:
        log.warning(
            "automod: regex match exceeded %.1fs — skipping (possible ReDoS pattern)",
            _REGEX_TIMEOUT,
        )
        return None


def _all_matches_regex(content: str, patterns: list[dict]) -> list[dict]:
    """Return every stored pattern that matches content. For /automod regex test."""
    hits = []
    for p in patterns:
        try:
            compiled = _user_regex_cache.get(p["pattern"])
            if compiled is None:
                compiled = re.compile(p["pattern"], re.IGNORECASE)
                if len(_user_regex_cache) >= _REGEX_CACHE_MAX:
                    _user_regex_cache.clear()
                _user_regex_cache[p["pattern"]] = compiled
            if compiled.search(content):
                hits.append(p)
        except re.error:
            pass
    return hits


async def _all_matches_regex_safe(content: str, patterns: list[dict]) -> list[dict]:
    """Thread-bounded variant of _all_matches_regex under the ReDoS timeout."""
    if not patterns:
        return []
    snippet = content[:_MAX_REGEX_INPUT]
    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, _all_matches_regex, snippet, patterns),
            timeout=_REGEX_TIMEOUT,
        )
    except asyncio.TimeoutError:
        log.warning(
            "automod: regex test exceeded %.1fs — aborted (possible ReDoS pattern)",
            _REGEX_TIMEOUT,
        )
        return []
