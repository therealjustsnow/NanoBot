"""
tests/test_config.py
Unit tests for utils/config.py validate().

validate() is pure Python (no Discord dependency) — operates only on a dict.
"""

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
    assert _warnings(
        issues, "log_level"
    ), "expected non-fatal warning for bad log_level"


def test_valid_log_levels():
    for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        issues = validate({"token": _VALID_TOKEN, "log_level": level})
        assert not [
            i for i in issues if i.field == "log_level"
        ], f"unexpected issue for level {level}"


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


# ══════════════════════════════════════════════════════════════════════════════
#  webhook_allowed_ips
# ══════════════════════════════════════════════════════════════════════════════


def test_webhook_allowed_ips_valid_single():
    issues = validate({"token": _VALID_TOKEN, "webhook_allowed_ips": "192.168.1.1"})
    assert not [i for i in issues if i.field == "webhook_allowed_ips"]


def test_webhook_allowed_ips_valid_cidr():
    issues = validate({"token": _VALID_TOKEN, "webhook_allowed_ips": "10.0.0.0/8"})
    assert not [i for i in issues if i.field == "webhook_allowed_ips"]


def test_webhook_allowed_ips_valid_multiple():
    issues = validate(
        {"token": _VALID_TOKEN, "webhook_allowed_ips": "1.2.3.4, 10.0.0.0/24"}
    )
    assert not [i for i in issues if i.field == "webhook_allowed_ips"]


def test_webhook_allowed_ips_invalid_warns():
    issues = validate({"token": _VALID_TOKEN, "webhook_allowed_ips": "not-an-ip"})
    assert _warnings(issues, "webhook_allowed_ips")


def test_webhook_allowed_ips_partial_invalid_warns():
    issues = validate(
        {"token": _VALID_TOKEN, "webhook_allowed_ips": "1.2.3.4, bad-entry"}
    )
    assert _warnings(issues, "webhook_allowed_ips")


# ══════════════════════════════════════════════════════════════════════════════
#  music_default_volume
# ══════════════════════════════════════════════════════════════════════════════


def test_music_volume_valid():
    for v in (0, 50, 100, 200):
        issues = validate({"token": _VALID_TOKEN, "music_default_volume": v})
        assert not [
            i for i in issues if i.field == "music_default_volume"
        ], f"unexpected issue for volume {v}"


def test_music_volume_too_high_warns():
    issues = validate({"token": _VALID_TOKEN, "music_default_volume": 201})
    assert _warnings(issues, "music_default_volume")


def test_music_volume_negative_warns():
    issues = validate({"token": _VALID_TOKEN, "music_default_volume": -1})
    assert _warnings(issues, "music_default_volume")


def test_music_volume_non_int_warns():
    issues = validate({"token": _VALID_TOKEN, "music_default_volume": "loud"})
    assert _warnings(issues, "music_default_volume")


# ══════════════════════════════════════════════════════════════════════════════
#  music_skip_ratio
# ══════════════════════════════════════════════════════════════════════════════


def test_music_skip_ratio_valid():
    for v in (0, 50, 100):
        issues = validate({"token": _VALID_TOKEN, "music_skip_ratio": v})
        assert not [
            i for i in issues if i.field == "music_skip_ratio"
        ], f"unexpected issue for ratio {v}"


def test_music_skip_ratio_over_100_warns():
    issues = validate({"token": _VALID_TOKEN, "music_skip_ratio": 101})
    assert _warnings(issues, "music_skip_ratio")


def test_music_skip_ratio_negative_warns():
    issues = validate({"token": _VALID_TOKEN, "music_skip_ratio": -1})
    assert _warnings(issues, "music_skip_ratio")


# ══════════════════════════════════════════════════════════════════════════════
#  music_max_queue
# ══════════════════════════════════════════════════════════════════════════════


def test_music_max_queue_valid():
    issues = validate({"token": _VALID_TOKEN, "music_max_queue": 500})
    assert not [i for i in issues if i.field == "music_max_queue"]


def test_music_max_queue_zero_warns():
    issues = validate({"token": _VALID_TOKEN, "music_max_queue": 0})
    assert _warnings(issues, "music_max_queue")


def test_music_max_queue_negative_warns():
    issues = validate({"token": _VALID_TOKEN, "music_max_queue": -1})
    assert _warnings(issues, "music_max_queue")


# ══════════════════════════════════════════════════════════════════════════════
#  music_default_speed
# ══════════════════════════════════════════════════════════════════════════════


def test_music_speed_valid():
    for v in ("0.5", "1.0", "2.0", "3.0"):
        issues = validate({"token": _VALID_TOKEN, "music_default_speed": v})
        assert not [
            i for i in issues if i.field == "music_default_speed"
        ], f"unexpected issue for speed {v}"


def test_music_speed_too_slow_warns():
    issues = validate({"token": _VALID_TOKEN, "music_default_speed": "0.4"})
    assert _warnings(issues, "music_default_speed")


def test_music_speed_too_fast_warns():
    issues = validate({"token": _VALID_TOKEN, "music_default_speed": "3.1"})
    assert _warnings(issues, "music_default_speed")


