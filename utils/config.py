"""
utils/config.py
Config schema, INI load/save, and validation.

NanoBot stores configuration in `config.ini` (an INI file split into logical
sections) so it's easy to edit by hand and via the `!config` command.
For backwards compatibility a legacy `config.json` is auto-migrated on first
load — the old file is renamed to `config.json.bak` after migration.

Sections:
    [bot]      token, default_prefix, owner_id
    [logging]  log_level, log_http
    [votes]    top.gg / DBL / discord.bots.gg tokens, webhook port/secret
    [groq]     groq_api_key
    [scraper]  fml_pages_per_scrape, wyr_requests_per_scrape,
               nekos_per_endpoint, nekosia_per_tag, revalidate_age,
               revalidate_batch, groq_wyr_system

Usage:
    from utils import config
    cfg = config.load()                 # flat dict
    issues = config.validate(cfg)
    config.save(cfg)                    # write back
"""

from __future__ import annotations

import configparser
import json
import os
from dataclasses import dataclass

CONFIG_PATH = "config.ini"
LEGACY_JSON_PATH = "config.json"

VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
PLACEHOLDER_TOKENS = {"YOUR_BOT_TOKEN_HERE", "your_token_here", "TOKEN", ""}

# Which INI section each key belongs to.
SECTION_MAP = {
    # [bot]
    "token": "bot",
    "default_prefix": "bot",
    "owner_id": "bot",
    # [logging]
    "log_level": "logging",
    "log_http": "logging",
    # [votes]
    "topgg_token": "votes",
    "topgg_v1_token": "votes",
    "dbl_token": "votes",
    "discordbotsgg_token": "votes",
    "vote_webhook_port": "votes",
    "vote_webhook_secret": "votes",
    # [groq]
    "groq_api_key": "groq",
    # [scraper]
    "fml_pages_per_scrape": "scraper",
    "wyr_requests_per_scrape": "scraper",
    "nekos_per_endpoint": "scraper",
    "nekosia_per_tag": "scraper",
    "revalidate_age": "scraper",
    "revalidate_batch": "scraper",
    "groq_wyr_system": "scraper",
}

SECTION_ORDER = ("bot", "logging", "votes", "groq", "scraper")

# Schema: key -> (type, required, description)
_SCHEMA: dict[str, tuple[type | None, bool, str]] = {
    "token": (str, True, "Bot token from the Discord Developer Portal"),
    "default_prefix": (str, False, "Command prefix (max 5 chars, no spaces)"),
    "owner_id": (None, False, "Your Discord user ID (int or blank)"),
    "log_level": (str, False, "DEBUG / INFO / WARNING / ERROR / CRITICAL"),
    "log_http": (bool, False, "Log raw HTTP requests (true/false)"),
    "topgg_token": (str, False, "top.gg AUTH token"),
    "topgg_v1_token": (str, False, "top.gg v1 API token for commands sync"),
    "dbl_token": (str, False, "discordbotlist.com bot token"),
    "discordbotsgg_token": (str, False, "discord.bots.gg bot token"),
    "vote_webhook_port": (int, False, "Open port for the vote webhook"),
    "vote_webhook_secret": (str, False, "Secret used by bot lists to verify webhooks"),
    "groq_api_key": (str, False, "Groq API key (or set GROQ_API_KEY env var)"),
    # ── scraper ──
    "fml_pages_per_scrape": (int, False, "FML pages per daily scrape"),
    "wyr_requests_per_scrape": (int, False, "WYR requests per rating per scrape"),
    "nekos_per_endpoint": (int, False, "nekos.best images per endpoint per scrape"),
    "nekosia_per_tag": (int, False, "Nekosia images per tag per scrape"),
    "revalidate_age": (int, False, "Seconds before a URL is rechecked (HEAD)"),
    "revalidate_batch": (int, False, "Max URLs checked per revalidation cycle"),
    "groq_wyr_system": (str, False, "System prompt for Groq WYR generation"),
}

# Defaults used when writing a fresh example_config.ini and when keys are missing.
DEFAULTS: dict[str, object] = {
    "token": "YOUR_BOT_TOKEN_HERE",
    "default_prefix": "n!",
    "owner_id": None,
    "log_level": "INFO",
    "log_http": False,
    "topgg_token": None,
    "topgg_v1_token": None,
    "dbl_token": None,
    "discordbotsgg_token": None,
    "vote_webhook_port": 5000,
    "vote_webhook_secret": None,
    "groq_api_key": None,
    "fml_pages_per_scrape": 500,
    "wyr_requests_per_scrape": 500,
    "nekos_per_endpoint": 400,
    "nekosia_per_tag": 400,
    "revalidate_age": 7 * 86400,
    "revalidate_batch": 1000,
    "groq_wyr_system": (
        "You generate Would You Rather questions for a Discord bot. "
        "Return ONLY a JSON array of strings. Each string must start with "
        '"Would you rather" and contain exactly two options separated by " or ". '
        "End each with a question mark. Make them fun, creative, and varied -- "
        "mix silly, deep, gross, impossible, and everyday scenarios. "
        "No numbered lists, no markdown, no explanation. Just the JSON array."
    ),
}

