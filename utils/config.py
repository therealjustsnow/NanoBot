"""
utils/config.py
Config schema, INI load/save, and validation.

NanoBot stores configuration in `config.ini` (an INI file split into logical
sections) so it's easy to edit by hand and via the `!config` command.
For backwards compatibility a legacy `config.json` is auto-migrated on first
load — the old file is renamed to `config.json.bak` after migration.

Sections:
    [bot]      token, default_prefix, owner_id, error_channel_id
    [logging]  log_level, log_http, log_events_jsonl
    [votes]    top.gg / DBL / discord.bots.gg tokens, webhook port/secret,
               webhook_allowed_ips
    [groq]     groq_api_key
    [scraper]  fml_pages_per_scrape, wyr_requests_per_scrape,
               nekos_per_endpoint, nekosia_per_tag, revalidate_age,
               revalidate_batch, groq_wyr_system
    [music]    playback/queue knobs (music_* keys — see example_config.ini)

Usage:
    from utils import config
    cfg = config.load()                 # flat dict
    issues = config.validate(cfg)
    config.save(cfg)                    # write back
"""

from __future__ import annotations

import configparser
import ipaddress
import json
import os
from dataclasses import dataclass, field as dc_field
from typing import Callable, Optional

from utils.helpers import parse_duration, parse_filesize_mb

CONFIG_PATH = "config.ini"
LEGACY_JSON_PATH = "config.json"

VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
PLACEHOLDER_TOKENS = {"YOUR_BOT_TOKEN_HERE", "your_token_here", "TOKEN", ""}
VALID_SEARCH_SERVICES = ("ytsearch", "ytmsearch", "scsearch")

SECTION_ORDER = ("bot", "logging", "votes", "groq", "scraper", "music")


@dataclass
class ConfigIssue:
    field: str
    message: str
    fatal: bool  # True = bot cannot start; False = warning only

    def __str__(self) -> str:
        tag = "ERROR" if self.fatal else "WARN"
        return f"[{tag}] {self.field}: {self.message}"


# ══════════════════════════════════════════════════════════════════════════════
#  Typed schema — single source of truth
#
#  Every config key is described once by a Field. The flat lookup dicts the rest
#  of the codebase relies on (DEFAULTS, SECTION_MAP, SENSITIVE_KEYS, _SCHEMA) are
#  derived from this list, and validate()/_coerce() are driven by it — so adding
#  a key means adding one Field, with no risk of the parallel tables drifting.
# ══════════════════════════════════════════════════════════════════════════════

# Maps a Field.kind to the python type used by _coerce() / the legacy _SCHEMA.
# "id" coerces to int-or-None (Discord snowflake), so it has no single type.
_KIND_TYPE: dict[str, type | None] = {"str": str, "int": int, "bool": bool, "id": None}


@dataclass(frozen=True)
class Field:
    key: str
    section: str
    kind: str  # "str" | "int" | "bool" | "id"
    default: object
    desc: str
    required: bool = False
    sensitive: bool = False
    # Severity applied to generic type/range/choice failures for this field.
    fatal: bool = False
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    choices: Optional[tuple[str, ...]] = None
    numeric_str: bool = False  # str value validated as a float in [min, max]
    file_exists: bool = False  # str value must point at an existing file
    # Fully custom check; when set it REPLACES the generic checks above.
    validator: Optional[Callable[[object], list]] = dc_field(default=None)


# ── Per-field custom validators (irregular fields only) ───────────────────────
def _v_token(v) -> list[ConfigIssue]:
    if not isinstance(v, str) or v.strip() in PLACEHOLDER_TOKENS:
        return [
            ConfigIssue(
                "token",
                "Missing or placeholder. Get yours at "
                "discord.com/developers/applications → Bot → Token",
                True,
            )
        ]
    return []


