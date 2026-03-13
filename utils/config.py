"""
utils/config.py
Config schema validation — shared by run.py (startup check) and runtime.

Usage:
    from utils.config import validate, ConfigIssue
    issues = validate(cfg_dict)
    fatal  = [i for i in issues if i.fatal]
"""

from dataclasses import dataclass

VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
PLACEHOLDER_TOKENS = {"YOUR_BOT_TOKEN_HERE", "your_token_here", "TOKEN", ""}

_SCHEMA = {
    # key: (type_or_None, required, description)
    "token": (str, True, "Bot token from the Discord Developer Portal"),
    "default_prefix": (str, False, "Command prefix (max 5 chars, no spaces)"),
    "owner_id": (None, False, "Your Discord user ID (int or null)"),
    "log_level": (str, False, "Logging verbosity (DEBUG/INFO/WARNING/ERROR/CRITICAL)"),
    "log_http": (bool, False, "Log raw HTTP requests (true/false)"),
    "topgg_token": (str, False, "top.gg AUTH token (str or null)"),
    "topgg_v1_token": (
        str,
        False,
        "top.gg v1 API token for commands sync (str or null)",
    ),
    "dbl_token": (str, False, "discordbotlist.com bot token (str or null)"),
    "discordbotsgg_token": (str, False, "discord.bots.gg bot token (str or null)"),
    "vote_webhook_port": (int, False, "an open port required to get votes"),
    "vote_webhook_secret": (
        str,
        False,
        "the secret used by bot list sites to verify webhooks",
    ),
}


@dataclass
class ConfigIssue:
    field: str
    message: str
    fatal: bool  # True = bot cannot start; False = warning only

    def __str__(self) -> str:
        tag = "ERROR" if self.fatal else "WARN"
        return f"[{tag}] {self.field}: {self.message}"


def validate(cfg: dict) -> list[ConfigIssue]:
    """
    Validate a config dict against the NanoBot schema.
    Returns a list of ConfigIssue objects (empty = all good).
    """
    issues: list[ConfigIssue] = []

    # ── Unknown keys ───────────────────────────────────────────────────────────
    for key in cfg:
        if key not in _SCHEMA:
            issues.append(
                ConfigIssue(
                    field=key,
                    message=f"Unrecognised key '{key}' — check for typos",
                    fatal=False,
                )
            )

    # ── Token ──────────────────────────────────────────────────────────────────
    token = cfg.get("token", "")
    if not isinstance(token, str) or token.strip() in PLACEHOLDER_TOKENS:
        issues.append(
            ConfigIssue(
                field="token",
                message="Missing or placeholder. Get yours at discord.com/developers/applications → Bot → Token",
                fatal=True,
            )
        )

    # ── default_prefix ─────────────────────────────────────────────────────────
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

    # ── log_level ──────────────────────────────────────────────────────────────
    raw_level = cfg.get("log_level", "INFO")
    if not isinstance(raw_level, str):
        issues.append(
            ConfigIssue(
                "log_level", f"Must be a string, got {type(raw_level).__name__}", False
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

    # ── log_http ───────────────────────────────────────────────────────────────
    log_http = cfg.get("log_http", False)
    if not isinstance(log_http, bool):
        issues.append(
            ConfigIssue(
                field="log_http",
                message=f"Expected true or false, got {type(log_http).__name__} '{log_http}'",
                fatal=False,
            )
        )

    # ── owner_id ───────────────────────────────────────────────────────────────
    owner_id = cfg.get("owner_id")
    if owner_id is not None:
        if not isinstance(owner_id, int) and not str(owner_id).isdigit():
            issues.append(
                ConfigIssue(
                    field="owner_id",
                    message=f"'{owner_id}' is not a valid Discord user ID (must be an integer or null)",
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

    return issues


def assert_no_fatal(cfg: dict) -> None:
    """Raise ValueError if config has any fatal issues. Used at bot startup."""
    issues = validate(cfg)
    fatal = [i for i in issues if i.fatal]
    if fatal:
        msg = "Config errors prevent startup:\n" + "\n".join(f"  • {i}" for i in fatal)
        raise ValueError(msg)
