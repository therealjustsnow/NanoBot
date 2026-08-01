"""
utils/config.py
Config schema, INI load/save, and validation.

NanoBot stores configuration in `config.ini` (an INI file split into logical
sections) so it's easy to edit by hand and via the `!config` command.
For backwards compatibility a legacy `config.json` is auto-migrated on first
load — the old file is renamed to `config.json.bak` after migration.

Sections:
    [bot]      token, default_prefix, owner_id, error_channel_id,
               idle_status_message, health_check_port, health_check_host
    [logging]  log_level, log_http, log_events_jsonl, db_slow_query_ms
    [votes]    top.gg / DBL / discord.bots.gg tokens, webhook port/host/secret
    [groq]     groq_api_key
    [scraper]  fml_pages_per_scrape, wyr_requests_per_scrape,
               nekos_per_endpoint, nekosia_per_tag, revalidate_age,
               revalidate_batch, groq_wyr_system
    [music]    playback/queue knobs (music_* keys — see example_config.ini)
    [dashboard] web dashboard port/host/base URL, OAuth client id + secret,
               session secret/lifetime, and whether the economy is playable
               from the browser

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
from dataclasses import dataclass, field as dc_field
from typing import Callable, Optional

from utils.helpers import parse_duration, parse_filesize_mb

CONFIG_PATH = "config.ini"
LEGACY_JSON_PATH = "config.json"

VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
PLACEHOLDER_TOKENS = {"YOUR_BOT_TOKEN_HERE", "your_token_here", "TOKEN", ""}
VALID_SEARCH_SERVICES = ("ytsearch", "ytmsearch", "scsearch")

SECTION_ORDER = (
    "bot",
    "logging",
    "votes",
    "groq",
    "scraper",
    "music",
    "dashboard",
)


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


def _v_db_encryption_key(v) -> list[ConfigIssue]:
    if not v:
        return []  # blank = encryption off
    try:
        import sqlcipher3  # noqa: F401
    except ImportError:
        return [
            ConfigIssue(
                "db_encryption_key",
                "db_encryption_key is set but the sqlcipher3 driver is not "
                "installed. Install it with: pip install sqlcipher3-binary",
                True,
            )
        ]
    if len(str(v)) < 12:
        return [
            ConfigIssue(
                "db_encryption_key",
                f"Passphrase is only {len(str(v))} chars — use at least 12 "
                "for a meaningful level of protection",
                False,
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


def _v_dashboard_base_url(v) -> list[ConfigIssue]:
    if not v:
        return []  # blank is fine while the dashboard is off
    if not isinstance(v, str):
        return [
            ConfigIssue(
                "dashboard_base_url",
                f"Must be a string, got {type(v).__name__}",
                False,
            )
        ]
    url = v.strip()
    if not url.startswith(("http://", "https://")):
        return [
            ConfigIssue(
                "dashboard_base_url",
                f"'{v}' must start with http:// or https://",
                False,
            )
        ]
    if url.rstrip("/").count("/") > 2:
        return [
            ConfigIssue(
                "dashboard_base_url",
                "Should be an origin (scheme + host [+ port]) with no path, "
                f"e.g. https://nano.example.com — got '{v}'",
                False,
            )
        ]
    if url.startswith("http://") and not url.startswith(
        ("http://localhost", "http://127.0.0.1")
    ):
        return [
            ConfigIssue(
                "dashboard_base_url",
                "Plain http:// sends the session cookie in the clear. Use https:// "
                "(or put the dashboard behind a TLS-terminating reverse proxy)",
                False,
            )
        ]
    return []


def _v_dashboard_frontend_url(v) -> list[ConfigIssue]:
    """The browser app's own URL, when something other than the bot serves it.

    Unlike `dashboard_base_url` this one *may* carry a path: a GitHub Pages
    project site lives at `https://you.github.io/NanoBot`, and refusing the
    path would refuse the deployment it exists for. What it may not do is carry
    a query or a fragment, which would mean the redirect back from a login
    silently dropped part of itself.
    """
    if not v:
        return []
    url = str(v).strip()
    if not url.startswith(("http://", "https://")):
        return [
            ConfigIssue(
                "dashboard_frontend_url",
                f"'{v}' must start with http:// or https://",
                False,
            )
        ]
    if "?" in url or "#" in url:
        return [
            ConfigIssue(
                "dashboard_frontend_url",
                "Should be a URL with no query or fragment, e.g. "
                f"https://you.github.io/NanoBot — got '{v}'",
                False,
            )
        ]
    return []


def _v_dashboard_origins(v) -> list[ConfigIssue]:
    if not v:
        return []
    issues = []
    for part in str(v).replace(",", " ").split():
        entry = part.strip().rstrip("/")
        if not entry.startswith(("http://", "https://")):
            issues.append(
                ConfigIssue(
                    "dashboard_allowed_origins",
                    f"'{part}' needs a scheme — origins look like "
                    "https://you.github.io",
                    False,
                )
            )
        elif entry.count("/") > 2:
            issues.append(
                ConfigIssue(
                    "dashboard_allowed_origins",
                    f"'{part}' has a path. An origin is scheme + host only",
                    False,
                )
            )
        elif entry.startswith("http://") and not entry.startswith(
            ("http://localhost", "http://127.0.0.1")
        ):
            issues.append(
                ConfigIssue(
                    "dashboard_allowed_origins",
                    f"'{part}' is plain http. A cross-origin session cookie has "
                    "to be Secure, so browsers will drop it — use https",
                    False,
                )
            )
    return issues


def _v_dashboard_session_secret(v) -> list[ConfigIssue]:
    if not v:
        return []  # blank = ephemeral random secret, warned about at startup
    if len(str(v)) < 32:
        return [
            ConfigIssue(
                "dashboard_session_secret",
                f"Only {len(str(v))} chars — use at least 32. Generate one with: "
                'python -c "import secrets; print(secrets.token_urlsafe(48))"',
                False,
            )
        ]
    return []


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
        "db_encryption_key",
        "bot",
        "str",
        None,
        "Passphrase that encrypts the SQLite databases at rest via SQLCipher "
        "(blank = no encryption; needs `pip install sqlcipher3-binary`; the "
        "NANOBOT_DB_KEY env var overrides this; losing the key loses the data)",
        sensitive=True,
        validator=_v_db_encryption_key,
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
    Field(
        "idle_status_message",
        "bot",
        "str",
        None,
        "Manual idle presence text (blank = auto-rotate 'Listening to /help | /<command>')",
    ),
    Field(
        "health_check_port",
        "bot",
        "int",
        0,
        "Port for a plain-HTTP health-check endpoint (GET /health). 0 = disabled",
        minimum=0,
        maximum=65535,
    ),
    Field(
        "health_check_host",
        "bot",
        "str",
        "0.0.0.0",
        "Bind address for the health endpoint (0.0.0.0 = all interfaces; "
        "127.0.0.1 = host-local only — the payload is unauthenticated)",
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
    Field(
        "db_slow_query_ms",
        "logging",
        "int",
        0,
        "Log any SQLite query slower than this many ms (0 = disabled)",
        minimum=0,
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
        "vote_webhook_host",
        "votes",
        "str",
        "0.0.0.0",
        "Bind address for the vote webhook (0.0.0.0 = all interfaces; "
        "127.0.0.1 = host-local, e.g. behind a reverse proxy)",
    ),
    Field(
        "vote_webhook_secret",
        "votes",
        "str",
        None,
        "Secret used by bot lists to verify webhooks",
        sensitive=True,
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
        "Seconds before disconnect when channel empties (pauses immediately; resumes if someone returns) or queue goes idle (e.g. 180, 3m)",
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
        "music_request_throttle",
        "music",
        "str",
        "0",
        "Seconds between yt-dlp HTTP requests; paces the bot so a proxy IP flags slower (0 = off, try 1)",
        numeric_str=True,
        minimum=0,
    ),
    Field(
        "music_player_client",
        "music",
        "str",
        None,
        "Force a yt-dlp YouTube player client, e.g. web_safari / mweb / android_vr "
        "(blank = yt-dlp picks; set one if playback 403s on m3u8/HLS)",
    ),
    Field(
        "music_pot_provider_url",
        "music",
        "str",
        None,
        "Base URL of a running bgutil PO-token provider (e.g. http://127.0.0.1:4416). "
        "Fixes GVS 'format not available'/403. Blank = default 127.0.0.1:4416 if the "
        "provider runs there; set only for a non-default host/port",
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
        "music_metadata_lookup",
        "music",
        "bool",
        True,
        "Fill missing artist via the free iTunes Search API (true/false)",
    ),
    Field(
        "music_js_runtime_path",
        "music",
        "str",
        None,
        "Explicit path to deno/node/bun binary for yt-dlp JS (blank = auto-detect)",
        file_exists=True,
    ),
    Field(
        "music_sponsorblock",
        "music",
        "bool",
        False,
        "Skip non-music/sponsor segments via SponsorBlock (downloads + FFmpeg-cuts before play; true/false)",
    ),
    Field(
        "music_sponsorblock_categories",
        "music",
        "str",
        "music_offtopic",
        "Comma/space-separated SponsorBlock categories to remove (sponsor intro outro selfpromo preview filler interaction music_offtopic poi_highlight)",
    ),
    # ── [dashboard] ──
    Field(
        "dashboard_port",
        "dashboard",
        "int",
        0,
        "Port for the web dashboard. 0 = disabled (rides the shared HTTP server, "
        "so it can share an allocation with /health and the vote webhook)",
        minimum=0,
        maximum=65535,
    ),
    Field(
        "dashboard_host",
        "dashboard",
        "str",
        "0.0.0.0",
        "Bind address for the dashboard (0.0.0.0 = all interfaces; 127.0.0.1 = "
        "host-local only, for running behind a reverse proxy)",
    ),
    Field(
        "dashboard_base_url",
        "dashboard",
        "str",
        None,
        "Public URL this API is reached at, e.g. https://nano.example.com. "
        "Used to build the OAuth redirect URI — must match the one registered in "
        "the Discord developer portal",
        validator=_v_dashboard_base_url,
    ),
    Field(
        "dashboard_frontend_url",
        "dashboard",
        "str",
        None,
        "Where the browser app is served from, if not by this bot — e.g. "
        "https://you.github.io/NanoBot. Blank (the default) means the bot serves "
        "it and the two are the same URL",
        validator=_v_dashboard_frontend_url,
    ),
    Field(
        "dashboard_client_id",
        "dashboard",
        "id",
        None,
        "Discord application (client) ID for dashboard OAuth. Blank = the bot's own",
    ),
    Field(
        "dashboard_client_secret",
        "dashboard",
        "str",
        None,
        "Discord OAuth2 client secret (developer portal → OAuth2 → Client Secret)",
        sensitive=True,
    ),
    Field(
        "dashboard_session_secret",
        "dashboard",
        "str",
        None,
        "Secret used to sign session cookies. Blank = a random one is generated "
        "at startup, which logs everyone out on every restart",
        sensitive=True,
        validator=_v_dashboard_session_secret,
    ),
    Field(
        "dashboard_session_days",
        "dashboard",
        "int",
        7,
        "How long a dashboard login lasts before it must be renewed, in days",
        minimum=1,
        maximum=90,
    ),
    Field(
        "dashboard_allowed_origins",
        "dashboard",
        "str",
        None,
        "Extra origins allowed to call the API from another host, space- or "
        "comma-separated (e.g. https://you.github.io). Only needed when the "
        "frontend is hosted separately — leave blank when the bot serves it. "
        "Setting this switches the session cookie to SameSite=None; Secure, so "
        "every origin here must be HTTPS",
        validator=_v_dashboard_origins,
    ),
    Field(
        "dashboard_play_enabled",
        "dashboard",
        "bool",
        True,
        "Allow playing the economy (fishing/mining/adventure) from the web. "
        "false = the dashboard is read-only for members and still fully "
        "configurable for admins",
    ),
)

_FIELD: dict[str, Field] = {f.key: f for f in FIELDS}

# Derived lookup tables — kept for backwards compatibility with callers that
# import them directly (cogs/admin/, the test suite). Never edit by hand.
SECTION_MAP: dict[str, str] = {f.key: f.section for f in FIELDS}
DEFAULTS: dict[str, object] = {f.key: f.default for f in FIELDS}
SENSITIVE_KEYS: set[str] = {f.key for f in FIELDS if f.sensitive}
_SCHEMA: dict[str, tuple[type | None, bool, str]] = {
    f.key: (_KIND_TYPE[f.kind], f.required, f.desc) for f in FIELDS
}


# ══════════════════════════════════════════════════════════════════════════════
#  Display helpers (shared by the !config command and the startup summary)
# ══════════════════════════════════════════════════════════════════════════════
def mask_value(key: str, val, mask: bool = True) -> str:
    """Render a config value as a plain string, optionally masking secrets.

    Returns ``"(unset)"`` for blank values, a partially-obscured token for
    SENSITIVE_KEYS (when ``mask`` is True), and a length-capped string otherwise.
    Callers that need markdown (the !config command) wrap the result themselves.
    """
    if val is None or val == "":
        return "(unset)"
    s = str(val)
    if mask and key in SENSITIVE_KEYS:
        return f"{s[:4]}…{s[-2:]}" if len(s) > 8 else "***"
    return s if len(s) <= 120 else f"{s[:117]}…"


def summary(cfg: dict, mask: bool = True) -> str:
    """Grouped plaintext dump of the active config.

    ``mask=True`` (default) obscures secrets — use it for anything that leaves
    the host (the !config command, log files). ``mask=False`` shows everything
    in the clear and is only safe for the local terminal.
    """
    lines: list[str] = []
    for section in SECTION_ORDER:
        keys = [k for k, sec in SECTION_MAP.items() if sec == section]
        if not keys:
            continue
        lines.append(f"[{section}]")
        for k in keys:
            lines.append(f"  {k} = {mask_value(k, cfg.get(k, DEFAULTS.get(k)), mask)}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
#  INI load / save / migration
# ══════════════════════════════════════════════════════════════════════════════


# Int-typed keys that store durations in seconds — accept "3m", "1 hour", etc.
_DURATION_SEC_KEYS = {
    "music_idle_timeout",
    "music_ratelimit_cooldown",
    "revalidate_age",
}

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


ENV_PREFIX = "NANOBOT_"


def env_key(key: str) -> str:
    """The environment variable that overrides one config key."""
    return f"{ENV_PREFIX}{key.upper()}"


def apply_env(cfg: dict, environ: Optional[dict] = None) -> dict:
    """Overlay `NANOBOT_<KEY>` environment variables onto a loaded config.

    This exists for hosts where a file is the awkward way to configure a
    process — a container, a PaaS, a systemd unit with an `EnvironmentFile` —
    and it is deliberately *only* an overlay:

    * `load()` still returns exactly what is in `config.ini`, so `set_value()`
      (which loads, edits and saves) can never write an environment value into
      the file. Editing config from Discord and configuring by environment stay
      separate systems that don't overwrite each other.
    * values are coerced by the same `_coerce` the INI parser uses, so
      `NANOBOT_DASHBOARD_PORT=8080` arrives as an int, not the string "8080".
    * an unknown `NANOBOT_*` name is ignored rather than guessed at.

    `DISCORD_TOKEN` (read by `main.py`) and `NANOBOT_DB_KEY` (read by
    `utils/db_crypto.py`) predate this and keep their own names.
    """
    env = os.environ if environ is None else environ
    out = dict(cfg)
    for field in FIELDS:
        raw = env.get(env_key(field.key))
        if raw is None:
            continue
        out[field.key] = _coerce(field.key, raw)
    return out


def env_overrides(environ: Optional[dict] = None) -> list[str]:
    """Which config keys the environment is currently overriding.

    Startup logs this so "I changed config.ini and nothing happened" is one
    line away from being answered.
    """
    env = os.environ if environ is None else environ
    return [f.key for f in FIELDS if env_key(f.key) in env]


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

    # Restrict to owner read/write — the file holds the bot token and API keys,
    # so on a shared host it must not be world- or group-readable. Best-effort:
    # chmod is a no-op semantics on Windows, so swallow failures there.
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


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