def _v_prefix(v) -> list[ConfigIssue]:
    if v is None:
        return []  # unset → falls back to the built-in default
    if not isinstance(v, str) or not v:
        return [ConfigIssue("default_prefix", "Must be a non-empty string", True)]
    if " " in v:
        return [
            ConfigIssue(
                "default_prefix",
                f"'{v}' contains a space — prefixes can't have spaces",
                True,
            )
        ]
    if len(v) > 5:
        return [
            ConfigIssue("default_prefix", f"'{v}' is {len(v)} chars — max is 5", True)
        ]
    return []


def _v_owner_id(v) -> list[ConfigIssue]:
    if v is None:
        return []
    if not isinstance(v, int) and not str(v).isdigit():
        return [
            ConfigIssue(
                "owner_id",
                f"'{v}' is not a valid Discord user ID (must be an integer or blank)",
                True,
            )
        ]
    if int(str(v)) < 10000:
        return [
            ConfigIssue(
                "owner_id",
                f"'{v}' looks too small to be a real Discord user ID",
                False,
            )
        ]
    return []


def _v_error_channel_id(v) -> list[ConfigIssue]:
    if v is None:
        return []
    if not isinstance(v, int) and not str(v).isdigit():
        return [
            ConfigIssue(
                "error_channel_id",
                f"'{v}' is not a valid Discord channel ID (must be an integer or blank)",
                False,
            )
        ]
    return []


def _v_log_level(v) -> list[ConfigIssue]:
    if v is None:
        return []
    if not isinstance(v, str):
        return [
            ConfigIssue("log_level", f"Must be a string, got {type(v).__name__}", False)
        ]
    if v.upper() not in VALID_LOG_LEVELS:
        return [
            ConfigIssue(
                "log_level",
                f"'{v}' is not valid. Choose from: {', '.join(sorted(VALID_LOG_LEVELS))}",
                False,
            )
        ]
    return []


def _v_allowed_ips(v) -> list[ConfigIssue]:
    if not v:
        return []
    issues: list[ConfigIssue] = []
    for entry in str(v).split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            ipaddress.ip_network(entry, strict=False)
        except ValueError:
            issues.append(
                ConfigIssue(
                    "webhook_allowed_ips",
                    f"'{entry}' is not a valid IP address or CIDR range",
                    False,
                )
            )
    return issues


_WYR_SYSTEM_DEFAULT = (
    "You generate Would You Rather questions for a Discord bot. "
    "Return ONLY a JSON array of strings. Each string must start with "
    '"Would you rather" and contain exactly two options separated by " or ". '
    "End each with a question mark. Make them fun, creative, and varied -- "
    "mix silly, deep, gross, impossible, and everyday scenarios. "
    "No numbered lists, no markdown, no explanation. Just the JSON array."
)