# Keys that must never be echoed back in Discord (logs, !config show, etc).
SENSITIVE_KEYS = {
    "token",
    "topgg_token",
    "topgg_v1_token",
    "dbl_token",
    "discordbotsgg_token",
    "vote_webhook_secret",
    "groq_api_key",
}


@dataclass
class ConfigIssue:
    field: str
    message: str
    fatal: bool  # True = bot cannot start; False = warning only

    def __str__(self) -> str:
        tag = "ERROR" if self.fatal else "WARN"
        return f"[{tag}] {self.field}: {self.message}"


# ══════════════════════════════════════════════════════════════════════════════
#  INI load / save / migration
# ══════════════════════════════════════════════════════════════════════════════


def _coerce(key: str, raw: str):
    """Convert an INI string value to the type declared in the schema."""
    if raw is None:
        return None
    raw = raw.strip()

    typ = _SCHEMA.get(key, (str, False, ""))[0]

    # Empty string = "unset" for every key except default_prefix (which needs a
    # non-empty string but has its own default fallback via cfg.get).
    if raw == "" and key != "default_prefix":
        return None

    if typ is bool:
        return raw.lower() in ("true", "1", "yes", "on")
    if typ is int:
        try:
            return int(raw)
        except ValueError:
            return raw  # validate() will flag it
    if typ is None:  # owner_id — int or null
        if raw.isdigit():
            return int(raw)
        return None
    return raw


def _format(val) -> str:
    """Turn a python value into its INI-string representation."""
    if val is None:
        return ""
    if isinstance(val, bool):
        return "true" if val else "false"
    return str(val)


def load(path: str = CONFIG_PATH) -> dict:
    """
    Load config.ini into a flat dict. Auto-migrates config.json when the
    INI file is missing but a legacy JSON file exists.
    """
    if not os.path.exists(path) and os.path.exists(LEGACY_JSON_PATH):
        migrate_from_json(LEGACY_JSON_PATH, path)

    if not os.path.exists(path):
        return {}

    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")

    flat: dict = {}
    for section in parser.sections():
        for key in parser[section]:
            flat[key] = _coerce(key, parser[section][key])
    return flat


def save(cfg: dict, path: str = CONFIG_PATH) -> None:
    """
    Write a flat dict back to an INI file, routing each key to the right
    section. Preserves canonical section order. Creates the file if missing.
    """
    parser = configparser.ConfigParser(interpolation=None)

    # Create sections in canonical order so output is always consistent.
    for sec in SECTION_ORDER:
        parser.add_section(sec)

    for key, val in cfg.items():
        section = SECTION_MAP.get(key, "bot")
        if not parser.has_section(section):
            parser.add_section(section)
        parser[section][key] = _format(val)

    # Drop empty sections (happens if the config has no scraper keys yet).
    for sec in list(parser.sections()):
        if not parser.options(sec):
            parser.remove_section(sec)

    with open(path, "w", encoding="utf-8") as f:
        parser.write(f)


def migrate_from_json(json_path: str, ini_path: str) -> bool:
    """
    Read an old config.json, write config.ini, and rename the JSON file to
    `.bak` so the migration doesn't re-trigger on the next start.
    Returns True if a migration was performed.
    """
    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False

    if not isinstance(data, dict):
        return False

    save(data, ini_path)

    bak = json_path + ".bak"
    try:
        if os.path.exists(bak):
            os.remove(bak)
        os.rename(json_path, bak)
    except OSError:
        # Leaving the old file in place is harmless — load() won't re-migrate
        # because config.ini now exists.
        pass
    return True


def set_value(key: str, value, path: str = CONFIG_PATH) -> None:
    """Update a single key and persist the change to disk."""
    cfg = load(path)
    cfg[key] = value
    save(cfg, path)


def example_ini() -> str:
    """
    Return the content of a fresh example_config.ini — used by `example_config.ini`
    committed to the repo and by the test suite.
    """
    lines: list[str] = [
        "; NanoBot configuration.",
        "; Copy this file to config.ini and set at least `token`.",
        "; Keys left blank (or missing) use their built-in default.",
        "",
    ]
    per_section: dict[str, list[str]] = {s: [] for s in SECTION_ORDER}
    for key, default in DEFAULTS.items():
        sec = SECTION_MAP.get(key, "bot")
        desc = _SCHEMA.get(key, (None, False, ""))[2]
        if desc:
            per_section[sec].append(f"; {desc}")
        per_section[sec].append(f"{key} = {_format(default)}")
        per_section[sec].append("")

    for sec in SECTION_ORDER:
        if not per_section[sec]:
            continue
        lines.append(f"[{sec}]")
        lines.extend(per_section[sec])

    return "\n".join(lines).rstrip() + "\n"


