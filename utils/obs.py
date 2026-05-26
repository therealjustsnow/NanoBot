"""
utils/obs.py
Lightweight observability: correlation IDs and a structured JSONL event sink.

Two outputs work together (see main.py):

  * The human-readable text log (logs/nanobot.log, read on mobile via !logs)
    gains a short correlation id column via CorrelationFilter, so the start /
    completion / error lines of one command invocation share an id you can grep.

  * A machine-parseable event stream (logs/events.jsonl) carries one JSON object
    per line for command lifecycle events — handy for later analysis without
    bloating the text log.

The correlation id lives in a contextvar so any log record emitted while a
command runs (in the same task) picks it up automatically. It defaults to "-"
when nothing is in flight.
"""

from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
import os
import time
import uuid

# Correlation id for the in-flight command. "-" means nothing is in flight.
_cid_var: contextvars.ContextVar[str] = contextvars.ContextVar("nb_cid", default="-")

EVENTS_LOGGER_NAME = "nanobot.events"
_events_log = logging.getLogger(EVENTS_LOGGER_NAME)


def new_cid() -> str:
    """A fresh short correlation id (8 hex chars)."""
    return uuid.uuid4().hex[:8]


def get_cid() -> str:
    return _cid_var.get()


def set_cid(cid: str) -> contextvars.Token:
    """Set the current correlation id, returning a token for reset_cid()."""
    return _cid_var.set(cid)


def reset_cid(token: contextvars.Token) -> None:
    try:
        _cid_var.reset(token)
    except (ValueError, LookupError):
        # Token from a different context (e.g. listener ran in another task);
        # fall back to clearing rather than raising.
        _cid_var.set("-")


class CorrelationFilter(logging.Filter):
    """Inject the current correlation id onto every record as `record.cid`."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "cid"):
            record.cid = _cid_var.get()
        return True


def setup_events_logger(
    enabled: bool,
    path: str = "logs/events.jsonl",
    max_bytes: int = 1_000_000,
    backups: int = 3,
) -> None:
    """
    (Re)configure the JSONL event sink. Safe to call repeatedly (on reloadconfig);
    clears existing handlers first so it never stacks.
    """
    for h in list(_events_log.handlers):
        _events_log.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass

    _events_log.propagate = False  # never leak JSON into the text/root log

    if not enabled:
        _events_log.disabled = True
        return

    _events_log.disabled = False
    _events_log.setLevel(logging.INFO)
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        filename=path,
        maxBytes=max_bytes,
        backupCount=backups,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    _events_log.addHandler(handler)


def log_event(event: str, **fields) -> None:
    """
    Write one structured JSON line to the event sink. Always includes a
    timestamp, the event name, and the current correlation id. No-ops (and never
    raises) when the sink is disabled, so callers can fire freely.
    """
    if _events_log.disabled or not _events_log.handlers:
        return
    record = {
        "ts": round(time.time(), 3),
        "event": event,
        "cid": _cid_var.get(),
    }
    record.update(fields)
    try:
        _events_log.info(json.dumps(record, ensure_ascii=False, default=str))
    except Exception:
        # Observability must never take down a command.
        pass