FIELDS: tuple[Field, ...] = (
    # ── [bot] ──
    Field(
        "token",
        "bot",
        "str",
        "YOUR_BOT_TOKEN_HERE",
        "Bot token from the Discord Developer Portal",
        required=True,
        sensitive=True,
        validator=_v_token,
    ),
    Field(
        "default_prefix",
        "bot",
        "str",
        "n!",
        "Command prefix (max 5 chars, no spaces)",
        validator=_v_prefix,
    ),
    Field(
        "owner_id",
        "bot",
        "id",
        None,
        "Your Discord user ID (int or blank)",
        validator=_v_owner_id,
    ),
    Field(
        "error_channel_id",
        "bot",
        "id",
        None,
        "Channel ID to receive Python warnings and unhandled asyncio errors (int or blank)",
        validator=_v_error_channel_id,
    ),
    # ── [logging] ──
    Field(
        "log_level",
        "logging",
        "str",
        "INFO",
        "DEBUG / INFO / WARNING / ERROR / CRITICAL",
        validator=_v_log_level,
    ),
    Field("log_http", "logging", "bool", False, "Log raw HTTP requests (true/false)"),
    Field(
        "log_events_jsonl",
        "logging",
        "bool",
        True,
        "Write structured command-lifecycle events to logs/events.jsonl (true/false)",
    ),
    # ── [votes] ──
    Field(
        "topgg_v1_token",
        "votes",
        "str",
        None,
        "top.gg v1 API token for commands sync",
        sensitive=True,
    ),
    Field(
        "dbl_token",
        "votes",
        "str",
        None,
        "discordbotlist.com bot token",
        sensitive=True,
    ),
    Field(
        "discordbotsgg_token",
        "votes",
        "str",
        None,
        "discord.bots.gg bot token",
        sensitive=True,
    ),
    Field(
        "vote_webhook_port",
        "votes",
        "int",
        5000,
        "Open port for the vote webhook",
        minimum=1,
        maximum=65535,
    ),
    Field(
        "vote_webhook_secret",
        "votes",
        "str",
        None,
        "Secret used by bot lists to verify webhooks",
        sensitive=True,
    ),
    Field(
        "webhook_allowed_ips",
        "votes",
        "str",
        None,
        "Comma-separated IPs or CIDR ranges allowed to POST vote webhooks (blank = allow all)",
        validator=_v_allowed_ips,
    ),
    # ── [groq] ──
    Field(
        "groq_api_key",
        "groq",
        "str",
        None,
        "Groq API key (or set GROQ_API_KEY env var)",
        sensitive=True,
    ),
    # ── [scraper] ──
    Field(
        "fml_pages_per_scrape",
        "scraper",
        "int",
        500,
        "FML pages per daily scrape",
        minimum=0,
    ),
    Field(
        "wyr_requests_per_scrape",
        "scraper",
        "int",
        500,
        "WYR requests per rating per scrape",
        minimum=0,
    ),
    Field(
        "nekos_per_endpoint",
        "scraper",
        "int",
        400,
        "nekos.best images per endpoint per scrape",
        minimum=0,
    ),
    Field(
        "nekosia_per_tag",
        "scraper",
        "int",
        400,
        "Nekosia images per tag per scrape",
        minimum=0,
    ),
    Field(
        "revalidate_age",
        "scraper",
        "int",
        7 * 86400,
        "How long before a URL is rechecked (HEAD) (e.g. 604800, 7d, 1 week)",
        minimum=0,
    ),
    Field(
        "revalidate_batch",
        "scraper",
        "int",
        1000,
        "Max URLs checked per revalidation cycle",
        minimum=0,
    ),
    Field(
        "groq_wyr_system",
        "scraper",
        "str",
        _WYR_SYSTEM_DEFAULT,
        "System prompt for Groq WYR generation",
    ),
    # ── [music] ──
    Field(
        "music_cookie_file",
        "music",
        "str",
        None,
        "Path to a yt-dlp cookies.txt (for age/region-locked or rate-limited sources)",
        file_exists=True,
    ),
    Field(
        "music_default_volume",
        "music",
        "int",
        50,
        "Default playback volume, 0-200",
        minimum=0,
        maximum=200,
    ),
    Field(
        "music_idle_timeout",
        "music",
        "int",
        180,
        "Time idle/alone before auto-disconnect (e.g. 180, 3m, 3 minutes)",
        minimum=0,
    ),
    Field(
        "music_skip_ratio",
        "music",
        "int",
        50,
        "Percent of listeners needed to vote-skip, 0-100",
        minimum=0,
        maximum=100,
    ),
    Field(
        "music_max_queue",
        "music",
        "int",
        500,
        "Maximum tracks allowed in a queue",
        minimum=1,
    ),
    Field(
        "music_use_opus",
        "music",
        "bool",
        True,
        "Send audio as Opus (true) or PCM (false). PCM gives live volume but costs more CPU",
    ),
    Field(
        "music_persist_queue",
        "music",
        "bool",
        True,
        "Save the queue to disk so it survives a restart (true/false)",
    ),
    Field(
        "music_predownload",
        "music",
        "bool",
        True,
        "Download the next queued track while one plays for gapless playback (true/false)",
    ),
    Field(
        "music_self_deafen",
        "music",
        "bool",
        True,
        "Self-deafen when joining voice to save bandwidth (true/false)",
    ),
    Field(
        "music_default_speed",
        "music",
        "str",
        "1.0",
        "Default playback speed, 0.5-3.0",
        numeric_str=True,
        minimum=0.5,
        maximum=3.0,
    ),
    Field(
        "music_search_service",
        "music",
        "str",
        "ytsearch",
        "yt-dlp search service: ytsearch / ytmsearch / scsearch",
        choices=VALID_SEARCH_SERVICES,
    ),
    Field(
        "music_status_message",
        "music",
        "str",
        None,
        "Presence text while playing; {title} = song. YT/Twitch show a Watch button (blank = song title)",
    ),
    Field(
        "music_proxy",
        "music",
        "str",
        None,
        "HTTP/HTTPS proxy URL for yt-dlp (blank = none)",
    ),
    Field(
        "music_user_agent",
        "music",
        "str",
        None,
        "Static User-Agent header for yt-dlp (blank = default)",
    ),
    Field(
        "music_source_address",
        "music",
        "str",
        "0.0.0.0",
        "Local IP yt-dlp binds to (default 0.0.0.0)",
    ),
    Field(
        "music_autoplay_autoskip",
        "music",
        "bool",
        True,
        "Skip the current autoplay track when a user queues a real song (true/false)",
    ),
    Field(
        "music_save_videos",
        "music",
        "bool",
        False,
        "Keep downloaded audio in the cache for instant replays (true/false)",
    ),
    Field(
        "music_cache_max_mb",
        "music",
        "int",
        0,
        "Max audio cache size when music_save_videos is on (e.g. 500, 500mb, 1gb; 0 = unlimited)",
        minimum=0,
    ),
    Field(
        "music_cache_max_age_days",
        "music",
        "int",
        0,
        "Delete cached audio older than this many days (0 = never)",
        minimum=0,
    ),
    Field(
        "music_ratelimit_cooldown",
        "music",
        "int",
        600,
        "Back-off time after a YouTube rate-limit (HTTP 429) (e.g. 600, 10m, 10 minutes)",
        minimum=0,
    ),
    Field(
        "music_ratelimit_leave",
        "music",
        "bool",
        False,
        "Leave voice channels when YouTube rate-limits the bot (true/false)",
    ),
    Field(
        "music_apl_prune_on_error",
        "music",
        "bool",
        True,
        "Remove autoplaylist entries that fail to play (true/false)",
    ),
    Field(
        "music_save_history",
        "music",
        "bool",
        True,
        "Save per-server played-track history for the history command (true/false)",
    ),
    Field(
        "music_js_runtime_path",
        "music",
        "str",
        None,
        "Explicit path to deno/node/bun binary for yt-dlp JS (blank = auto-detect)",
        file_exists=True,
    ),
)

