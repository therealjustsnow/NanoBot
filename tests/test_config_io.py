"""
Tests for utils/config.py — load, save, migrate, set_value, _coerce, _format,
assert_no_fatal, and example_ini.
"""

import json
import os

import pytest

from utils.config import (
    ConfigIssue,
    _coerce,
    _format,
    assert_no_fatal,
    example_ini,
    load,
    migrate_from_json,
    save,
    set_value,
)

# ── load ──────────────────────────────────────────────────────────────────────


def test_load_missing_file_returns_empty(tmp_path):
    result = load(str(tmp_path / "missing.ini"))
    assert result == {}


def test_load_reads_token(tmp_path):
    ini = tmp_path / "config.ini"
    ini.write_text("[bot]\ntoken = mytoken123\n", encoding="utf-8")
    cfg = load(str(ini))
    assert cfg["token"] == "mytoken123"


def test_load_coerces_bool_true(tmp_path):
    ini = tmp_path / "config.ini"
    ini.write_text("[logging]\nlog_http = true\n", encoding="utf-8")
    cfg = load(str(ini))
    assert cfg["log_http"] is True


def test_load_coerces_bool_false(tmp_path):
    ini = tmp_path / "config.ini"
    ini.write_text("[logging]\nlog_http = false\n", encoding="utf-8")
    cfg = load(str(ini))
    assert cfg["log_http"] is False


def test_load_coerces_int(tmp_path):
    ini = tmp_path / "config.ini"
    ini.write_text("[votes]\nvote_webhook_port = 8080\n", encoding="utf-8")
    cfg = load(str(ini))
    assert cfg["vote_webhook_port"] == 8080


def test_load_coerces_owner_id_to_int(tmp_path):
    ini = tmp_path / "config.ini"
    ini.write_text("[bot]\ntoken = t\nowner_id = 123456789\n", encoding="utf-8")
    cfg = load(str(ini))
    assert cfg["owner_id"] == 123456789


def test_load_empty_owner_id_is_none(tmp_path):
    ini = tmp_path / "config.ini"
    ini.write_text("[bot]\ntoken = t\nowner_id =\n", encoding="utf-8")
    cfg = load(str(ini))
    assert cfg.get("owner_id") is None


def test_load_triggers_json_migration(tmp_path, monkeypatch):
    import utils.config as config_module

    ini_path = str(tmp_path / "config.ini")
    json_path = str(tmp_path / "config.json")
    (tmp_path / "config.json").write_text(
        json.dumps({"token": "tok_migrated"}), encoding="utf-8"
    )

    monkeypatch.setattr(config_module, "LEGACY_JSON_PATH", json_path)

    cfg = load(ini_path)
    assert cfg.get("token") == "tok_migrated"
    assert os.path.exists(ini_path)


# ── save ──────────────────────────────────────────────────────────────────────


def test_save_creates_ini_file(tmp_path):
    p = tmp_path / "out.ini"
    save({"token": "mytoken"}, str(p))
    assert p.exists()


def test_save_round_trip(tmp_path):
    p = tmp_path / "rt.ini"
    cfg = {
        "token": "abc",
        "default_prefix": "n!",
        "vote_webhook_port": 5001,
        "log_http": True,
    }
    save(cfg, str(p))
    loaded = load(str(p))
    assert loaded["token"] == "abc"
    assert loaded["default_prefix"] == "n!"
    assert loaded["vote_webhook_port"] == 5001
    assert loaded["log_http"] is True


def test_save_none_values_round_trip(tmp_path):
    p = tmp_path / "none.ini"
    save({"token": "t", "owner_id": None}, str(p))
    cfg = load(str(p))
    assert cfg.get("owner_id") is None


def test_save_drops_empty_sections(tmp_path):
    p = tmp_path / "sec.ini"
    # Only bot key — scraper section should be omitted
    save({"token": "t"}, str(p))
    content = p.read_text()
    assert "[scraper]" not in content


def test_save_section_order(tmp_path):
    p = tmp_path / "order.ini"
    save({"token": "t", "log_level": "DEBUG", "vote_webhook_port": 5000}, str(p))
    content = p.read_text()
    bot_pos = content.index("[bot]")
    logging_pos = content.index("[logging]")
    votes_pos = content.index("[votes]")
    assert bot_pos < logging_pos < votes_pos


# ── migrate_from_json ─────────────────────────────────────────────────────────