# ══════════════════════════════════════════════════════════════════════════════
#  Validation
# ══════════════════════════════════════════════════════════════════════════════


def validate(cfg: dict) -> list[ConfigIssue]:
    """
    Validate a flat config dict against the NanoBot schema.
    Returns a list of ConfigIssue objects (empty = all good).
    """
    issues: list[ConfigIssue] = []

    # ── Unknown keys ──────────────────────────────────────────────────────────
    for key in cfg:
        if key not in _SCHEMA:
            issues.append(
                ConfigIssue(
                    field=key,
                    message=f"Unrecognised key '{key}' — check for typos",
                    fatal=False,
                )
            )

    # ── Token ─────────────────────────────────────────────────────────────────
    token = cfg.get("token", "")
    if not isinstance(token, str) or token.strip() in PLACEHOLDER_TOKENS:
        issues.append(
            ConfigIssue(
                field="token",
                message="Missing or placeholder. Get yours at discord.com/developers/applications → Bot → Token",
                fatal=True,
            )
        )

    # ── default_prefix ────────────────────────────────────────────────────────
    prefix = cfg.get("default_prefix", "!")
    if not isinstance(prefix, str) or not prefix:
        issues.append(ConfigIssue("default_prefix", "Must be a non-empty string", True))
    elif " " in prefix:
        issues.append(
            ConfigIssue(
                "default_prefix",
                f"'{prefix}' contains a space — prefixes can't have spaces",
                True,
            )
        )
    elif len(prefix) > 5:
        issues.append(
            ConfigIssue(
                "default_prefix", f"'{prefix}' is {len(prefix)} chars — max is 5", True
            )
        )

    # ── log_level ─────────────────────────────────────────────────────────────
    raw_level = cfg.get("log_level", "INFO")
    if raw_level is not None:
        if not isinstance(raw_level, str):
            issues.append(
                ConfigIssue(
                    "log_level",
                    f"Must be a string, got {type(raw_level).__name__}",
                    False,
                )
            )
        elif raw_level.upper() not in VALID_LOG_LEVELS:
            issues.append(
                ConfigIssue(
                    field="log_level",
                    message=f"'{raw_level}' is not valid. Choose from: {', '.join(sorted(VALID_LOG_LEVELS))}",
                    fatal=False,
                )
            )

    # ── log_http ──────────────────────────────────────────────────────────────
    log_http = cfg.get("log_http", False)
    if log_http is not None and not isinstance(log_http, bool):
        issues.append(
            ConfigIssue(
                field="log_http",
                message=f"Expected true or false, got {type(log_http).__name__} '{log_http}'",
                fatal=False,
            )
        )

    # ── owner_id ──────────────────────────────────────────────────────────────
    owner_id = cfg.get("owner_id")
    if owner_id is not None:
        if not isinstance(owner_id, int) and not str(owner_id).isdigit():
            issues.append(
                ConfigIssue(
                    field="owner_id",
                    message=f"'{owner_id}' is not a valid Discord user ID (must be an integer or blank)",
                    fatal=True,
                )
            )
        elif int(str(owner_id)) < 10000:
            issues.append(
                ConfigIssue(
                    field="owner_id",
                    message=f"'{owner_id}' looks too small to be a real Discord user ID",
                    fatal=False,
                )
            )

    # ── vote_webhook_port ─────────────────────────────────────────────────────
    port = cfg.get("vote_webhook_port")
    if port is not None:
        if not isinstance(port, int) or isinstance(port, bool):
            issues.append(
                ConfigIssue(
                    field="vote_webhook_port",
                    message=f"Must be an integer, got {type(port).__name__} '{port}'",
                    fatal=False,
                )
            )
        elif not (1 <= port <= 65535):
            issues.append(
                ConfigIssue(
                    field="vote_webhook_port",
                    message=f"{port} is not a valid port number (must be 1–65535)",
                    fatal=False,
                )
            )

    # ── scraper integer knobs ─────────────────────────────────────────────────
    for key in (
        "fml_pages_per_scrape",
        "wyr_requests_per_scrape",
        "nekos_per_endpoint",
        "nekosia_per_tag",
        "revalidate_age",
        "revalidate_batch",
    ):
        v = cfg.get(key)
        if v is None:
            continue
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            issues.append(
                ConfigIssue(
                    field=key,
                    message=f"Expected non-negative integer, got '{v}'",
                    fatal=False,
                )
            )

    return issues


def assert_no_fatal(cfg: dict) -> None:
    """Raise ValueError if config has any fatal issues. Used at bot startup."""
    issues = validate(cfg)
    fatal = [i for i in issues if i.fatal]
    if fatal:
        msg = "Config errors prevent startup:\n" + "\n".join(f"  • {i}" for i in fatal)
        raise ValueError(msg)