_FIELD: dict[str, Field] = {f.key: f for f in FIELDS}

# Derived lookup tables — kept for backwards compatibility with callers that
# import them directly (cogs/admin.py, the test suite). Never edit by hand.
SECTION_MAP: dict[str, str] = {f.key: f.section for f in FIELDS}
DEFAULTS: dict[str, object] = {f.key: f.default for f in FIELDS}
SENSITIVE_KEYS: set[str] = {f.key for f in FIELDS if f.sensitive}
_SCHEMA: dict[str, tuple[type | None, bool, str]] = {
    f.key: (_KIND_TYPE[f.kind], f.required, f.desc) for f in FIELDS
}


# ══════════════════════════════════════════════════════════════════════════════
#  INI load / save / migration
# ══════════════════════════════════════════════════════════════════════════════


# Int-typed keys that store durations in seconds — accept "3m", "1 hour", etc.
_DURATION_SEC_KEYS = {"music_idle_timeout", "music_ratelimit_cooldown", "revalidate_age"}

# Int-typed keys that store sizes in MB — accept "500mb", "1gb", "500", etc.
_FILESIZE_MB_KEYS = {"music_cache_max_mb"}


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
    if key in _DURATION_SEC_KEYS:
        parsed = parse_duration(raw)
        return parsed if parsed is not None else raw
    if key in _FILESIZE_MB_KEYS:
        parsed = parse_filesize_mb(raw)
        return parsed if parsed is not None else raw
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


