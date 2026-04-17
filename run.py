"""
run.py — NanoBot Pre-flight Check
──────────────────────────────────
Run this before main.py to verify your setup is correct.
Checks everything it can without connecting to Discord.

Usage:
    python run.py          → full check, then auto-launch if all clear
    python run.py --check  → check only, don't launch
"""

import importlib.util
import os
import re
import sys

# ── ANSI colours ───────────────────────────────────────────────────────────────
_USE_COLOUR = sys.platform != "win32" or os.getenv("TERM")


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if _USE_COLOUR else text


def ok(msg):
    print(f"  {_c('32', '✅')} {msg}")


def fail(msg):
    print(f"  {_c('31', '❌')} {msg}")
    return False


def warn(msg):
    print(f"  {_c('33', '⚠️ ')} {msg}")


def head(msg):
    print(f"\n{_c('1;34', msg)}")


_errors = []


def err(msg):
    _errors.append(msg)
    return fail(msg)


# ══════════════════════════════════════════════════════════════════════════════


def check_python():
    head("Python Version")
    major, minor, patch = sys.version_info[:3]
    v = f"{major}.{minor}.{patch}"

    if major < 3 or (major == 3 and minor < 11):
        return err(
            f"Python {v} detected. NanoBot requires Python 3.11+.\n"
            "      Download: https://python.org/downloads"
        )
    ok(f"Python {v}")
    return True


def check_dependencies():
    head("Dependencies")
    all_good = True

    if importlib.util.find_spec("discord") is None:
        err("discord.py not installed → run: pip install discord.py")
        all_good = False
    else:
        try:
            import discord as _d

            ver = _d.__version__
            parts = tuple(int(x) for x in ver.split(".")[:2])
            if parts < (2, 3):
                warn(
                    f"discord.py {ver} — version 2.3.0+ recommended (pip install -U discord.py)"
                )
            else:
                ok(f"discord.py {ver}")
        except Exception:
            ok("discord.py found")

    return all_good


def check_file_structure():
    head("File Structure")
    all_good = True

    required_dirs = ["cogs", "utils"]
    optional_dirs = ["data"]
    required_files = [
        "main.py",
        "requirements.txt",
        "cogs/__init__.py",
        "cogs/moderation.py",
        "cogs/tags.py",
        "cogs/utility.py",
        "cogs/admin.py",
        "utils/__init__.py",
        "utils/db.py",
        "utils/helpers.py",
        "utils/checks.py",
        "utils/config.py",
    ]

    for d in required_dirs:
        if os.path.isdir(d):
            ok(f"Directory  {d}/")
        else:
            err(f"Directory missing: {d}/")
            all_good = False

    for d in optional_dirs:
        if os.path.isdir(d):
            ok(f"Directory  {d}/")
        else:
            warn(
                f"Directory {d}/ not found — will be created automatically on first run"
            )

    for f in required_files:
        if os.path.isfile(f):
            ok(f"File  {f}")
        else:
            err(f"File missing: {f}")
            all_good = False

    return all_good


def _looks_like_token(token):
    """Rough structural check — exact validity requires a Discord API call."""
    parts = token.split(".")
    if len(parts) != 3 or len(token) < 50:
        return False
    ok_chars = re.compile(r"^[A-Za-z0-9_\-]+$")
    return all(ok_chars.match(p) for p in parts)


def check_config():
    head("Config  (config.ini)")
    all_good = True

    # Lazy import so the pre-flight itself never crashes on a broken utils/.
    try:
        from utils import config as cfg_mod
    except Exception as exc:
        return err(f"could not import utils/config.py: {exc}")

    # Auto-migrate an old config.json if present.
    if not os.path.isfile("config.ini") and os.path.isfile("config.json"):
        if cfg_mod.migrate_from_json("config.json", "config.ini"):
            ok(
                "Migrated config.json → config.ini (old file renamed to config.json.bak)"
            )

    if not os.path.isfile("config.ini"):
        return err(
            "config.ini not found.\n"
            "      Copy example_config.ini to config.ini and fill in your token."
        )

    try:
        cfg = cfg_mod.load("config.ini")
    except Exception as exc:
        return err(f"config.ini could not be parsed: {exc}")

    ok("config.ini parsed OK")

    # ── Token ──────────────────────────────────────────────────────────────────
    env_token = os.getenv("DISCORD_TOKEN", "")
    token = cfg.get("token") or ""

    if env_token:
        ok("Token: DISCORD_TOKEN env var found (overrides config.ini)")
    elif not token or token in ("YOUR_BOT_TOKEN_HERE", "your_token_here", "TOKEN"):
        err(
            "Token: placeholder or missing.\n"
            "      Get your token: https://discord.com/developers/applications → Bot → Token\n"
            "      Then paste it into config.ini or set DISCORD_TOKEN env var."
        )
        all_good = False
    elif not _looks_like_token(token):
        warn(
            "Token: present but doesn't match Discord's expected format.\n"
            "      Double-check at https://discord.com/developers/applications\n"
            "      (Actual validity can only be confirmed at connection time.)"
        )
    else:
        safe = token[:10] + ("*" * 24)
        ok(f"Token: {safe}… (format looks valid — confirmed at connect time)")

    # ── Prefix ─────────────────────────────────────────────────────────────────
    prefix = cfg.get("default_prefix") or "!"

    if not isinstance(prefix, str) or len(prefix) == 0:
        err("Prefix: must be a non-empty string")
        all_good = False
    elif " " in prefix:
        err(f"Prefix: '{prefix}' contains a space — prefixes can't have spaces")
        all_good = False
    elif len(prefix) > 5:
        err(f"Prefix: '{prefix}' is {len(prefix)} characters — max is 5")
        all_good = False
    else:
        ok(f"Prefix: '{prefix}'")

    # ── Log level ──────────────────────────────────────────────────────────────
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    log_level = str(cfg.get("log_level") or "INFO").upper()
    if log_level not in valid_levels:
        err(
            f"log_level: '{log_level}' is not valid.\n      Choose from: {', '.join(sorted(valid_levels))}"
        )
        all_good = False
    else:
        ok(f"log_level: '{log_level}'")

    # ── log_http ───────────────────────────────────────────────────────────────
    log_http = cfg.get("log_http")
    if log_http is None:
        log_http = False
    if not isinstance(log_http, bool):
        warn("log_http: should be true or false — defaulting to false")
    else:
        ok(
            f"log_http: {str(log_http).lower()}  {'(verbose HTTP logging ON)' if log_http else '(discord.http at WARNING — normal)'}"
        )

    # ── Owner ID ───────────────────────────────────────────────────────────────
    owner_id = cfg.get("owner_id")
    if owner_id is None:
        ok("owner_id: not set — will use the Discord application owner")
    elif not str(owner_id).isdigit():
        err(f"owner_id: '{owner_id}' is not a valid Discord user ID (must be a number)")
        all_good = False
    else:
        ok(
            f"owner_id: {owner_id} (overrides application owner for !reload / !shutdown / etc.)"
        )

    return all_good


