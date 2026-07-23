# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Communication Style

Use caveman mode for all responses: drop articles (a/an/the), filler words (just/really/basically/actually/simply), pleasantries (sure/certainly/of course/happy to), and hedging. Fragments OK. Short synonyms preferred. Technical terms exact. Code blocks unchanged.

Exception: user-facing strings written into bot code (Discord embeds, error messages, command descriptions, help text) use normal, friendly English — users read those directly.

## Overview

NanoBot is a lightweight Discord moderation bot (Python 3.11+) built with discord.py. Its core design philosophy is mobile-first: commands are optimized for phone usage, including a "last sender" targeting system so mods don't have to copy user IDs. All data lives in a single local SQLite file—zero cloud dependencies.

## Commands

**Install and run (scripted):**
```bash
./install.sh        # Linux/macOS setup: system deps, ./venv, pip, config.ini (Windows: install.bat → install.ps1, WinGet)
./run.sh            # launcher: prefers ./venv, finds Python 3.11+, runs run.py (Windows: run.bat)
```

**Install and run (manual):**
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
pip install -r requirements.dev.txt  # superset: runtime deps (requirements.txt) + dev/test extras
pytest tests/ -v
```

Tests cover pure-Python utilities and the SQLite layer (in-memory), no live Discord dependency:
- `tests/test_helpers.py` — `parse_duration`, `parse_duration_from_end`, `fmt_duration`, `parse_interval`, `fmt_interval`
- `tests/test_config.py` — `validate()` from `utils/config.py`
- `tests/test_config_io.py` — load, save, migrate, `set_value`, `_coerce`, `_format`, `assert_no_fatal`, `example_ini` from `utils/config.py`
- `tests/test_db.py` — the `utils/db/` package against an in-memory SQLite database
- `tests/test_db_crypto.py` — `utils/db_crypto.py` key resolution + header sniffing; the encrypted round-trip/migration tests need `sqlcipher3-binary` (in requirements.dev.txt) and self-skip without it
- `tests/test_cache_db.py` — `utils/cache_db.py` against an in-memory SQLite database
- `tests/test_storage.py` — sync and async JSON helpers in `utils/storage.py`
- `tests/test_music_helpers.py` — pure helper functions imported directly from `cogs/music/helpers.py` (Discord/yt-dlp-free module); includes `_extract_ytid`
- `tests/test_music_player.py` — voice-state recovery logic built via `__new__` (no live gateway): `GuildPlayer.skip()`'s honest bool return, `Music._clear_ghost_voice()`'s gateway-leave decision, and `Music.on_voice_state_update`'s bot-self-disconnect teardown (resets the stuck Streaming presence)
- `tests/test_leveling_helpers.py` — pure level-math helpers from `cogs/leveling.py` (XP curve, progress bar)
- `tests/test_leveling_db.py` — leveling accessors in `utils/db/` against in-memory SQLite
- `tests/test_economy_helpers.py` — pure economy helpers from `cogs/economy/` (coin formatting, daily/streak math, `_rank_title` contribution titles); imports stay on the flat `cogs.economy` API re-exported by the package `__init__`
- `tests/test_economy_db.py` — economy accessors in `utils/db/` against in-memory SQLite (balances, daily, config incl. the co-op/raid reward + party-size knobs, the lifetime contribution stat/leaderboard, and the shop: item CRUD + `purchase_item` enforcing funds/stock/per-user-limit/cooldown with stock refund-on-fail, plus the custom-reward pending/fulfil queue, and the persisted co-op CRUD `economy_raids` + `economy_squads`)
- `tests/test_no_duplicate_commands.py` — static check that no two cogs register the same top-level command name or alias
- `tests/test_obs.py` — correlation ids, the logging filter, and the JSONL event sink in `utils/obs.py`
- `tests/test_gatekeeper.py` — perceptual (difference) hash helpers + the join-time `_evaluate` mute-decision logic in `cogs/gatekeeper.py` (the slash-only `/gatekeeper` group isn't dispatchable via dpytest); gatekeeper DB accessors live in `tests/test_db.py`
- `tests/test_birthday_helpers.py` — pure date helpers from `cogs/birthday/` (`parse_birthday`, `next_birthday_date`, `days_until_birthday`, `is_birthday_today` incl. leap-day, `age_on`, `fmt_birthday`), the voice-region→timezone guesser + `_TZ_CHOICES` IANA validity, and the FFmpeg song-command builder
- `tests/test_birthday_db.py` — birthday accessors in `utils/db/` against in-memory SQLite (config defaults/roundtrip, the enabled-config filter, the `last_announced` once-a-year stamp)
- `tests/test_tickets_helpers.py` — pure helpers from `cogs/tickets/` (`thread_name` sanitise/truncate, `transcript_line` formatting)
- `tests/test_converters.py` — `SafeTextChannel` in `utils/converters.py`: cache-hit/fetch-fallback/fetch-fail transform paths + the hybrid-command slash-option/prefix-converter regression shape of the `/level announce` cache-miss bug
- `tests/test_tickets_db.py` — ticket accessors in `utils/db/` against in-memory SQLite (config roundtrip, per-guild sequential numbering, thread attach/lookup, the reserve-rollback `delete_ticket`, per-user open counts, the close/claim conditional-UPDATE race guards, the startup `close_stale_tickets` sweep)

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
| `moderation/` | Ban/kick/mute/purge/lock/slowmode, timed actions, last-sender targeting (a package — see "Moderation package layout" below) |
| `warnings.py` | Warning tracking with configurable auto-kick/ban thresholds |
| `automod/` | Passive rule enforcement (spam, invites, links, caps, mentions, badwords, regex, word+attachment); actions: delete/warn/timeout/kick/softban (a package — see "AutoMod package layout" below) |
| `gatekeeper/` | New-account gate: on join, role-mutes (`Muted (NanoBot)`) accounts younger than a threshold (default 30d, auto-unmute at 35d/5w), with no avatar (Discord logo default, `member.avatar is None`), or with a pickable "stock" avatar matched by perceptual (difference) hash against a **system-wide** catalog (`assets/gatekeeper_avatars/` bundled seeds + `data/gatekeeper_avatars/` runtime adds via `/gatekeeper learnavatar`; the catalog is global, so every guild reads the same images and any guild's `learnavatar` contributes to it). How the age and avatar checks combine is per-guild via `match_mode` (`or` = mute on either signal, default; `and` = mute only when an account is both too young AND has a bad avatar). Muted members get a verification prompt (DM first, fallback to a quarantine channel) with a persistent button → math-captcha modal; correct answer unmutes. Account-age mutes auto-unmute once the account ages out (per-guild `age_unmute_enabled`, on by default; off forces verification). Unverified members are kicked after a timeout (default 7d). Auto-unmute + auto-kick persist in SQLite and restore on restart via `on_restore_schedules` (mirrors `moderation.py`). Log-channel events are emoji-tagged: 🔇 mute, ✅ verify, 🔊 auto-unmute, 🚪 kick. `/gatekeeper` group (Manage Server): `setup`/`status`/`enable`/`disable`/`role`/`channel`/`logchannel`/`minage`/`unmuteage`/`kicktimeout`/`newaccounts`/`noavatar`/`stockavatar`/`sensitivity` (per-guild dHash match distance, default 8)/`matchmode` (and/or)/`ageunmute` (toggle age auto-unmute)/`verify`/`message`/`learnavatar`/`checkavatar`. Bulk-seed the stock-avatar catalog from a Figma file with `scripts/import_figma_avatars.py` (stdlib-only, needs a Figma token). |
| `auditlog.py` | 13 toggleable event types (12 Discord server events + AutoMod action) logged to a configurable channel |
| `roles/` | Persistent button-based self-assign role panels, incl. `/roles autogen` palette generation (a package — see "Roles package layout" below) |
| `tags.py` | Personal and global text snippets; `n!tagname` shortcut fires any tag |
| `admin/` | Owner-only: reload cogs, restart, git pull update, full upgrade (pull+pip+restart), sync slash commands, `status` (set idle presence text / `clear` to auto-rotate) (a package — see "Admin package layout" below) |
| `reminders.py` / `recurring.py` | One-time and repeating reminders, restart-safe via SQLite |
| `welcome.py` | Per-guild join/leave messages with template variables |
| `utility/` | Info commands (`/server`, `/user`, `/help`; `serverinfo`/`userinfo` aliases) (a package — see "Utility package layout" below) |
| `fun/` | 26 social + 33 reaction GIF commands via nekos.best (a package — see "Fun package layout" below) |
| `votes.py` | top.gg / DBL stat posting + vote webhooks; discord.bots.gg stat posting only (site has no voting/vote-webhook API) |
| `eli5.py` | Plain-English AI explanations via Groq (GPT-OSS 20B) |
| `images.py` | Anime image commands (husbando, kitsune, neko, waifu) via nekos.best |
| `debug.py` | Owner-only debug REPL / shell evaluation |
| `leveling.py` | Per-guild message XP + levels (Mee6-style curve). `/rank` card (flat hybrid) + `/level` group: `top` leaderboard, plus Manage-Server admin subcommands `set`/`give`/`reset`/`toggle`/`rate`/`announce`/`reward`/`ignore`/`coinreward`/`config`. In-memory per-member XP cooldown; role rewards granted on level-up; optional coin reward on level-up (`coin_reward` × new level, written via `db.add_coins`); off by default. |
| `economy.py` | Per-guild NanoCoin economy. Flat `/balance` (shows coin rank + lifetime contribution rank/title), `/daily` (24h cooldown + consecutive-day streak bonus), `/pay`, `/squad` (co-op activity reward, alias `coop`: tag up to five teammates directly, or tag no one to open a host-only `SquadBuilderView` UserSelect picker for a squad of up to 25; each teammate confirms via button → the whole party earns `coop_reward` coins + equal contribution points once *everyone* has confirmed; the contribution stat is lifetime/spend-proof and drives a separate `/coin contrib` leaderboard with position-based rank titles via `_rank_title`; the pending confirm is persisted in SQLite (`economy_squads`, incl. who's confirmed so far) and restored on restart via `on_restore_schedules` — persistent view (`squad:{id}:{action}` custom_ids) so a restart mid-confirm doesn't orphan the button or drop the payout — auto-expires `COOP_CONFIRM_TIMEOUT` via a cog-owned timer; the `SquadBuilderView` picker itself stays in-memory since it carries no payout), `/raid` (group co-op for big activities/raids: opens a `RaidView` join board anyone presses **Join** on — clicking is self-confirmation — up to the per-guild party cap; the host or a Manage-Server mod presses **Finish** to pay every participant `raid_reward` coins + equal contribution, or **Cancel** to scrap it; the board is persisted in SQLite (`economy_raids`) and restored on restart via `on_restore_schedules` — persistent view (`raid:{id}:{action}` custom_ids) so a restart no longer orphans the buttons ("This interaction failed") — and auto-expires `RAID_TIMEOUT` after creation via a cog-owned timer). `/coin` group: `top` rich list, `contrib` contributor leaderboard, `gamble` (bet coins, ~45% win, double-or-nothing), plus Manage-Server admin subcommands `grant`/`take`/`reset`/`daily`/`streakbonus`/`coop` (co-op reward, 0 disables `/squad`)/`raid` (raid reward, 0 disables `/raid`)/`raidsize` (min/max party size)/`name`/`emoji`/`config`. `/shop` group lets members spend coins on mod-configured rewards: `list`/`buy <id|name>` for everyone, plus Manage-Server `seed` (drop in a curated set of generic starter rewards so a fresh shop isn't empty — all `custom` kind, mod-fulfilled, idempotent by name)/`add`/`edit`/`remove`/`pending`/`fulfill`. Shop items are `role` (bot grants a Discord role instantly — validated grantable before charging, coins refunded + stock restored if the grant fails) or `custom` (arbitrary reward text; queued unfulfilled for a mod, delivered with `/shop fulfill`). Per-item optional `stock` (atomic decrement, refunded on funds-fail so a sold-out race never overspends/oversells), `per_user_limit`, and `cooldown`. Currency name/emoji configurable per guild. |
| `birthday/` | Per-guild birthday tracker (a package — see "Birthday package layout" below). `/birthday` group: members self-register (`set`/`remove`/`view`/`list`) with optional birth year (drives the age line); per-guild setup (Manage Server) `channel`/`disable`/`timezone`/`hour`/`message`/`gifs`/`voice`/`ping`/`config`/`test`. A `tasks.loop(minutes=15)` background check (not an event — restart-safe) announces each birthday once per year in the configured channel with a random festive GIF, gated on the guild-local timezone + hour; members sharing a day get **one combined post** (joined mentions + a per-member Turns field) instead of back-to-back posts. The per-row `last_announced` local-date stamp is written **before** the announcement, so a crash can't double-fire and a later same-day check is a no-op (offline during the hour → still fires that local day, no cross-day catch-up). The "Happy Birthday" voice song fires **any time the birthday member is in a voice channel that local day**: on join via `on_voice_state_update` plus a 15-minute in-voice sweep in the check loop (catches members already in voice before the bot started or before the announce hour), at most once per member per local day — guarded by an in-memory `_vc_sung` set (recorded only on a successful play, pruned daily) + a per-guild `_singing` set (claimed before any await so shared-birthday members in one VC can't race the player; the loser retries next sweep); when idle in the member's channel an existing voice client is reused, otherwise it skips rather than hijacking it (so it won't fight the music cog). The song is a synthesized melody (sine tones rendered once by FFmpeg to `data/birthday_cache/`, or a `song` path/URL override); voice needs FFmpeg + PyNaCl and degrades to text-only when unavailable. GIFs are HEAD-checked for reachability (`_validate_gifs`/`_pick_gif`, cached) so an announcement always shows a working one. Timezones are IANA names (DST-correct, never fixed offsets): `/birthday timezone` is a search-as-you-type autocomplete (the curated `_TZ_CHOICES` common zones with friendly labels first, then the full IANA database filtered by substring), and `channel` setup pre-fills a best-effort guess from the guild's voice-channel `rtc_region` (`guess_timezone_from_regions` + `_REGION_TZ`). |
| `tickets/` | Private-thread support tickets (a package — see "Tickets package layout" below). A persistent **Open Ticket** panel button (or `/ticket open`) shows a subject+details modal, then creates a private thread under the panel channel — no permission overwrites, no per-ticket channels; the opener is added explicitly and the staff-role mention pulls staff in. Staff **Claim** (conditional UPDATE — first claimer wins) and **Close** via in-thread buttons or `/ticket close [reason]` (opener may close too); closing posts a closing embed, drops a plain-text transcript (up to 500 messages) in the log channel, then locks+archives the thread (falls back to archive-only without Manage Threads). Restart-safe with zero per-ticket restore: all three buttons use **static** custom_ids and resolve their ticket from the clicked channel via SQLite (`tickets`/`ticket_config`). Edge cases: per-user open cap (`max_open`, default 3), double-click open races (in-memory `_opening` guard + post-modal recheck), per-guild ticket numbers computed inside the INSERT (no duplicate-number race), bot thread-permission prechecks before the modal ever opens, crash between row reserve and thread create (swept by `close_stale_tickets()` at startup), manual thread deletion (`on_raw_thread_delete` auto-closes the row), opener leaving the guild (`on_raw_member_remove` posts a note for staff), and auto-archived tickets (un-archived to post the closer). `/ticket` group (guild-only): everyone `open`/`close`/`claim`/`add`/`remove` (claim/add/remove staff-gated in code), Manage Server `setup` (staff role + log channel + cap)/`panel` (post the button, custom title/message persisted)/`limit`/`config`/`enable`/`disable`. Staff = Manage Server or the configured staff role (deleted role degrades to Manage-Server-only). |
| `music/` | Voice music player (a package — see "Music package layout" below). yt-dlp streaming, Spotify link support (no API key — embed-page metadata scraped then matched on YouTube at play time), per-guild queue, interactive Now Playing card (buttons), search picker, vote-skip, playnext/playnow/stream/shuffleplay, follow, move/jump, loop/shuffle/seek/speed/audio-filters/volume, lyrics, grab, pldump, smart `autoplay` (queues YouTube Mix/related tracks when the queue empties, seeded by last played YouTube track) + `guildplay` (plays random tracks from the server's persistent `guildplaylist`/`gpl`; add accepts whole playlists, dead entries auto-pruned on error), 24/7 stay-connected mode (`radio`/`247`, per-guild, off by default), idle auto-disconnect, per-guild song/user block lists (`blocksong`/`blockuser`, Manage Server), played-track `history`, self-deafen on join, now-playing bot presence (Streaming status with a clickable Watch button for YouTube/Twitch tracks; account-global since Discord has no per-guild activity; `music_status_message` customizes the text). Presence is owned by `NanoBot` in main.py (`set_music_activity`/`apply_presence`/hourly `_presence_loop`): music activity takes priority while a track plays, otherwise the idle status shows a manual override (`idle_status_message`, set live via owner `!status`) or auto-rotates "Listening to /help | /<command>" hourly. Tracks carry a cleaned `artist` field (`_clean_artist` prefers yt-dlp's `artist`/`creator` tag, else de-dupes the uploader against the title; the Now Playing card shows Artist when known, else Uploader). When the artist is still unknown, `_enrich_metadata` does a one-shot lookup against Apple's free, keyless iTunes Search API at Now-Playing time (`music_metadata_lookup`, on by default; `_pick_itunes_match` only accepts a result whose track name substantially overlaps the title). Configurable yt-dlp search service/proxy/user-agent/source-address, and YouTube rate-limit (429/403) back-off (`music_ratelimit_leave` posts a notice to each guild's last control-panel channel before disconnecting). Audio output is configurable via `music_use_opus`: Opus (default) is stream-copied from the source when unprocessed, else re-encoded with libopus at the voice channel's bitrate; PCM mode decodes every track and lets discord.py encode, trading CPU for instant (re-stream-free) volume changes. `music_persist_queue` (on by default) saves the queue to SQLite and resumes it (rejoins the last voice channel, current track restarts from 0:00, rest of queue intact) on restart; `music_predownload` (on by default) fetches the next queued track to `data/music_cache/` while one plays for gapless playback (live streams are skipped — they'd never finish downloading). Downloading is the only thing that populates the cache, so enabling `music_save_videos` also triggers the fetch even when `music_predownload` is off (otherwise the cache config would be dead). `music_sponsorblock` (off by default) skips non-music/sponsor segments via the SponsorBlock database: yt-dlp's `SponsorBlock`+`ModifyChapters` postprocessors download the track and FFmpeg-cut the matched segments before play (so it also forces a download, like `save_videos`; live streams can't be cut and stream uncut); `music_sponsorblock_categories` (default `music_offtopic` — the non-music section of a music video) picks which categories to remove. Reads `[music]` config (incl. cookies). Requires FFmpeg + PyNaCl. Live playback position is in-memory; the guild playlist, 24/7 setting, and (when enabled) the queue persist in SQLite. |

### Music package layout

`cogs/music/` is the one cog split into a package (it was a single 4k-line file). `load_extension("cogs.music")` still works via `setup()` in `__init__.py`. Submodules, in dependency order (no import cycles; cross-class type hints use `TYPE_CHECKING`):

| Module | Holds |
|---|---|
| `constants.py` | Module constants + compiled regexes (`ACCENT`, `LOOP_*`, `FILTERS`, `_YTDL_BASE`, `_MUSIC_CACHE_DIR`, `_SPOTIFY_*`, …). |
| `helpers.py` | Pure, Discord/yt-dlp-free helpers (`_extract_ytid`, `_fmt_time`, `_clean_artist`, `_pick_itunes_match`, …). Imported directly by tests. |
| `track.py` | The `Track` dataclass. |
| `source.py` | `MusicSource`: yt-dlp extraction/search, downloading, the on-disk cache, and Spotify metadata scraping (the "downloaded.py" equivalent). Holds a back-ref to the cog for config + `players`. Owns the `yt_dlp` import / `YTDLP_AVAILABLE`. |
| `views.py` | discord.ui views (`Controls`, `QueuePageView`, `AplPageView`, `SearchView`) + `_apl_single_embed`. |
| `player.py` | `GuildPlayer`: per-guild queue, player loop, predownload, source building, Now Playing card. Calls `self.cog.source.*` for extraction/download. |
| `cog.py` | The `Music` cog: command surface, listeners, config accessors, presence/rate-limit/metadata helpers. Instantiates `self.source = MusicSource(self)`. Carries the full module docstring (commands enumerated for `test_docs_freshness`). |

The static scanners `test_no_duplicate_commands.py` and `test_docs_freshness.py` walk `cogs/` recursively so package submodules are covered.

### Admin package layout

`cogs/admin/` is command-heavy, so every command stays in one `Admin` class (no command moved); only the supporting code is extracted. `load_extension("cogs.admin")` works via `setup()` in `__init__.py`.

| Module | Holds |
|---|---|
| `constants.py` | `_REPO_ROOT` (pinned subprocess cwd — **two** `dirname` levels up now the file lives in `cogs/admin/`), `_ALL_COGS` (the managed-cog list; keep in sync with `main.py`'s copy), `_VALID_LEVELS`. |
| `helpers.py` | `_git_pull` (the git-pull subprocess wrapper). |
| `views.py` | `ServersView` paginator for the `servers` command. |
| `config_ops.py` | `ConfigMixin`: the DM-only `config` show/get/set operations (`_resolve_key`, `_display`, `_config_show/get/set`). `Admin` inherits it. |
| `cog.py` | `Admin(ConfigMixin, commands.Cog)`: the full command surface. |

### Utility package layout

`cogs/utility/` splits the help engine and source-viewer out of the cog. `load_extension("cogs.utility")` works via `setup()` in `__init__.py`.

| Module | Holds |
|---|---|
| `help_engine.py` | The `/help` engine: `_CATEGORY_ORDER`, the static `_SLASH_GROUPS` metadata (for pure-slash groups like automod/roles/auditlog that can't carry `extras`), category collection/lookup, embed builders, and the paginated `HelpView`. Walks `bot.commands` at call-time so help never goes stale. `test_docs_freshness` reads this file for the AutoMod-help coverage check. |
| `source.py` | Helpers for the `source` command: GitHub URL building (`_gh_url`), related-callable discovery, codebase symbol search. |
| `cog.py` | The `Utility` cog: the full command surface (`help`, `prefix`, `ping`, `server`, `user`, `source`, …). |

### Moderation package layout

`cogs/moderation/` is a command-heavy cog, so the split keeps every command in one `Moderation` class (no command moved) and extracts only the supporting code. `load_extension("cogs.moderation")` works via `setup()` in `__init__.py`.

| Module | Holds |
|---|---|
| `helpers.py` | Stateless module functions: `resolve_target`, `try_dm`, `can_target`, `can_bot_target`, `action_log`, `_chunked_sleep`. Commands call them as bare names. |
| `views.py` | `NukeConfirm` confirm/cancel view for `/nuke`. |
| `schedules.py` | `TimedActionsMixin`: auto-unban / auto-unslow scheduling + restore, persisted in SQLite. `Moderation` inherits it; the task dicts are created in `Moderation.__init__` and restore is driven by the cog's `on_restore_schedules` listener. |
| `cog.py` | `Moderation(TimedActionsMixin, commands.Cog)`: `__init__`/`cog_unload`/`on_restore_schedules` + the full command surface. |

### Fun package layout

`cogs/fun/` is the other cog split into a package (was a single 2.2k-line file). `load_extension("cogs.fun")` works via `setup()` in `__init__.py`. Most of the fun cog's logic already lived in module-level functions (not cog methods), so the split is a straight move — no call-site rewriting. Submodules, in dependency order:

| Module | Holds |
|---|---|
| `constants.py` | URLs, colours, tags, Groq/Kaggle/WYR constants, scraper defaults, compiled regexes. |
| `actions.py` | Static data tables: `_SOCIAL_ACTIONS`, `_REACT_ACTIONS`, 8-ball pools, RPS constants, and the derived `_ALL_NEKOS_ENDPOINTS`. |
| `helpers.py` | Pure helpers (`_ship_score`/`_ship_name`/`_ship_verdict`, `_split_wyr`, `_parse_duration`, `_scrape_cfg`). |
| `sources.py` | Network layer: nekos.best / Nekosia fetches, FML/WYR scrapers, the Kaggle seed, Groq WYR generation; all cached via `cache_db`. `cogs/images.py` imports `_get_nekos_image` from here. |
| `views.py` | `WyrView` (Would-You-Rather voting) + `RpsView` (Rock-Paper-Scissors). |
| `cog.py` | The `Fun` cog: slash `/fun` group, dynamically-registered prefix commands, daily scrape + revalidate loops. (`test_docs_freshness` exempts `fun/cog.py` since its prefix commands are generated at runtime.) |

### Economy package layout

`cogs/economy/` was a single 1.6k-line file. Like the moderation/admin splits, every command stays in one `Economy` class (no command moved) — only supporting code is extracted. `load_extension("cogs.economy")` works via `setup()` in `__init__.py`, which also re-exports the flat public API (`from cogs.economy import fmt_coins, compute_daily, resolve_gamble, Economy, _DEFAULT_SHOP_ITEMS, …`) so callers and tests keep importing from `cogs.economy` unchanged.

| Module | Holds |
|---|---|
| `constants.py` | Cooldowns/windows, gamble odds, `COIN_MAX`, co-op/raid timeouts, `_DEFAULT_SHOP_ITEMS` starter catalogue, `_RANK_TITLES`. |
| `helpers.py` | Pure, Discord-free helpers (`fmt_coins`, `compute_daily`, `resolve_gamble`, `_rank_title`, `_scaled_price`) — covered by `tests/test_economy_helpers.py`. |
| `views.py` | `SquadView` (/squad multi-teammate co-op confirm) + `SquadBuilderView` (/squad UserSelect picker when no one is tagged) + `RaidView` (/raid join board); `SquadView`/`RaidView` are persistent (per-id `custom_id`s) and restart-safe (the builder stays transient), while all call back into the cog via a `TYPE_CHECKING` `Economy` hint. |
| `cog.py` | The `Economy` cog: full command surface (`/balance`, `/daily`, `/pay`, `/squad`, `/raid`, `/coin …`, `/shop …`). Carries the full module docstring (commands enumerated for `test_docs_freshness`). |

### AutoMod package layout

`cogs/automod/` was a single 1.5k-line file. Every command stays in one `AutoMod` class (no command moved); only supporting code is extracted. `load_extension("cogs.automod")` works via `setup()` in `__init__.py`. The in-memory `_spam_tracker` lives in `constants.py` and is imported by reference into both helpers and the cog so they share one tracker.

| Module | Holds |
|---|---|
| `constants.py` | `RULE_LABELS`/`ACTION_LABELS`, `TIMEOUT_SECONDS`, the compiled `_RE_INVITE`/`_RE_URL`, the regex cache + ReDoS guards (`_REDOS_RE`, `_REGEX_TIMEOUT`, `_MAX_REGEX_INPUT`, …), and the `_spam_tracker`. `test_docs_freshness` reads this file for the `RULE_LABELS`/`ACTION_LABELS` ↔ `/help` coverage check. |
| `helpers.py` | Pure rule-check helpers: spam tracking, content matchers (`_has_invite`/`_has_link`/`_caps_percent`/`_has_badword`/…), and the ReDoS-bounded regex matchers (`_matches_regex_safe`, `_all_matches_regex_safe`). |
| `actions.py` | Side-effects: log-channel resolution, the action-log embed, the delete/warn/timeout/kick/softban executor (`_execute_action`), and soft-delete of notices. |
| `autocomplete.py` | The rule/action/regex-pattern app-command autocompletes. |
| `cog.py` | The `AutoMod` cog: the `on_message` listener, the spam-prune loop, and the `/automod` command tree. Carries the full module docstring (commands enumerated for `test_docs_freshness`). |

### Gatekeeper package layout

`cogs/gatekeeper/` was a single 1.3k-line file. Every command stays in one `Gatekeeper` class (no command moved); only supporting code is extracted. `load_extension("cogs.gatekeeper")` works via `setup()` in `__init__.py`, which lifts every name into the package namespace so the flat test imports (`from cogs import gatekeeper as gk; gk._dhash`, `gk._is_safe_public_url`, `gk.Gatekeeper`) keep working.

| Module | Holds |
|---|---|
| `constants.py` | Avatar-catalog dirs (`BUNDLED_AVATAR_DIR`/`AVATAR_CATALOG_DIR`), `_DHASH_THRESHOLD`, `MUTE_ROLE_NAME`, the verify button `custom_id`, the captcha attempt caps (`_MAX_VERIFY_ATTEMPTS`/`_VERIFY_LOCKOUT_SECONDS`), `_MAX_AVATAR_BYTES`, and `DEFAULT_VERIFY_MESSAGE`. |
| `helpers.py` | Pure helpers covered by `tests/test_gatekeeper.py`: the learnavatar SSRF guard (`_is_safe_public_url`) and the perceptual (difference) hash + Hamming distance (`_dhash`/`_hamming`). Owns the Pillow import / `_PILLOW_OK` (and the decompression-bomb pixel cap); `Image` is `None` when Pillow is absent. |
| `views.py` | The verification UI — `VerifyModal` (math captcha) + the persistent-button `VerifyView`; they call back into the cog via a `TYPE_CHECKING` `Gatekeeper` hint. |
| `cog.py` | The `Gatekeeper` cog: join-time `_evaluate`, mute/verify/unmute/kick flow, schedule restore, and the `/gatekeeper` command group. Carries the full module docstring (commands enumerated for `test_docs_freshness`). |

### Roles package layout

`cogs/roles/` was a single 1.2k-line file. Every command stays in one `Roles` class (no command moved); only supporting code is extracted. `load_extension("cogs.roles")` works via `setup()` in `__init__.py`.

| Module | Holds |
|---|---|
| `constants.py` | The autogen palettes (`COLOUR_PALETTE`/`PRONOUN_PALETTE`/`AGE_PALETTE`/`REGION_PALETTE`) and `_AUTOGEN_CFG` (kind → title/desc/mode/palette). |
| `helpers.py` | Per-guild autogen concurrency locks (`_get_autogen_lock`), the short id generator (`_new_id`), and the persistent-button custom_id encode/decode (`_encode_cid`/`_decode_cid`). |
| `views.py` | The persistent `RoleButton` (self-assign/-remove + single-mode swap) plus the `_build_view`/`_build_embed` factories. |
| `autogen.py` | The `_panel_autocomplete` and the shared `_run_autogen` engine behind the four `/roles autogen` commands (calls back into the cog via a `TYPE_CHECKING` `Roles` hint). |
| `cog.py` | The `Roles` cog: the `/roles panel …`, `/roles add|remove`, and `/roles autogen …` command surface + persistent-view restore. Carries the full module docstring (commands enumerated for `test_docs_freshness`). |

### Birthday package layout

`cogs/birthday/` was a single 1.2k-line file. Every command stays in one `Birthday` class (no command moved); only supporting code is extracted. `load_extension("cogs.birthday")` works via `setup()` in `__init__.py`, which lifts every name into the package namespace so the flat test imports (`from cogs.birthday import parse_birthday, _TZ_CHOICES, _ffmpeg_song_cmd, …`) keep working.

| Module | Holds |
|---|---|
| `constants.py` | `BIRTHDAY_COLOR`, the default announcement message + `_VARS_HELP`, the `_BIRTHDAY_GIFS` pool, the `_TZ_CHOICES` curated-zone list (used by the `/birthday timezone` autocomplete) + `_REGION_TZ` map, the month tables (`_MONTH_NAMES`/`_MONTHS`/`_MAX_DAY`), and the `_HB_NOTES` melody + `_SONG_DIR`/`_SONG_PATH`. |
| `helpers.py` | Pure helpers covered by `tests/test_birthday_helpers.py`: date parse/format/countdown (`parse_birthday`, `fmt_birthday`, `next_birthday_date`, `days_until_birthday`, `is_birthday_today`, `age_on`, `_is_leap`), the voice-region→timezone guesser (`guess_timezone_from_regions`), and the FFmpeg song-command builder (`_ffmpeg_song_cmd`). |
| `cog.py` | The `Birthday` cog: self-register/setup commands (incl. the `/birthday timezone` search-as-you-type autocomplete), the 15-minute announce loop, and the `on_voice_state_update` song trigger. Carries the full module docstring (commands enumerated for `test_docs_freshness`). |

### Tickets package layout

`cogs/tickets/` was born as a package. Every command lives in one `Tickets` class; supporting code is split out. `load_extension("cogs.tickets")` works via `setup()` in `__init__.py`, which lifts every name into the package namespace so flat imports (`from cogs.tickets import thread_name`) work.

| Module | Holds |
|---|---|
| `constants.py` | The static view custom_ids (`ticket:panel:open`, `ticket:thread:close|claim`), the modal field caps, the transcript message limit, the 7-day thread auto-archive duration, `DEFAULT_MAX_OPEN`, and the default panel title/message. |
| `helpers.py` | Pure helpers covered by `tests/test_tickets_helpers.py`: `thread_name` (sanitised, 100-char-capped `ticket-NNNN-username`) and `transcript_line`. |
| `views.py` | `TicketModal` (subject + optional details), the persistent `TicketPanelView` (Open Ticket button) and `TicketThreadView` (Close/Claim); all call back into the cog via a `TYPE_CHECKING` `Tickets` hint. |
| `cog.py` | The `Tickets` cog: the open/close/claim flows, transcript builder, log-channel poster, the `on_raw_thread_delete`/`on_raw_member_remove` listeners, and the `/ticket` command group. Carries the full module docstring (commands enumerated for `test_docs_freshness`). |

### Data Layer

Two SQLite databases, both opened once at startup via `setup_hook()` and shared as module-level connections:

- **`data/nanobot.db`** — All persistent bot data. Managed by the `utils/db/` package (see "Data layer package layout" below). Tables include: `tags`, `notes`, `prefixes`, `unban_schedules`, `slow_schedules`, `reminders`, `automod_config`, `automod_badwords`, `automod_regex_patterns`, `automod_attachment_words`, warnings, auditlog settings, role panels, welcome/leave config, recurring reminders, vote history, the music tables (`music_settings`, `music_queue`, `music_history`, `music_autoplaylist`, `music_song_blocklist`, `music_user_blocklist`), the leveling tables (`user_levels`, `level_config`, `level_rewards`, `level_ignored_channels`), the economy tables (`economy` — now also carries the lifetime `contribution` stat, `economy_config`, plus the shop tables `shop_items` and `shop_purchases`, and the persisted co-op tables `economy_raids` + `economy_squads`), the gatekeeper tables (`gatekeeper_config`, `gatekeeper_pending`), the birthday tables (`birthdays`, `birthday_config`), and the ticket tables (`tickets`, `ticket_config`).
- **`data/cache.db`** — External content cache (anime images, stories). Managed by `utils/cache_db.py`.

Both use WAL mode (`PRAGMA journal_mode=WAL`) for concurrent read/write. All queries are async via `aiosqlite`. Initialize with `await db.init()` and `await cache_db.init()` in `NanoBot.setup_hook()` (both take the optional SQLCipher key).

**Data layer package layout:** `utils/db/` was a single 3.6k-line module, split by domain. The public surface is unchanged — `from utils import db` then `db.get_tag(...)`, `db.init()`, `db._conn()`, even the private `db._ensure_*` helpers tests call directly, all still resolve flat. `_core.py` owns the single shared connection (`_db`, `_conn`, the slow-query wrapper, `_ensure_columns`, the `@migration`/`_run_migrations` registry, and `init()`/`close()`); each domain module (`tags`, `notes`, `prefixes`, `schedules`, `reminders`, `warnings`, `welcome`, `votes`, `recurring`, `roles`, `auditlog`, `automod`, `music`, `leveling`, `economy`, `gatekeeper`, `birthday`, `liverole`, `tickets`) holds its own accessors and registers its table-setup coroutine with `_core` via `register_init`. `__init__.py` imports the domains in the original table-creation order (so `init()` builds tables and runs the interleaved one-time migrations identically), lifts every name — public and private — into the package namespace, and routes the package's `_db` attribute to `_core._db` so the long-standing `monkeypatch.setattr(db, "_db", conn)` test-injection pattern still works. Add a new table by creating/extending a domain module, giving it an `_ensure_*` registered with `register_init`, and adding the module to `_DOMAIN_ORDER`; non-trivial changes to existing tables still go through `@db.migration(N)`.

**Encryption at rest (optional):** when `db_encryption_key` (config) or `NANOBOT_DB_KEY` (env, wins) is set, `utils/db_crypto.py` opens both files through the `sqlcipher3` driver instead of stdlib sqlite3 — install `sqlcipher3-binary`. A plaintext DB is auto-migrated on the first keyed start (`sqlcipher_export`, plus a manual `PRAGMA user_version` copy since the header isn't exported; original kept as `*.plain.bak`). sqlcipher3 raises its own exception classes, so constraint handlers catch `db_crypto.INTEGRITY_ERRORS` — never `aiosqlite.IntegrityError` directly. Wrong key / encrypted-file-without-key fail fast at startup with clear `RuntimeError`s. Key changes don't re-key an existing DB.

**Schema changes:** the `CREATE TABLE IF NOT EXISTS` / `_ensure_columns()` calls in `db.init()` are the version-0 baseline (idempotent on every start). Adding a column to an existing table uses `_ensure_columns(table, {col: definition})`. For anything more involved, register a forward-only migration with `@db.migration(N)` — these run in ascending order on startup, tracked by `PRAGMA user_version`, which advances only after a migration succeeds (so a failure retries next start; write migrations to be safe to re-run). Don't scatter ad-hoc `ALTER TABLE` blocks through `init()`.

### Utilities (`utils/`)

- **`helpers.py`** — Embed factory (`ok()`, `err()`, `warn()`, `info()` with consistent brand colors), duration parsing (`parse_duration`, `parse_duration_from_end`, `parse_interval`), and `user_display()` for consistent user references.
- **`converters.py`** — Shared command-argument converters. `SafeTextChannel`: drop-in replacement for a `discord.TextChannel` parameter annotation (slash/hybrid/prefix) that survives a guild-cache miss — the built-in transform raises `TransformerError` when the picked channel isn't cached (e.g. created while the gateway was down), aborting the command; this one falls back to an HTTP fetch and always hands the command a real `discord.TextChannel`. Every command-facing text-channel parameter across the cogs uses it; use it for new ones too.
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

* **`[bot]`** — `token`, `db_encryption_key`, `default_prefix`, `owner_id`, `error_channel_id`, `idle_status_message`, `health_check_port`, `health_check_host`
* **`[logging]`** — `log_level`, `log_http`, `log_events_jsonl`, `db_slow_query_ms`
* **`[votes]`** — `topgg_v1_token`, `dbl_token`, `discordbotsgg_token`, `vote_webhook_port`, `vote_webhook_host`, `vote_webhook_secret`, `webhook_allowed_ips`
* **`[groq]`** — `groq_api_key`
* **`[scraper]`** — `fml_pages_per_scrape`, `wyr_requests_per_scrape`, `nekos_per_endpoint`, `nekosia_per_tag`, `revalidate_age`, `revalidate_batch`, `groq_wyr_system`
* **`[music]`** — playback/queue knobs read live from `bot.config` so `!reloadconfig` applies without a cog reload: `music_cookie_file`, `music_default_volume`, `music_idle_timeout`, `music_skip_ratio`, `music_max_queue`, `music_use_opus`, `music_persist_queue`, `music_predownload`, `music_self_deafen`, `music_default_speed`, `music_search_service` (ytsearch/ytmsearch/scsearch), `music_status_message` ({title} presence template), `music_proxy`, `music_user_agent`, `music_source_address`, `music_request_throttle` (seconds between yt-dlp HTTP requests; 0 = off), `music_js_runtime_path` (deno/node/bun binary for yt-dlp JS challenges), `music_autoplay_autoskip`, `music_save_videos` + `music_cache_max_mb`/`music_cache_max_age_days` (cache caps), `music_ratelimit_cooldown`/`music_ratelimit_leave` (429 back-off), `music_apl_prune_on_error`, `music_save_history`, `music_metadata_lookup` (iTunes Search artist enrichment), `music_sponsorblock` + `music_sponsorblock_categories` (skip non-music/sponsor segments via SponsorBlock — downloads + FFmpeg-cuts the track before play; off by default, default category `music_offtopic`).

All keys are optional except `token` (or the `DISCORD_TOKEN` env var). An old `config.json` is auto-migrated to `config.ini` on first start (the legacy file is renamed to `config.json.bak`).

The bot keeps the whole flat config on `bot.config`. Cogs that need live values (fun.py scraper knobs, for example) read from `bot.config` on every use so `!reloadconfig` takes effect without a cog reload. Values captured at `__init__` time (e.g. votes.py webhook settings) still need `!reload votes` to change.

Live editing:
* `!setloglevel <level>` — changes the log level and persists to `config.ini`.
* `!reloadconfig` — re-reads `config.ini` from disk.
* `!config show|get|set|unset …` — DM-only inspect/edit. Secrets (token, API keys, webhook secret) are always masked when echoed back.

On startup the active config is printed **last** (end of `on_ready`, once) so it isn't buried under cog-load/gateway output. It goes to stdout (not the logger) and is **unmasked** — terminal-only for the host operator; routing it through the logger would copy secrets into the rotating `logs/nanobot.log` (and expose them via `!logs`). `!config show`/`get` stay masked. Both paths share `config.summary()` / `config.mask_value()` (which take a `mask` flag).

Logs rotate at 50 KB, 5 backups, written to `logs/nanobot.log`. Each line carries a short correlation id (`[abcd1234]`) shared by the start/completion/error records of one command invocation, so a single command's trace is greppable. When `log_events_jsonl` is on (default), structured command-lifecycle events (`command.start/ok/err`, `slash.start/ok/err` with `dur_ms`, user, guild) are also written to `logs/events.jsonl` (one JSON object per line, rotating). Correlation/timing helpers live in `utils/obs.py`.

### CI

GitHub Actions runs two workflows on every push:
- **`black.yml`** — Auto-formats code with Black. If formatting is needed, it auto-commits with `[skip ci]`. Run `black .` locally before pushing to avoid the auto-commit noise.
- **`tests.yml`** — Runs the pytest suite (`pytest tests/ -v`). Installs `requirements.dev.txt` (a superset of `requirements.txt`) before running.

A third workflow, **`branch-protection.yml`**, is not part of the per-push CI: it runs only via `workflow_dispatch` or when pushed to `main` touching that file, applying `main` branch protection (required `test` + `black` checks, 1 review, no force-push) via the GitHub API.
