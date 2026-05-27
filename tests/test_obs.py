"""
tests/test_obs.py
Unit tests for utils/obs.py — correlation ids, the logging filter, and the
JSONL event sink. Pure Python (no Discord).
"""

import json
import logging

from utils import obs


# ── correlation ids ────────────────────────────────────────────────────────────
def test_new_cid_is_short_hex_and_unique():
    a, b = obs.new_cid(), obs.new_cid()
    assert a != b
    assert len(a) == 8
    assert all(c in "0123456789abcdef" for c in a)


def test_cid_set_get_reset():
    assert obs.get_cid() == "-"
    token = obs.set_cid("abc123")
    try:
        assert obs.get_cid() == "abc123"
    finally:
        obs.reset_cid(token)
    assert obs.get_cid() == "-"


def test_correlation_filter_injects_current_cid():
    f = obs.CorrelationFilter()
    record = logging.LogRecord("n", logging.INFO, "p", 1, "msg", None, None)
    token = obs.set_cid("xyz789")
    try:
        assert f.filter(record) is True
        assert record.cid == "xyz789"
    finally:
        obs.reset_cid(token)


def test_correlation_filter_preserves_existing_cid():
    f = obs.CorrelationFilter()
    record = logging.LogRecord("n", logging.INFO, "p", 1, "msg", None, None)
    record.cid = "preset"
    token = obs.set_cid("other")
    try:
        f.filter(record)
        assert record.cid == "preset"
    finally:
        obs.reset_cid(token)


# ── JSONL event sink ────────────────────────────────────────────────────────────
def test_log_event_writes_one_json_line(tmp_path):
    path = tmp_path / "events.jsonl"
    obs.setup_events_logger(True, path=str(path))
    token = obs.set_cid("cid12345")
    try:
        obs.log_event("command.ok", cmd="ban", dur_ms=12, user=42)
    finally:
        obs.reset_cid(token)
        obs.setup_events_logger(False)

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["event"] == "command.ok"
    assert rec["cmd"] == "ban"
    assert rec["dur_ms"] == 12
    assert rec["user"] == 42
    assert rec["cid"] == "cid12345"
    assert "ts" in rec


def test_log_event_noop_when_disabled(tmp_path):
    path = tmp_path / "events.jsonl"
    obs.setup_events_logger(False, path=str(path))
    obs.log_event("command.ok", cmd="ban")
    assert not (path.exists() and path.read_text(encoding="utf-8").strip())


def test_log_event_serializes_non_json_values(tmp_path):
    path = tmp_path / "events.jsonl"
    obs.setup_events_logger(True, path=str(path))

    class Weird:
        def __str__(self):
            return "weird-obj"

    try:
        obs.log_event("e", obj=Weird())
    finally:
        obs.setup_events_logger(False)

    rec = json.loads(path.read_text(encoding="utf-8").strip())
    assert rec["obj"] == "weird-obj"


def test_setup_is_idempotent_no_handler_stacking(tmp_path):
    path = tmp_path / "events.jsonl"
    obs.setup_events_logger(True, path=str(path))
    obs.setup_events_logger(True, path=str(path))
    obs.setup_events_logger(True, path=str(path))
    logger = logging.getLogger(obs.EVENTS_LOGGER_NAME)
    try:
        assert len(logger.handlers) == 1
    finally:
        obs.setup_events_logger(False)