def _range_msg(field: Field, v) -> str:
    lo, hi = field.minimum, field.maximum
    if lo is not None and hi is not None:
        return f"{v} is out of range — must be {_num(lo)}–{_num(hi)}"
    if lo is not None:
        return f"Expected integer ≥ {_num(lo)}, got '{v}'"
    return f"Expected integer ≤ {_num(hi)}, got '{v}'"


def _num(x) -> str:
    """Render a range bound without a trailing .0 for whole numbers."""
    return str(int(x)) if float(x).is_integer() else str(x)


def _check_generic(field: Field, v) -> list[ConfigIssue]:
    """Type / range / choice / file checks driven by the Field descriptor."""
    if v is None:
        if field.required:
            return [ConfigIssue(field.key, "Required but missing", True)]
        return []

    # An empty string means "unset" for string keys (mirrors _coerce), so skip
    # choice/range/file checks — a blank value just falls back to the default.
    if field.kind == "str" and v == "":
        return []

    sev = field.fatal

    if field.kind == "bool":
        if not isinstance(v, bool):
            return [ConfigIssue(field.key, f"Expected true or false, got '{v}'", sev)]
        return []

    if field.kind == "int":
        if not isinstance(v, int) or isinstance(v, bool):
            return [ConfigIssue(field.key, f"Expected integer, got '{v}'", sev)]
        if (field.minimum is not None and v < field.minimum) or (
            field.maximum is not None and v > field.maximum
        ):
            return [ConfigIssue(field.key, _range_msg(field, v), sev)]
        return []

    # str
    if field.choices is not None and v not in field.choices:
        return [
            ConfigIssue(
                field.key,
                f"'{v}' is not valid — choose from: {', '.join(field.choices)}",
                sev,
            )
        ]
    if field.numeric_str:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return [
                ConfigIssue(
                    field.key,
                    f"'{v}' is not a valid number — must be "
                    f"{_num(field.minimum)}–{_num(field.maximum)}",
                    sev,
                )
            ]
        if (field.minimum is not None and f < field.minimum) or (
            field.maximum is not None and f > field.maximum
        ):
            return [
                ConfigIssue(
                    field.key,
                    f"{v} is out of range — must be "
                    f"{_num(field.minimum)}–{_num(field.maximum)}",
                    sev,
                )
            ]
    if field.file_exists and not os.path.isfile(v):
        return [ConfigIssue(field.key, f"File not found: '{v}'", sev)]
    return []


def validate(cfg: dict) -> list[ConfigIssue]:
    """
    Validate a flat config dict against the NanoBot schema.
    Returns a list of ConfigIssue objects (empty = all good).
    """
    issues: list[ConfigIssue] = []

    # Unknown keys → typo warning.
    for key in cfg:
        if key not in _FIELD:
            issues.append(
                ConfigIssue(key, f"Unrecognised key '{key}' — check for typos", False)
            )

    # Every known field: a custom validator (if any) fully owns its checks,
    # otherwise the generic type/range/choice/file checks apply.
    for field in FIELDS:
        v = cfg.get(field.key)
        if field.validator is not None:
            issues.extend(field.validator(v))
        else:
            issues.extend(_check_generic(field, v))

    return issues


def assert_no_fatal(cfg: dict) -> None:
    """Raise ValueError if config has any fatal issues. Used at bot startup."""
    issues = validate(cfg)
    fatal = [i for i in issues if i.fatal]
    if fatal:
        msg = "Config errors prevent startup:\n" + "\n".join(f"  • {i}" for i in fatal)
        raise ValueError(msg)
