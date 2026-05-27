# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Communication Style

Use caveman mode for all responses: drop articles (a/an/the), filler words (just/really/basically/actually/simply), pleasantries (sure/certainly/of course/happy to), and hedging. Fragments OK. Short synonyms preferred. Technical terms exact. Code blocks unchanged.

Exception: user-facing strings written into bot code (Discord embeds, error messages, command descriptions, help text) use normal, friendly English — users read those directly.

## Overview

NanoBot is a lightweight Discord moderation bot (Python 3.11+) built with discord.py. Its core design philosophy is mobile-first: commands are optimized for phone usage, including a "last sender" targeting system so mods don't have to copy user IDs. All data lives in a single local SQLite file—zero cloud dependencies.

## Commands

**Install and run:**
```bash
pip install -r requirements.txt
cp example_config.ini config.ini     # then edit with your bot token
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

Tests cover pure-Python utilities and the SQLite layer (in-memory), no live Discord dependency:
- `tests/test_helpers.py` — `parse_duration`, `parse_duration_from_end`, `fmt_duration`, `parse_interval`, `fmt_interval`
- `tests/test_config.py` — `validate()` from `utils/config.py`
- `tests/test_config_io.py` — load, save, migrate, `set_value`, `_coerce`, `_format`, `assert_no_fatal`, `example_ini` from `utils/config.py`
- `tests/test_db.py` — `utils/db.py` against an in-memory SQLite database
- `tests/test_cache_db.py` — `utils/cache_db.py` against an in-memory SQLite database
- `tests/test_storage.py` — sync and async JSON helpers in `utils/storage.py`
- `tests/test_music_helpers.py` — pure helper functions extracted from `cogs/music.py` (no yt-dlp/FFmpeg needed)
- `tests/test_leveling_helpers.py` — pure level-math helpers from `cogs/leveling.py` (XP curve, progress bar)
- `tests/test_leveling_db.py` — leveling accessors in `utils/db.py` against in-memory SQLite
- `tests/test_economy_helpers.py` — pure economy helpers from `cogs/economy.py` (coin formatting, daily/streak math)
- `tests/test_economy_db.py` — economy accessors in `utils/db.py` against in-memory SQLite
- `tests/test_no_duplicate_commands.py` — static check that no two cogs register the same top-level command name or alias
- `tests/test_obs.py` — correlation ids, the logging filter, and the JSONL event sink in `utils/obs.py`

Command-level tests (parse → permission check → DB → reply) run under **dpytest**, which fakes a guild/members/message dispatch so cog wiring executes without a live gateway:
- `tests/conftest.py` — the `bot` fixture (dpytest-configured `NanoBot` + throwaway DB; load cogs via `@pytest.mark.cogs(...)`) and `grant_perms` helper
- `tests/test_commands_dpytest.py` — permission enforcement + a note write/read round-trip
- `tests/test_leveling_commands.py` — `/rank` reply wiring + `/level` admin permission enforcement and config round-trip
- `tests/test_economy_commands.py` — `/balance`/`/daily`/`/pay` flows + `/coin grant` permission enforcement

CI runs `pytest tests/ -v` on every push and pull request (`.github/workflows/tests.yml`).
Manual end-to-end testing against a Discord test server is still useful for live-gateway behavior (voice, presence, real latency) that dpytest does not simulate.

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
| `automod.py` | Passive rule enforcement (spam, invites, links, caps, mentions, badwords, regex, word+attachment); actions: delete/warn/timeout/kick/softban |
| `auditlog.py` | 12 server event types logged to a configurable channel |
| `roles.py` | Persistent button-based self-assign role panels |
| `tags.py` | Personal and global text snippets; `n!tagname` shortcut fires any tag |
| `admin.py` | Owner-only: reload cogs, restart, git pull update, full upgrade (pull+pip+restart), sync slash commands |
| `reminders.py` / `recurring.py` | One-time and repeating reminders, restart-safe via SQLite |
| `welcome.py` | Per-guild join/leave messages with template variables |
| `utility.py` | Info commands (`/server`, `/user`, `/help`; `serverinfo`/`userinfo` aliases) |
| `fun.py` | 26 social + 33 reaction GIF commands via nekos.best |
| `votes.py` | top.gg / DBL / discord.bots.gg stat posting and vote webhooks |
| `eli5.py` | Plain-English AI explanations via Groq (Llama 3.1 8B) |
| `images.py` | Anime image commands (husbando, kitsune, neko, waifu) via nekos.best |
| `debug.py` | Owner-only debug REPL / shell evaluation |
| `leveling.py` | Per-guild message XP + levels (Mee6-style curve). `/rank` card (flat hybrid) + `/level` group: `top` leaderboard, plus Manage-Server admin subcommands `set`/`give`/`reset`/`toggle`/`rate`/`announce`/`reward`/`ignore`/`config`. In-memory per-member XP cooldown; role rewards granted on level-up; off by default. |
| `economy.py` | Per-guild NanoCoin economy. Flat `/balance`, `/daily` (24h cooldown + consecutive-day streak bonus), `/pay` + `/coin` group: `top` rich list, plus Manage-Server admin subcommands `grant`/`take`/`reset`/`daily`/`streakbonus`/`name`/`emoji`/`config`. Currency name/emoji configurable per guild. Command-driven (no passive earning in v1). |
| `music.py` | Voice music player: yt-dlp streaming, Spotify link support (no API key — embed-page metadata scraped then matched on YouTube at play time), per-guild queue, interactive Now Playing card (buttons), search picker, vote-skip, playnext/playnow/stream/shuffleplay, follow, move/jump, loop/shuffle/seek/speed/audio-filters/volume, lyrics, grab, pldump, autoplay + persistent autoplaylist (add accepts whole playlists, dead entries auto-pruned on error), 24/7 stay-connected mode (`radio`/`247`, per-guild, off by default), idle auto-disconnect, per-guild song/user block lists (`blocksong`/`blockuser`, Manage Server), played-track `history`, self-deafen on join, now-playing bot presence (Streaming status with a clickable Watch button for YouTube/Twitch tracks, reverts to "watching over the server" when idle; account-global since Discord has no per-guild activity; `music_status_message` customizes the text), configurable yt-dlp search service/proxy/user-agent/source-address, and YouTube rate-limit (429) back-off. Audio output is configurable via `music_use_opus`: Opus (default) is stream-copied from the source when unprocessed, else re-encoded with libopus at the voice channel's bitrate; PCM mode decodes every track and lets discord.py encode, trading CPU for instant (re-stream-free) volume changes. `music_persist_queue` (on by default) saves the queue to SQLite and resumes it (rejoins the last voice channel, current track restarts from 0:00, rest of queue intact) on restart; `music_predownload` (on by default) fetches the next queued track to `data/music_cache/` while one plays for gapless playback (live streams are skipped — they'd never finish downloading). Reads `[music]` config (incl. cookies). Requires FFmpeg + PyNaCl. Live playback position is in-memory; the autoplaylist, 24/7 setting, and (when enabled) the queue persist in SQLite. |

### Data Layer

Two SQLite databases, both opened once at startup via `setup_hook()` and shared as module-level connections:

- **`data/nanobot.db`** — All persistent bot data. Managed by `utils/db.py`. Tables include: `tags`, `notes`, `prefixes`, `unban_schedules`, `slow_schedules`, `reminders`, `automod_config`, `automod_badwords`, `automod_regex_patterns`, `automod_attachment_words`, warnings, auditlog settings, role panels, welcome/leave config, recurring reminders, vote history, the music tables (`music_settings`, `music_queue`, `music_history`, `music_autoplaylist`, `music_song_blocklist`, `music_user_blocklist`), the leveling tables (`user_levels`, `level_config`, `level_rewards`, `level_ignored_channels`), and the economy tables (`economy`, `economy_config`).
- **`data/cache.db`** — External content cache (anime images, stories). Managed by `utils/cache_db.py`.

Both use WAL mode (`PRAGMA journal_mode=WAL`) for concurrent read/write. All queries are async via `aiosqlite`. Initialize with `await db.init()` and `await cache_db.init()` in `NanoBot.setup_hook()`.

**Schema changes:** the `CREATE TABLE IF NOT EXISTS` / `_ensure_columns()` calls in `db.init()` are the version-0 baseline (idempotent on every start). Adding a column to an existing table uses `_ensure_columns(table, {col: definition})`. For anything more involved, register a forward-only migration with `@db.migration(N)` — these run in ascending order on startup, tracked by `PRAGMA user_version`, which advances only after a migration succeeds (so a failure retries next start; write migrations to be safe to re-run). Don't scatter ad-hoc `ALTER TABLE` blocks through `init()`.

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

`config.ini` (gitignored) at the repo root, split into six sections:

* **`[bot]`** — `token`, `default_prefix`, `owner_id`, `error_channel_id`
* **`[logging]`** — `log_level`, `log_http`, `log_events_jsonl`
* **`[votes]`** — `topgg_v1_token`, `dbl_token`, `discordbotsgg_token`, `vote_webhook_port`, `vote_webhook_secret`, `webhook_allowed_ips`
* **`[groq]`** — `groq_api_key`
* **`[scraper]`** — `fml_pages_per_scrape`, `wyr_requests_per_scrape`, `nekos_per_endpoint`, `nekosia_per_tag`, `revalidate_age`, `revalidate_batch`, `groq_wyr_system`
* **`[music]`** — playback/queue knobs read live from `bot.config` so `!reloadconfig` applies without a cog reload: `music_cookie_file`, `music_default_volume`, `music_idle_timeout`, `music_skip_ratio`, `music_max_queue`, `music_use_opus`, `music_persist_queue`, `music_predownload`, `music_self_deafen`, `music_default_speed`, `music_search_service` (ytsearch/ytmsearch/scsearch), `music_status_message` ({title} presence template), `music_proxy`, `music_user_agent`, `music_source_address`, `music_js_runtime_path` (deno/node/bun binary for yt-dlp JS challenges), `music_autoplay_autoskip`, `music_save_videos` + `music_cache_max_mb`/`music_cache_max_age_days` (cache caps), `music_ratelimit_cooldown`/`music_ratelimit_leave` (429 back-off), `music_apl_prune_on_error`, `music_save_history`.

All keys are optional except `token` (or the `DISCORD_TOKEN` env var). An old `config.json` is auto-migrated to `config.ini` on first start (the legacy file is renamed to `config.json.bak`).

The bot keeps the whole flat config on `bot.config`. Cogs that need live values (fun.py scraper knobs, for example) read from `bot.config` on every use so `!reloadconfig` takes effect without a cog reload. Values captured at `__init__` time (e.g. votes.py webhook settings) still need `!reload votes` to change.

Live editing:
* `!setloglevel <level>` — changes the log level and persists to `config.ini`.
* `!reloadconfig` — re-reads `config.ini` from disk.
* `!config show|get|set|unset …` — DM-only inspect/edit. Secrets (token, API keys, webhook secret) are always masked when echoed back.

Logs rotate at 50 KB, 5 backups, written to `logs/nanobot.log`. Each line carries a short correlation id (`[abcd1234]`) shared by the start/completion/error records of one command invocation, so a single command's trace is greppable. When `log_events_jsonl` is on (default), structured command-lifecycle events (`command.start/ok/err`, `slash.start/ok/err` with `dur_ms`, user, guild) are also written to `logs/events.jsonl` (one JSON object per line, rotating). Correlation/timing helpers live in `utils/obs.py`.

### CI

GitHub Actions runs two workflows on every push:
- **`black.yml`** — Auto-formats code with Black. If formatting is needed, it auto-commits with `[skip ci]`. Run `black .` locally before pushing to avoid the auto-commit noise.
- **`tests.yml`** — Runs the pytest suite (`pytest tests/ -v`). Installs `requirements.txt` then `requirements-dev.txt` before running.

A third workflow, **`branch-protection.yml`**, is not part of the per-push CI: it runs only via `workflow_dispatch` or when pushed to `main` touching that file, applying `main` branch protection (required `test` + `black` checks, 1 review, no force-push) via the GitHub API.