def check_data_dir():
    head("Data Directory")
    if os.path.isdir("data"):
        files = [f for f in os.listdir("data") if f.endswith(".json")]
        if files:
            ok(f"data/ exists  ({len(files)} JSON file(s): {', '.join(files)})")
        else:
            ok("data/ exists (empty — will populate on first use)")
    else:
        warn("data/ not found — NanoBot will create it on startup")
    return True


def check_intents_reminder():
    head("Discord Portal Reminder")
    warn(
        "Make sure these are enabled in the Discord Developer Portal:\n"
        "      https://discord.com/developers/applications → Your Bot → Bot\n\n"
        "      ✅ SERVER MEMBERS INTENT   (required for member-related commands)\n"
        "      ✅ MESSAGE CONTENT INTENT  (required for prefix commands)\n\n"
        "      Without these, prefix commands and most mod commands will silently fail."
    )
    return True


def check_logs_dir():
    head("Log Files")
    if os.path.isdir("logs"):
        log_files = sorted(
            [f for f in os.listdir("logs") if f.endswith(".log")],
            key=lambda f: os.path.getmtime(os.path.join("logs", f)),
            reverse=True,
        )
        if log_files:
            latest = log_files[0]
            size = os.path.getsize(os.path.join("logs", latest))
            ok(
                f"logs/ exists  ({len(log_files)} file(s), latest: {latest} [{size // 1024} KB])"
            )
        else:
            ok("logs/ exists (empty — log file created on first run)")
    else:
        warn("logs/ not found — will be created automatically on startup")

    ok(
        "Logging: discord.utils.setup_logging() (coloured console) "
        "+ RotatingFileHandler → logs/nanobot.log (5 MB × 3 backups)"
    )
    warn(
        "discord.http is set to WARNING to reduce noise.\n"
        "      To see raw HTTP request logs, set it to DEBUG in main.py."
    )
    return True


# ══════════════════════════════════════════════════════════════════════════════


def main():
    print(_c("1;36", "\n⚡ NanoBot — Pre-flight Check"))
    print(_c("90", "   Small. Fast. Built for Mobile Mods.\n"))

    results = [
        check_python(),
        check_dependencies(),
        check_file_structure(),
        check_config(),
        check_data_dir(),
        check_logs_dir(),
        check_intents_reminder(),
    ]

    passed = all(results)

    print()
    if passed:
        print(_c("1;32", "  ✅ All checks passed!"))
    else:
        count = sum(1 for r in results if not r)
        print(
            _c(
                "1;31",
                f"  ❌ {count} check(s) failed — resolve the issues above first.",
            )
        )

    check_only = "--check" in sys.argv

    if passed and not check_only:
        print(_c("90", "\n  Launching NanoBot in 2 seconds...  (Ctrl+C to cancel)\n"))
        import time

        try:
            time.sleep(2)
        except KeyboardInterrupt:
            print("\n  Cancelled.")
            return

        import asyncio
        import importlib.util as ilu

        spec = ilu.spec_from_file_location("main", "main.py")
        mod = ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        asyncio.run(mod.main())

    elif passed and check_only:
        print(_c("90", "\n  --check flag used. Run `python main.py` when ready."))
    else:
        print(_c("90", "\n  Fix the issues above, then re-run `python run.py`."))
        sys.exit(1)


if __name__ == "__main__":
    main()
