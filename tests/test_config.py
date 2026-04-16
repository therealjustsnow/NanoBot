"""
tests/test_config.py
Unit tests for utils/config.py validate().

validate() is pure Python (no Discord dependency) — operates only on a dict.
"""

import pytest

from utils.config import validate


# ══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════════════

# A token string that is not a placeholder and not empty.
_VALID_TOKEN = "Bot.valid.token1234567890abcdef"


def _fatal(issues, field):
    """Return fatal issues for a given field."""
    return [i for i in issues if i.fatal and i.field == field]


def _warnings(issues, field):
    """Return non-fatal issues for a given field."""
    return [i for i in issues if not i.fatal and i.field == field]


# ══════════════════════════════════════════════════════════════════════════════
#  Full valid config
# ══════════════════════════════════════════════════════════════════════════════


def test_valid_full_config_no_issues():
    cfg = {
        "token": _VALID_TOKEN,
        "default_prefix": "n!",
        "owner_id": 123456789012345678,
        "log_level": "INFO",
        "log_http": False,
        "vote_webhook_port": 5000,
    }
    assert validate(cfg) == []


def test_minimal_valid_config_no_issues():
    assert validate({"token": _VALID_TOKEN}) == []


# ══════════════════════════════════════════════════════════════════════════════
#  Token
# ══════════════════════════════════════════════════════════════════════════════


def test_missing_token_is_fatal():
    issues = validate({})
    assert _fatal(issues, "token"), "expected fatal issue for missing token"


def test_empty_token_is_fatal():
    issues = validate({"token": ""})
    assert _fatal(issues, "token")


def test_placeholder_token_your_bot_token_here_is_fatal():
    issues = validate({"token": "YOUR_BOT_TOKEN_HERE"})
    assert _fatal(issues, "token")


def test_placeholder_token_token_is_fatal():
    issues = validate({"token": "TOKEN"})
    assert _fatal(issues, "token")


# ══════════════════════════════════════════════════════════════════════════════
#  default_prefix
# ══════════════════════════════════════════════════════════════════════════════


def test_prefix_with_space_is_fatal():
    issues = validate({"token": _VALID_TOKEN, "default_prefix": "n !"})
    assert _fatal(issues, "default_prefix")


def test_prefix_too_long_is_fatal():
    issues = validate({"token": _VALID_TOKEN, "default_prefix": "toolong"})
    assert _fatal(issues, "default_prefix")


def test_valid_prefix_no_issue():
    issues = validate({"token": _VALID_TOKEN, "default_prefix": "n!"})
    assert not _fatal(issues, "default_prefix")
    assert not _warnings(issues, "default_prefix")


def test_single_char_prefix_valid():
    issues = validate({"token": _VALID_TOKEN, "default_prefix": "!"})
    assert not [i for i in issues if i.field == "default_prefix"]


# ══════════════════════════════════════════════════════════════════════════════
#  log_level
# ══════════════════════════════════════════════════════════════════════════════


def test_invalid_log_level_is_warning():
    issues = validate({"token": _VALID_TOKEN, "log_level": "VERBOSE"})
    assert _warnings(issues, "log_level"), "expected non-fatal warning for bad log_level"


def test_valid_log_levels():
    for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        issues = validate({"token": _VALID_TOKEN, "log_level": level})
        assert not [i for i in issues if i.field == "log_level"], f"unexpected issue for level {level}"


# ══════════════════════════════════════════════════════════════════════════════
#  Unknown keys
# ══════════════════════════════════════════════════════════════════════════════


def test_unknown_key_is_warning():
    issues = validate({"token": _VALID_TOKEN, "typo_key": "value"})
    assert _warnings(issues, "typo_key"), "expected non-fatal warning for unknown key"


def test_unknown_key_is_not_fatal():
    issues = validate({"token": _VALID_TOKEN, "typo_key": "value"})
    assert not _fatal(issues, "typo_key")


# ══════════════════════════════════════════════════════════════════════════════
#  owner_id
# ══════════════════════════════════════════════════════════════════════════════


def test_non_numeric_owner_id_is_fatal():
    issues = validate({"token": _VALID_TOKEN, "owner_id": "notanid"})
    assert _fatal(issues, "owner_id")


def test_valid_owner_id_no_issue():
    issues = validate({"token": _VALID_TOKEN, "owner_id": 123456789012345678})
    assert not [i for i in issues if i.field == "owner_id"]


def test_null_owner_id_no_issue():
    issues = validate({"token": _VALID_TOKEN, "owner_id": None})
    assert not [i for i in issues if i.field == "owner_id"]


# ══════════════════════════════════════════════════════════════════════════════
#  vote_webhook_port
# ══════════════════════════════════════════════════════════════════════════════


def test_port_out_of_range_high_is_warning():
    issues = validate({"token": _VALID_TOKEN, "vote_webhook_port": 99999})
    assert _warnings(issues, "vote_webhook_port")


def test_port_zero_is_warning():
    issues = validate({"token": _VALID_TOKEN, "vote_webhook_port": 0})
    assert _warnings(issues, "vote_webhook_port")


def test_valid_port_no_issue():
    issues = validate({"token": _VALID_TOKEN, "vote_webhook_port": 5000})
    assert not [i for i in issues if i.field == "vote_webhook_port"]


def test_port_non_integer_is_warning():
    issues = validate({"token": _VALID_TOKEN, "vote_webhook_port": "8080"})
    assert _warnings(issues, "vote_webhook_port")
