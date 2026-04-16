# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

NanoBot is a lightweight Discord moderation bot (Python 3.11+) built with discord.py. Its core design philosophy is mobile-first: commands are optimized for phone usage, including a "last sender" targeting system so mods don't have to copy user IDs. All data lives in a single local SQLite file—zero cloud dependencies.

## Commands

**Install and run:**
```bash
pip install -r requirements.txt
cp example_config.json config.json   # then edit with your bot token
python run.py                         # recommended: includes pre-flight validation
python main.py                        # direct launch, skips validation
```

**Format (CI enforces Black on every push):**
```bash
pip install black
black .
```

**Test (pytest):**
```bash
pip install -r requirements.txt      # discord.py is required by utils/helpers.py
pip install -r requirements-dev.txt
pytest tests/ -v
```

Tests cover pure-Python utilities with no Discord dependency:
- `tests/test_helpers.py` — `parse_duration`, `parse_duration_from_end`, `fmt_duration`, `parse_interval`, `fmt_interval`
- `tests/test_config.py` — `validate()` from `utils/config.py`

CI runs `pytest tests/ -v` on every push and pull request (`.github/workflows/tests.yml`).
Manual end-to-end testing against a Discord test server is still required for cog-level features.

**Migration (JSON → SQLite, idempotent):**
```bash
python migrate.py
```

## Architecture

### Entry Points

- **`run.py`** — Pre-flight checker: validates Python version, required packages, config schema, and directory structure before delegating to `main.py`. Prefer this for development.
- **`main.py`** — Defines `NanoBot(commands.Bot)` and the `main()` async function. Handles config loading, logging setup, cog loading, and global error handling for both prefix and slash commands.

### Plugin System (Cogs)

All features live in `cogs/` as discord.py cogs, hot-reloadable via `n!reload <cog>` (owner only). Each cog is independent—no cog imports another cog directly. Cross-cog shared logic belongs in `utils/`.

| Cog | Responsibility |
|---|---|
| `moderation.py` | Ban/kick/mute/purge/lock/slowmode, timed actions, last-sender targeting |
| `warnings.py` | Warning tracking with configurable auto-kick/ban thresholds |
| `automod.py` | Passive rule enforcement (spam, invites, links, caps, mentions, badwords, regex) |
| `auditlog.py` | 12 server event types logged to a configurable channel |
| `roles.py` | Persistent button-based self-assign role panels |
| `tags.py` | Personal and global text snippets; `n!tagname` shortcut fires any tag |
| `admin.py` | Owner-only: reload cogs, restart, git pull update, full upgrade (pull+pip+restart), sync slash commands |
| `reminders.py` / `recurring.py` | One-time and repeating reminders, restart-safe via SQLite |
| `welcome.py` | Per-guild join/leave messages with template variables |
| `utility.py` | Info commands (`/serverinfo`, `/userinfo`, `/help`) |
| `fun.py` | 26 social + 33 reaction GIF commands via nekos.best |
| `votes.py` | top.gg / DBL / discord.bots.gg stat posting and vote webhooks |
| `eli5.py` | Plain-English AI explanations via Groq (Llama 3.1 8B) |

### Data Layer

Two SQLite databases, both opened once at startup via `setup_hook()` and shared as module-level connections:

- **`data/nanobot.db`** — All persistent bot data. Managed by `utils/db.py`. Tables include: `tags`, `notes`, `prefixes`, `unban_schedules`, `slow_schedules`, `reminders`, `automod_regex_patterns`, warnings, automod config, auditlog settings, role panels, welcome config, recurring reminders, and vote history.
- **`data/cache.db`** — External content cache (anime images, stories). Managed by `utils/cache_db.py`.

Both use WAL mode (`PRAGMA journal_mode=WAL`) for concurrent read/write. All queries are async via `aiosqlite`. Initialize with `await db.init()` and `await cache_db.init()` in `NanoBot.setup_hook()`.

### Utilities (`utils/`)

- **`helpers.py`** — Embed factory (`ok()`, `err()`, `warn()`, `info()` with consistent brand colors), duration parsing (`parse_duration`, `parse_duration_from_end`, `parse_interval`), and `user_display()` for consistent user references.
- **`checks.py`** — Combined user+bot permission decorators (`has_ban_perms()`, `has_mod_perms()`, etc.). Always use these instead of bare `commands.has_permissions` so both the user and bot permissions are checked together.
- **`config.py`** — Config validation with detailed error reporting. Called by `run.py`.
- **`storage.py`** — Legacy JSON helpers kept for backward compatibility. New code should use `db.py`.

### Command System

The bot supports three invocation styles simultaneously:
1. Slash commands (`/ban`)
2. Prefix commands (`n!ban`, configurable per guild via `n!prefix`)
3. Mention commands (`@NanoBot ban`)

The `NanoBot` class in `main.py` overrides `get_prefix()` to look up per-guild prefixes from the `prefixes` table. It also maintains a `last_message_authors` dict per channel so `moderation.py` can target the last sender without requiring a user argument.

Tag shortcuts are detected in `on_message`: if a message matches no command but matches a guild tag name after the prefix, the tag fires automatically.

### Configuration

`config.json` (gitignored) at the repo root. All keys are optional except `token` (or `DISCORD_TOKEN` env var):

```json
{
  "token": "...",
  "default_prefix": "n!",
  "owner_id": null,
  "log_level": "INFO",
  "log_http": false,
  "topgg_token": null,
  "topgg_v1_token": null,
  "dbl_token": null,
  "discordbotsgg_token": null,
  "vote_webhook_port": 5000,
  "vote_webhook_secret": null,
  "groq_api_key": null
}
```

`log_level` changes take effect without restart (re-read dynamically). Logs rotate at 50 KB, 5 backups, written to `logs/nanobot.log`.

### CI

GitHub Actions runs two workflows on every push:
- **`black.yml`** — Auto-formats code with Black. If formatting is needed, it auto-commits with `[skip ci]`. Run `black .` locally before pushing to avoid the auto-commit noise.
- **`tests.yml`** — Runs the pytest suite (`pytest tests/ -v`). Installs `requirements.txt` then `requirements-dev.txt` before running.