def test_music_speed_non_numeric_warns():
    issues = validate({"token": _VALID_TOKEN, "music_default_speed": "fast"})
    assert _warnings(issues, "music_default_speed")


def test_music_speed_none_no_issue():
    issues = validate({"token": _VALID_TOKEN, "music_default_speed": None})
    assert not [i for i in issues if i.field == "music_default_speed"]


# ══════════════════════════════════════════════════════════════════════════════
#  music_search_service
# ══════════════════════════════════════════════════════════════════════════════


def test_music_search_service_valid():
    for svc in ("ytsearch", "ytmsearch", "scsearch"):
        issues = validate({"token": _VALID_TOKEN, "music_search_service": svc})
        assert not [
            i for i in issues if i.field == "music_search_service"
        ], f"unexpected issue for {svc}"


def test_music_search_service_invalid_warns():
    issues = validate({"token": _VALID_TOKEN, "music_search_service": "googlesearch"})
    assert _warnings(issues, "music_search_service")


def test_music_search_service_empty_no_issue():
    issues = validate({"token": _VALID_TOKEN, "music_search_service": ""})
    assert not [i for i in issues if i.field == "music_search_service"]


# ══════════════════════════════════════════════════════════════════════════════
#  music booleans
# ══════════════════════════════════════════════════════════════════════════════


def test_music_boolean_keys_valid():
    bool_keys = (
        "music_use_opus",
        "music_persist_queue",
        "music_predownload",
        "music_self_deafen",
        "music_autoplay_autoskip",
        "music_save_videos",
        "music_ratelimit_leave",
        "music_apl_prune_on_error",
        "music_save_history",
    )
    for key in bool_keys:
        for val in (True, False):
            issues = validate({"token": _VALID_TOKEN, key: val})
            assert not [
                i for i in issues if i.field == key
            ], f"unexpected issue for {key}={val}"


def test_music_boolean_non_bool_warns():
    issues = validate({"token": _VALID_TOKEN, "music_use_opus": "yes"})
    assert _warnings(issues, "music_use_opus")


# ══════════════════════════════════════════════════════════════════════════════
#  music non-negative integer knobs
# ══════════════════════════════════════════════════════════════════════════════


def test_music_nonneg_ints_valid():
    for key in (
        "music_cache_max_mb",
        "music_cache_max_age_days",
        "music_ratelimit_cooldown",
        "music_idle_timeout",
    ):
        issues = validate({"token": _VALID_TOKEN, key: 0})
        assert not [
            i for i in issues if i.field == key
        ], f"unexpected issue for {key}=0"
        issues = validate({"token": _VALID_TOKEN, key: 60})
        assert not [
            i for i in issues if i.field == key
        ], f"unexpected issue for {key}=60"


def test_music_nonneg_int_negative_warns():
    issues = validate({"token": _VALID_TOKEN, "music_idle_timeout": -1})
    assert _warnings(issues, "music_idle_timeout")


def test_music_nonneg_int_non_integer_warns():
    issues = validate({"token": _VALID_TOKEN, "music_cache_max_mb": "big"})
    assert _warnings(issues, "music_cache_max_mb")


# ══════════════════════════════════════════════════════════════════════════════
#  music_cookie_file
# ══════════════════════════════════════════════════════════════════════════════


def test_music_cookie_file_missing_path_warns():
    issues = validate(
        {"token": _VALID_TOKEN, "music_cookie_file": "/nonexistent/cookies.txt"}
    )
    assert _warnings(issues, "music_cookie_file")


def test_music_cookie_file_none_no_issue():
    issues = validate({"token": _VALID_TOKEN, "music_cookie_file": None})
    assert not [i for i in issues if i.field == "music_cookie_file"]


def test_music_cookie_file_empty_no_issue():
    issues = validate({"token": _VALID_TOKEN, "music_cookie_file": ""})
    assert not [i for i in issues if i.field == "music_cookie_file"]


def test_music_cookie_file_existing_path_no_issue(tmp_path):
    cookie = tmp_path / "cookies.txt"
    cookie.write_text("# cookie data")
    issues = validate({"token": _VALID_TOKEN, "music_cookie_file": str(cookie)})
    assert not [i for i in issues if i.field == "music_cookie_file"]


# ══════════════════════════════════════════════════════════════════════════════
#  music_js_runtime_path
# ══════════════════════════════════════════════════════════════════════════════


def test_music_js_runtime_path_missing_warns():
    issues = validate(
        {"token": _VALID_TOKEN, "music_js_runtime_path": "/nonexistent/deno"}
    )
    assert _warnings(issues, "music_js_runtime_path")


def test_music_js_runtime_path_none_no_issue():
    issues = validate({"token": _VALID_TOKEN, "music_js_runtime_path": None})
    assert not [i for i in issues if i.field == "music_js_runtime_path"]


def test_music_js_runtime_path_existing_no_issue(tmp_path):
    binary = tmp_path / "deno"
    binary.write_text("#!/bin/sh")
    issues = validate({"token": _VALID_TOKEN, "music_js_runtime_path": str(binary)})
    assert not [i for i in issues if i.field == "music_js_runtime_path"]