def test_migrate_from_json_creates_ini_and_renames(tmp_path):
    json_file = tmp_path / "config.json"
    ini_file = tmp_path / "config.ini"
    json_file.write_text(json.dumps({"token": "migrated_tok"}), encoding="utf-8")

    result = migrate_from_json(str(json_file), str(ini_file))

    assert result is True
    assert ini_file.exists()
    assert (tmp_path / "config.json.bak").exists()
    assert not json_file.exists()


def test_migrate_from_json_content_preserved(tmp_path):
    json_file = tmp_path / "config.json"
    ini_file = tmp_path / "config.ini"
    json_file.write_text(
        json.dumps({"token": "my_tok", "default_prefix": ">>"}), encoding="utf-8"
    )

    migrate_from_json(str(json_file), str(ini_file))
    cfg = load(str(ini_file))
    assert cfg["token"] == "my_tok"
    assert cfg["default_prefix"] == ">>"


def test_migrate_from_json_invalid_json_returns_false(tmp_path):
    json_file = tmp_path / "bad.json"
    ini_file = tmp_path / "bad.ini"
    json_file.write_text("{broken", encoding="utf-8")
    assert migrate_from_json(str(json_file), str(ini_file)) is False
    assert not ini_file.exists()


def test_migrate_from_json_missing_file_returns_false(tmp_path):
    result = migrate_from_json(str(tmp_path / "nope.json"), str(tmp_path / "nope.ini"))
    assert result is False


def test_migrate_from_json_non_dict_returns_false(tmp_path):
    json_file = tmp_path / "list.json"
    ini_file = tmp_path / "list.ini"
    json_file.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert migrate_from_json(str(json_file), str(ini_file)) is False


# ── set_value ─────────────────────────────────────────────────────────────────


def test_set_value_updates_key(tmp_path):
    p = tmp_path / "sv.ini"
    save({"token": "old_tok", "log_level": "INFO"}, str(p))
    set_value("log_level", "DEBUG", str(p))
    assert load(str(p))["log_level"] == "DEBUG"


def test_set_value_adds_new_key(tmp_path):
    p = tmp_path / "sv2.ini"
    save({"token": "t"}, str(p))
    set_value("owner_id", 999999999, str(p))
    assert load(str(p))["owner_id"] == 999999999


# ── _coerce ───────────────────────────────────────────────────────────────────


def test_coerce_bool_variants():
    for val in ("true", "1", "yes", "on", "True", "YES"):
        assert _coerce("log_http", val) is True
    for val in ("false", "0", "no", "off", "False"):
        assert _coerce("log_http", val) is False


def test_coerce_int():
    assert _coerce("vote_webhook_port", "8080") == 8080


def test_coerce_int_invalid_returns_raw():
    result = _coerce("vote_webhook_port", "notanint")
    assert result == "notanint"


def test_coerce_owner_id_digit_string():
    assert _coerce("owner_id", "123456") == 123456


def test_coerce_owner_id_non_digit_returns_none():
    assert _coerce("owner_id", "notanid") is None


def test_coerce_empty_string_returns_none():
    assert _coerce("token", "") is None


def test_coerce_none_returns_none():
    assert _coerce("token", None) is None


def test_coerce_default_prefix_empty_string_kept():
    # default_prefix is the one exception — empty stays as empty string, not None
    result = _coerce("default_prefix", "")
    assert result == ""


# ── _format ───────────────────────────────────────────────────────────────────


def test_format_none():
    assert _format(None) == ""


def test_format_bool_true():
    assert _format(True) == "true"


def test_format_bool_false():
    assert _format(False) == "false"


def test_format_int():
    assert _format(42) == "42"


def test_format_str():
    assert _format("hello") == "hello"


# ── assert_no_fatal ───────────────────────────────────────────────────────────


def test_assert_no_fatal_passes_valid_config():
    assert_no_fatal({"token": "valid_token_here"})


def test_assert_no_fatal_raises_on_missing_token():
    with pytest.raises(ValueError, match="Config errors"):
        assert_no_fatal({})


def test_assert_no_fatal_raises_on_bad_prefix():
    with pytest.raises(ValueError):
        assert_no_fatal({"token": "valid", "default_prefix": "toolong!!"})


# ── example_ini ───────────────────────────────────────────────────────────────


def test_example_ini_contains_required_sections():
    content = example_ini()
    assert "[bot]" in content
    assert "[logging]" in content
    assert "[votes]" in content


def test_example_ini_contains_token_key():
    content = example_ini()
    assert "token" in content


def test_example_ini_ends_with_newline():
    assert example_ini().endswith("\n")
