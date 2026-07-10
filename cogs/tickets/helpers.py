"""Pure, Discord-free helpers for the tickets cog (covered by
tests/test_tickets_helpers.py)."""

import re

_NAME_SAFE_RE = re.compile(r"[^a-z0-9\-_]+")


def thread_name(number: int, username: str) -> str:
    """Build a ticket thread name like "ticket-0042-username".

    Discord caps thread names at 100 chars; the username part is lowercased,
    reduced to safe characters, and truncated so the full name always fits.
    A username with no safe characters at all falls back to just the number.
    """
    base = f"ticket-{number:04d}"
    safe = _NAME_SAFE_RE.sub("-", username.lower()).strip("-")
    if not safe:
        return base
    return f"{base}-{safe}"[:100]


def transcript_line(
    timestamp: str, author: str, content: str, attachments: list[str]
) -> str:
    """One transcript line: "[ts] author: content" plus attachment URLs."""
    body = content if content else ""
    if attachments:
        urls = " ".join(attachments)
        body = f"{body} [attachments: {urls}]" if body else f"[attachments: {urls}]"
    if not body:
        body = "[no text content]"
    return f"[{timestamp}] {author}: {body}"
