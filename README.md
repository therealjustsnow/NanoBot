[![Tests](https://github.com/therealjustsnow/NanoBot/actions/workflows/tests.yml/badge.svg)](https://github.com/therealjustsnow/NanoBot/actions/workflows/tests.yml) [![Black Formatter](https://github.com/therealjustsnow/NanoBot/actions/workflows/black.yml/badge.svg)](https://github.com/therealjustsnow/NanoBot/actions/workflows/black.yml)

# ⚡ NanoBot

> **Small. Fast. Built for Mobile Mods.**

NanoBot is a lightweight Discord moderation and utility bot built for one specific goal: help moderators on their phone get things done fast. No bloated dashboards. No confusing panels. Just clean, fast commands that work.

**[💬 Support Server](https://discord.gg/M7fjxNg72s)** 

---

## Why NanoBot?

If you moderate from mobile, you already know:

- Banning someone while scrolling is annoying
- Slowmode adjustments take too many taps
- You can't always grab IDs easily
- Cleaning messages is clunky
- Most bots assume you're on desktop

NanoBot fixes that.

---

## Features

**Moderation**
- Slash commands + prefix commands + @mention -- your choice
- "Last sender" targeting -- no need to copy IDs on mobile
- Timed bans with auto-unban -- survives bot restarts
- Timed slowmode with auto-disable
- Channel lock, hide/unhide, nuke, echo, and voice moveall
- Per-server warning system with configurable auto-kick/ban thresholds
- Mod notes -- private, invisible to users, SQLite-backed
- Bulk message purge with filters (bots, user, contains, starts/ends with)

**AutoMod**
- Eight passive rules: spam, invites, links, caps, mentions, bad words, regex, word + attachment
- Regex pattern matching with test-before-you-save
- Five actions per rule: delete, warn, timeout, kick, softban
- Per-server exempt channels and roles

**Audit Log**
- Live feed of 12 server event types to a configurable channel
- Multi-select dropdown to toggle exactly the events you care about

**Role Panels**
- Button-based self-assign panels -- one tap on mobile
- Toggle mode (add/remove) or single-choice mode (radio-style)
- Persistent across bot restarts
- Autogen templates for colors, pronouns, age ranges, and regions

**Tags**
- Personal and global text snippets (up to 2000 chars) with optional images
- One-tap shortcuts: `n!tagname` fires any tag directly
- Import/export as JSON for backup or migration

**Reminders**
- One-time and recurring reminders with natural duration parsing
- Recurring reminders support pause, resume, and cancel
- All survive bot restarts via SQLite

**Welcome & Leave**
- Per-server welcome and leave messages with template variables
- Embed support with images

**Fun & Images**
- 26 social interaction commands + 33 solo reaction commands
- Ship calculator, magic 8-ball
- GIFs from nekos.best (no API key needed)
- Random anime image commands: husbando, kitsune, neko, waifu

**Music**
- Stream from YouTube and 1000+ other sites via yt-dlp -- paste a URL or just search
- Spotify track / album / playlist links (no API key -- metadata is scraped, then matched on YouTube)
- Interactive Now Playing card: play/pause, skip, stop, loop, shuffle, replay, autoplay, queue buttons (mobile-first)
- Live progress bar that updates as the track plays
- Search picker, `playnext`, `playnow`, `stream`, and `shuffleplay` for fast queue control
- `follow` -- the bot tracks you between voice channels; `pldump` exports the queue
- Per-server queue with playlist support, shuffle, move, jump, remove, and clear
  (playback is flat -- `/play`, `/skip`, `/queue`, `/volume`; queue editing and the
  server library live under `/music`, and every prefix name stays flat)
- Democratic vote-skip (requester / Manage Server force-skip) with a configurable ratio
- Loop modes (off / track / queue), volume 0-200%, playback speed, and seek
- Audio effects: bassboost, nightcore, vaporwave, treble, 8D, muffle
- Lyrics lookup, grab-to-DM, and a persistent per-server autoplaylist with autoplay
- Optional yt-dlp cookies support for age/region-locked or rate-limited sources
- Auto-disconnect when idle or left alone in the channel

**Leveling**
- Per-server message XP and levels with a Mee6-style curve
- `/rank` card and a `/level top` leaderboard
- Role rewards granted automatically on level-up
- Optional NanoCoin payout on level-up (ties into the economy)
- Per-channel XP ignore list, configurable XP rate, and level-up announcements
- Off by default -- enable per server with `/level toggle`

**Economy**
- Per-server NanoCoin currency (name and emoji are configurable)
- `/balance` (a rendered wallet card), `/daily` (24h cooldown with a consecutive-day streak bonus), and `/pay`
- A cosmetic shop (`/shop profile`, `/shop wallet`) that turns coins into profile- and wallet-card looks
- `/coin top` rich list and `/coin gamble` double-or-nothing
- Admin grant / take / reset and per-server daily-amount + streak-bonus tuning

**Gatekeeper**
- New-account gate: on join, mutes accounts that are too young, have the default avatar, or use a known "stock" avatar (matched by perceptual hash)
- Combine the age and avatar checks with `or` (either) or `and` (both) match modes
- Muted members get a math-captcha verification prompt (DM first, quarantine channel fallback)
- Account-age mutes auto-unmute once the account ages out; unverified members are auto-kicked after a timeout
- Schedules persist in SQLite and restore on restart

**Bot Lists**
- top.gg, discordbotlist.com, discord.bots.gg integration
- Vote tracking with per-user history
- Automatic stat posting on a 12-hour loop

**Infrastructure**
- SQLite storage -- single portable file, zero cloud dependency
- Owner-only admin: hot-reload, restart, git pull update, slash sync
- Per-server custom prefix
- Configurable log level (no restart needed)
- Optional HTTP health-check endpoint (`GET /health`) for containers/orchestrators
- GitHub Actions CI: Black auto-formatting, pytest suite, branch protection

**Web Dashboard** (optional, off by default — see [docs/dashboard.md](docs/dashboard.md))
- Sign in with Discord; the gate is Manage Server, exactly as it is in Discord
- Configure AutoMod with a plain-language rule builder, not a JSON field
- Design welcome and leave messages against a live preview
- Set up tickets, birthdays, the new-account gate and music without leaving the page
- A permission checklist per feature, so a missing permission is visible before
  it breaks something at 3am
- Moderate with the safety rails on — role hierarchy checked, a reason required,
  every destructive action confirmed, and each one written to the audit log
- Play the whole economy from a phone — fishing, mining, adventuring, the
  casino, crafting, the inventory, the shop — through the *same* cooldowns and
  claims Discord uses, so nobody gets two of anything
- Dress your profile card against a live preview of the card itself
- Mobile-first, dark and light, no build step, rides the same port as `/health`
- Hostable as a static site (GitHub Pages, a CDN) against a remote API with no
  code changes — see [docs/deployment.md](docs/deployment.md)

---

## Setup

### 1. Prerequisites

- Python 3.11+
- A Discord bot application ([discord.com/developers](https://discord.com/developers/applications))
- **FFmpeg** on the host's PATH (required only for the Music cog) -- e.g. `apt install ffmpeg` / `brew install ffmpeg`

### 2. Install

**Scripted (recommended)** — installs system dependencies (Git, FFmpeg, Python 3.11+),
creates a `./venv` virtual environment, installs Python requirements, and creates
`config.ini` from the example:

```bash
# Linux / macOS
git clone https://github.com/therealjustsnow/NanoBot.git
cd NanoBot
./install.sh              # add --no-system to skip apt/dnf/pacman/zypper/brew

# Windows — double-click install.bat, or from PowerShell:
.\install.ps1             # add -NoSystem to skip the WinGet installs
```

Both are safe to re-run; existing venvs and an existing `config.ini` are left untouched.

**Manual:**

```bash
git clone https://github.com/therealjustsnow/NanoBot.git
cd NanoBot
pip install -r requirements.txt
```

### 3. Config

The install scripts create `config.ini` for you; otherwise copy `example_config.ini`
to `config.ini`. Then fill in your values:

```ini
[bot]
token = YOUR_BOT_TOKEN_HERE
default_prefix = n!
owner_id =

[logging]
log_level = INFO
log_http = false

[votes]
topgg_token =
topgg_v1_token =
dbl_token =
discordbotsgg_token =
vote_webhook_port = 5000
vote_webhook_secret =

[groq]
groq_api_key =

[scraper]
fml_pages_per_scrape = 500
wyr_requests_per_scrape = 500
nekos_per_endpoint = 400
nekosia_per_tag = 400
revalidate_age = 604800
revalidate_batch = 1000
groq_wyr_system = You generate Would You Rather questions for a Discord bot. ...
```

| Section | Key | Required | Description |
|---------|-----|----------|-------------|
| `bot` | `token` | **Yes** | Bot token from the Developer Portal |
| `bot` | `default_prefix` | No | Default prefix for all servers (changeable per-server with `/prefix`). Default `n!` |
| `bot` | `owner_id` | No | Your Discord user ID -- overrides app owner for admin commands. Blank = use app owner |
| `logging` | `log_level` | No | `DEBUG` / `INFO` / `WARNING` / `ERROR`. Changeable live with `!setloglevel` |
| `logging` | `log_http` | No | `true` to log every raw HTTP request (very verbose, for debugging) |
| `votes` | `topgg_token` | No | top.gg API token -- enables stat posting and vote webhooks |
| `votes` | `topgg_v1_token` | No | top.gg v1 API token -- enables command sync to top.gg |
| `votes` | `dbl_token` | No | discordbotlist.com bot token -- enables stat posting, command sync, and vote webhooks |
| `votes` | `discordbotsgg_token` | No | discord.bots.gg bot token -- enables stat posting (the site has no voting or vote-webhook API) |
| `votes` | `vote_webhook_port` | No | Port for the vote webhook HTTP server. Default `5000` |
| `votes` | `vote_webhook_secret` | No | Shared secret for webhook verification |
| `groq` | `groq_api_key` | No | Groq API key for daily WYR generation. Free at [console.groq.com](https://console.groq.com). Also accepts `GROQ_API_KEY` env var |
| `scraper` | `fml_pages_per_scrape` | No | FML pages fetched per daily scrape. Default `500` |
| `scraper` | `wyr_requests_per_scrape` | No | WYR requests per rating per daily scrape. Default `500` |
| `scraper` | `nekos_per_endpoint` | No | nekos.best images per endpoint per daily scrape. Default `400` |
| `scraper` | `nekosia_per_tag` | No | Nekosia images per tag per daily scrape. Default `400` |
| `scraper` | `revalidate_age` | No | Seconds before a cached URL is rechecked. Default `604800` (7 days) |
| `scraper` | `revalidate_batch` | No | Max URLs HEAD-checked per 6-hour cycle. Default `1000` |
| `scraper` | `groq_wyr_system` | No | System prompt used when Groq generates WYR questions |
| `dashboard` | `dashboard_port` | No | Port for the web dashboard. `0` = disabled (the default) |
| `dashboard` | `dashboard_host` | No | Bind address. Default `0.0.0.0`; use `127.0.0.1` behind a reverse proxy |
| `dashboard` | `dashboard_base_url` | No | Public URL this API is reached at, e.g. `https://nano.example.com`. The OAuth redirect is built from it |
| `dashboard` | `dashboard_frontend_url` | No | Where the browser app is served from, if not by the bot (e.g. a GitHub Pages URL). Blank = the bot serves it |
| `dashboard` | `dashboard_client_id` | No | OAuth2 client id. Blank = the bot's own application id |
| `dashboard` | `dashboard_client_secret` | No | OAuth2 client secret. Required for anyone to sign in |
| `dashboard` | `dashboard_session_secret` | No | Signs session cookies. Blank = a random one per start, which signs everyone out on every restart |
| `dashboard` | `dashboard_session_days` | No | How long a sign-in lasts. Default `7` |
| `dashboard` | `dashboard_play_enabled` | No | Whether the economy is playable from the browser. `false` = read-only for members. Default `true` |
| `dashboard` | `dashboard_allowed_origins` | No | Origins allowed to call the API cross-origin, when the frontend is hosted elsewhere. Blank (the default) = same-origin only. See [docs/deployment.md](docs/deployment.md) |

> Migrating from an older version? An existing `config.json` is auto-migrated to
> `config.ini` on first start and renamed to `config.json.bak`.

**Configuring by environment instead:** every key above can be set as
`NANOBOT_<KEY>` (`NANOBOT_DASHBOARD_PORT`, `NANOBOT_LOG_LEVEL`, …), which is the
easier route on a container or a PaaS. It is an overlay on top of `config.ini`,
not a replacement: `!config set` still reads and writes the file, so the two
never overwrite each other, and the bot reports which keys the environment is
overriding at startup. `DISCORD_TOKEN` and `NANOBOT_DB_KEY` keep their own names.
See [`.env.example`](.env.example).

**Live editing without a restart:**

* `!reloadconfig` — re-read `config.ini` from disk.
* `!config show` / `!config get <section>.<key>` / `!config set <section>.<key> <value>` — **DM-only** commands to inspect or edit values right from Discord.
* `!setloglevel DEBUG` — change log level and save to `config.ini` in one shot.

> ⚠️ **Never commit `config.ini` to git.** It's already in `.gitignore`.

Token via environment variable also works:
```bash
export DISCORD_TOKEN=your_token_here
```

### 4. Discord Developer Portal

Enable these **Privileged Gateway Intents** in your app's Bot settings:
- ✅ **Server Members Intent**
- ✅ **Message Content Intent**
- ✅ **Presence Intent**

Without these, prefix commands and most mod commands will silently fail, and the
`/user` card will show everyone as offline.

### 5. Migrating from JSON storage

If you're upgrading from an older version that used JSON files for data, run the migration script once before starting:

```bash
python migrate.py
```

This imports all existing JSON data into SQLite. Your JSON files are left untouched as a backup. Safe to run multiple times.

### 6. Run

```bash
# Recommended -- finds Python (prefers ./venv), pre-flight check, then launch
./run.sh         # Linux / macOS
run.bat          # Windows (double-click works too)

# Same thing, calling Python yourself
python run.py

# Or launch directly, skipping the pre-flight check
python main.py
```

Both launchers pass arguments through, so `./run.sh --check` runs the pre-flight
check without starting the bot.

Logs are written to `logs/nanobot.log` (rotating, max 50 KB x 5 files).

---

## Commands

All commands work as slash commands (`/`), prefix commands (default `n!`), and @mention unless noted otherwise.

Most commands that take a `user` argument will automatically **target the last person who sent a message** in the channel if left blank -- ideal for mobile where copying IDs is a pain.

Use `/help` for the full paginated reference, `/help <command>` for detail on any command, or `/help <category>` to browse a section.

---

### 🔧 Admin *(owner only)*

Restricted to the bot owner. These are **prefix-only by design** -- slash commands appear in the `/` menu for every user in the server, which would expose admin controls publicly.

| Command | Description |
|---------|-------------|
| `!reload [cog\|all]` | Hot-reload a cog without restarting |
| `!unload <cog>` | Unload a single cog without restarting |
| `!restart` | Gracefully close and re-execute the process |
| `!shutdown` | Flush logs and close cleanly |
| `!update` | `git pull` + reload all cogs. Does NOT sync slash commands |
| `!upgrade` | `git pull` + `pip install` + spawn new process + close |
| `!sync [guild_id]` | Push slash commands to Discord (global or one guild) |
| `!setloglevel <level>` | Change log verbosity live (`DEBUG` / `INFO` / `WARNING` / `ERROR`) |
| `!reloadconfig` | Re-read `config.ini` from disk without restarting |
| `!config show\|get\|set\|unset` | DM-only: inspect / edit config values (secrets masked) |
| `!logs [lines]` | Tail `logs/nanobot.log` in Discord -- default 20, max 50 lines |
| `!servers` | List all servers the bot is in |
| `!scrape` | Manually trigger the daily content-cache scrape |
| `!cachestats` | Show cache DB statistics (FML, WYR, images) |
| `!fmlpurge` | Wipe all cached FML stories (forces re-scrape) |

---

### 🔨 Banning

| Command | Description |
|---------|-------------|
| `/ban [user] [message]` | Permanent ban with optional DM. Targets last sender if no user given. |
| `/cban [user] [days] [wait] [message]` | Clean ban -- deletes message history, optional timed unban, optional DM |
| `/tempban [user] [duration] [reason]` | Quick timed ban with auto-unban. Defaults to 24h. Survives restarts. |
| `/massban <id1 id2 ...> [reason]` | Ban up to 50 users by ID at once. Useful after a raid. |
| `/unban <user_id> [reason]` | Unban by Discord User ID |

---

### 👢 Kicking & Timeouts

| Command | Description |
|---------|-------------|
| `/kick [user] [message]` | Kick with optional DM. Targets last sender if no user given. |
| `/freeze [user] [duration] [reason]` | Discord Timeout -- can't speak, react, or join VCs. Default 10m, max 28d. |
| `/unfreeze <user>` | Remove a timeout before it expires. |

---

### 📢 Channel Controls

| Command | Description |
|---------|-------------|
| `/lock [channel] [reason]` | Toggle @everyone send permissions. Run again to unlock. |
| `/hide [channel]` | Hide a channel from @everyone (`view_channel = false`). |
| `/unhide [channel]` | Restore @everyone visibility on a hidden channel. |
| `/slow [delay] [length]` | Set slowmode (`30s`-`5m`) with optional timed auto-disable. No args = toggle. |
| `/purge <amount>` | Delete messages. Filters: `only` (anyone/humans/bots/nanobot), `user`, `contains`, `starts_with`, `ends_with`, and `mode`. |
| `/purge <amount> mode:slow` | One-by-one delete (1-500). No 14-day limit. Asks for a confirmation code. Prefix shorthand: `!snailpurge` |
| `/purge <amount> only:nanobot` | Delete NanoBot's own recent messages. Prefix shorthand: `!clean` |
| `/echo [channel] <message>` | Send a message as NanoBot. Prefix mode auto-deletes your trigger. |
| `/nuke [reason]` | Clone channel + delete original -- wipes all history. Button confirmation required. **Irreversible.** |
| `/moveall <to> [from]` | Move all members from one voice channel to another. Defaults to your current VC. |

---

### 🎭 Quick Roles

| Command | Description |
|---------|-------------|
| `/addrole <user> <role>` | Assign a role. Role must be below NanoBot's highest role. |
| `/removerole <user> <role>` | Remove a role from a user. |

---

### ⚠️ Warnings

Slash commands use the `/warn` group. Prefix commands stay flat.

**Slash commands:**

| Command | Description |
|---------|-------------|
| `/warn issue <user> [reason]` | Issue a warning. Configured auto-actions fire at thresholds. |
| `/warn list <user>` | View all warnings for a user (last 5 shown with dates and moderators). |
| `/warn clear <user>` | Permanently wipe all warnings for a user. Admin only. |
| `/warn config [kick_at] [ban_at] [dm_user]` | Configure per-server thresholds. No args shows current config. |

**Prefix commands:**

| Command | Description |
|---------|-------------|
| `!warn <user> [reason]` | Issue a warning |
| `!warnings <user>` | View warnings |
| `!clearwarnings <user>` | Wipe all warnings |
| `!warnconfig [kick_at] [ban_at] [dm_user]` | Configure thresholds |

---

### 🔎 Notes

| Command | Description |
|---------|-------------|
| `/note add <user> <content>` | Add a private mod note. Never visible to the target user. |
| `/note list <user>` | View notes for a user (last 5). Ephemeral. |
| `/note clear <user>` | Wipe all notes for a user. Admin only. |
| `/last` | Show who last sent a message here -- the auto-target for `/kick`, `/ban`, etc. |

---

### 🛡️ AutoMod

Passive rule enforcement. Watches every message and acts without manual intervention. All commands require **Manage Server**.

**Eight individually toggleable rules:**

| Rule | What it catches |
|------|----------------|
| `spam` | X messages from the same user within Y seconds |
| `invites` | Discord invite links (`discord.gg`, `discord.com/invite`) |
| `links` | Any external URL |
| `caps` | Messages above a configurable % uppercase (minimum length guard) |
| `mentions` | Too many @mentions in a single message |
| `badwords` | Per-server word list (case-insensitive substring match) |
| `regex` | Custom regex patterns with test-before-you-save |
| `attachment_word` | A filtered word plus N+ attachments in the same message |

**Five actions per rule (set independently):** `delete`, `warn` (adds a formal warning), `timeout` (10-minute Discord timeout), `kick`, `softban`.

Members with Manage Messages are always exempt. Additional exempt channels and roles can be configured.

| Command | Description |
|---------|-------------|
| `/automod status` | Full config overview -- all rules, actions, exemptions |
| `/automod enable` | Master on switch |
| `/automod disable` | Master off switch |
| `/automod rule <rule> <action>` | Toggle a rule on/off and set its action |
| `/automod spam <count> <seconds>` | Set spam detection threshold |
| `/automod caps [percent] [min_length]` | Set uppercase % threshold and minimum message length |
| `/automod mentions <limit>` | Set per-message @mention limit |
| `/automod timeout <minutes>` | Set how long the timeout action lasts (1–10080) |
| `/automod attachments <count>` | Min attachments that trigger the word + attachment rule |
| `/automod badword add <word>` | Add a word to the filter |
| `/automod badword remove <word>` | Remove a word from the filter |
| `/automod badword list` | List all filtered words (ephemeral) |
| `/automod regex add <pattern> [label]` | Add a regex pattern to the filter |
| `/automod regex remove <pattern>` | Remove a regex pattern |
| `/automod regex list` | List all regex patterns |
| `/automod regex test <pattern> <text>` | Test a pattern against sample text before saving |
| `/automod attachword add <word>` | Add a word to the word + attachment filter |
| `/automod attachword remove <word>` | Remove a word from the word + attachment filter |
| `/automod attachword list` | List all word + attachment filter words (ephemeral) |
| `/automod ignore channel <channel>` | Toggle a channel exemption |
| `/automod ignore role <role>` | Toggle a role exemption |

---

### 📋 Audit Log

Posts a live feed of server events to a configurable channel. Fully opt-in -- nothing fires until you set a channel and enable it. All commands require **Manage Server**.

**Thirteen toggleable event types:** message delete, message edit, member join, member leave, member ban, member unban, nickname change, role update, channel create, channel delete, role create, role delete, AutoMod action.

Event selection uses a multi-select dropdown -- one interaction to enable or silence any combination. Bot events are filtered out.

| Command | Description |
|---------|-------------|
| `/auditlog channel <#channel>` | Set the channel for log entries |
| `/auditlog enable` | Enable the audit log |
| `/auditlog disable` | Disable the audit log |
| `/auditlog events` | Toggle individual event types via dropdown |
| `/auditlog status` | Show full configuration |

---

### 🎭 Role Panels

Button-based self-assignable role panels. One tap on mobile to assign or remove a role. All commands require **Manage Roles**.

**Two panel modes:** `toggle` (add/remove on click) and `single` (radio-style -- selecting a role removes any other role from the same panel).

Panels are **persistent** -- they survive bot restarts via custom IDs encoded into each button.

| Command | Description |
|---------|-------------|
| `/roles panel create` | Create a new panel (title, description, mode) |
| `/roles panel post` | Post or re-post a panel to a channel |
| `/roles panel edit` | Edit a panel's title, description, or mode |
| `/roles panel delete` | Delete a panel and remove its message |
| `/roles panel list` | List all panels in this server |
| `/roles add <panel> <role>` | Add a role to a panel |
| `/roles remove <panel> <role>` | Remove a role from a panel |

**Autogen commands** -- generate a complete set of roles and a ready-to-post panel in one command. Each accepts up to 5 existing roles to append.

| Command | What it generates |
|---------|-------------------|
| `/roles autogen colors` | 18 cosmetic colour roles (single-choice panel) |
| `/roles autogen pronouns` | She/Her, He/Him, They/Them, It/Its, Any/All |
| `/roles autogen age` | Age ranges: 13-17, 18-20, 21-25, 26-30, 31+ |
| `/roles autogen region` | 7 world regions (N. America, Europe, Asia, etc.) |

---

### 👋 Welcome & Leave

| Command | Description |
|---------|-------------|
| `/welcome` | View current welcome config |
| `/welcome set` | Configure welcome messages for new members |
| `/welcome test` | Preview the welcome message as if you just joined |
| `/leave` | View current leave config |
| `/leave set` | Configure leave messages |
| `/leave test` | Preview the leave message |

`/welcome set` and `/leave set` accept: `enabled`, `channel`, `title`, `content`, `image_url`, `dm`.

Template variables in title/content: `{user}`, `{mention}`, `{server}`, `{count}`.

---

### 🏷️ Tags

Saved text snippets (up to 2000 chars) with optional images. Post in channel in one tap.

- **Personal tags** -- only you can create and use them
- **Global tags** -- anyone can use; Manage Messages required to create

#### Slash commands

| Command | Description |
|---------|-------------|
| `/tag list` | List your personal tags and all global tags |
| `/tag create <n> [content] [image]` | Create a personal tag |
| `/tag global <n> [content] [image]` | Create a server-wide global tag *(Manage Messages)* |
| `/tag use <n> [dm_user]` | Post in channel, or DM to a specific user |
| `/tag preview <n>` | Preview a tag -- only you see the response |
| `/tag edit <n> [content] [image]` | Update a tag's content or image |
| `/tag delete <n>` | Delete a tag |
| `/tag export` | Download all your personal tags as a JSON file |
| `/tag import <file>` | Import personal tags from a previously exported JSON file |

#### Prefix shorthands

| Shorthand | Description |
|-----------|-------------|
| `n!tag` | List all tags |
| `n!tag <n>` | Post tag in channel |
| `n!<n>` | Even shorter -- fires any tag directly |
| `n!tag + <n> \| <content>` | Create a personal tag |
| `n!tag - <n>` | Delete a personal tag |
| `n!tag g+ <n> \| <content>` | Create a global tag *(mods only)* |

```
n!tag + rules | Read #rules before posting!
n!rules                     → posts the tag named "rules"
n!tag - rules               → deletes it
```

> Tags over 1500 characters are sent as plain text to stay within Discord's embed limit.

---

### 🔍 Server & User Info

| Command | Description |
|---------|-------------|
| `/server` | Full server info -- members, channels, boost level, features, creation date |
| `/user [user]` | User card -- status, roles, badges, join date, boost, timeout status |
| `/avatar [user]` | Avatar at 1024px with PNG/JPG/WEBP/GIF download links |
| `/info banner [user]` | Profile banner with download links |
| `/info role <role>` | Role color, position, member count, permissions, creation date |
| `/info channel [channel]` | Channel type, ID, category, creation date, NSFW status, slowmode, topic |
| `/info id [target]` | ID of a user, role, or channel, in a copyable code block |
| `/info members` | Quick member count for this server |
| `/info firstmsg [channel]` | Jump link to the oldest message in a channel |

The `/info` lookups all keep their flat prefix names: `!banner`, `!roleinfo`,
`!channelinfo`, `!id`, `!mc` and `!firstmsg` are unchanged.

---

### ⏰ Reminders

**One-time reminders:**

| Command | Description |
|---------|-------------|
| `/remindme <message with duration>` | Set a reminder for yourself. Duration goes at the end. |
| `/reminders user <@user> <message with duration>` | Set a reminder for someone else (prefix: `!remind`) |
| `/reminders list` | List your active reminders |
| `/reminders cancel <number>` | Cancel a reminder by its list number |

```
!remindme stand up 1h
!remindme check that PR 30m
```

**Recurring reminders:**

| Command | Description |
|---------|-------------|
| `/recurring every <interval> <message> [label] [dm]` | Create a recurring reminder that fires repeatedly (prefix: `!every`) |
| `/recurring list` | List your recurring reminders with interval, next fire time, and status |
| `/recurring pause <id>` | Pause a recurring reminder |
| `/recurring resume <id>` | Resume a paused recurring reminder |
| `/recurring cancel <id>` | Permanently delete a recurring reminder |

```
!every 1h drink water
!every 24h standup meeting label:standup
!recurring pause abc123
```

Max 10 recurring reminders per user.

---

### 🎉 Fun

26 social interaction commands and 33 solo reaction commands powered by GIFs from [nekos.best](https://nekos.best). Falls back to text-only if the API is unavailable.

**Slash commands** (one top-level slot with 4 subcommands):

| Command | Description |
|---------|-------------|
| `/fun social <action> [user]` | Social interaction -- autocomplete picker with 26 actions (hug, slap, pat, kiss, etc.) |
| `/fun react <action>` | Solo reaction -- autocomplete picker with 33 actions (cry, dance, laugh, shrug, etc.) |
| `/fun ship <user1> <user2>` | Ship two users with a deterministic compatibility score |
| `/fun 8ball <question>` | Ask the magic 8-ball |

**Prefix commands** are flat -- `!hug @user`, `!cry`, `!ship @user1 @user2`, `!8ball will it rain`.

---

### 🖼️ Images

Random anime images from [nekos.best](https://nekos.best). Includes artist credit and source links when the API provides them.

| Command | Description |
|---------|-------------|
| `/husbando` | Random husbando image |
| `/kitsune` | Random kitsune image |
| `/neko` | Random neko image |
| `/waifu` | Random waifu image |

Also available as prefix: `!husbando`, `!kitsune`, `!neko`, `!waifu`.

---

### 📈 Leveling

Per-server message XP and levels (Mee6-style curve). **Off by default** -- enable it with `/level toggle on`. This is the *server's* progression and stays fully admin-configurable; it sits alongside the **global account level** (hard-coded, earned from normalized actions anywhere) which appears next to it on `/rank` and on `/profile` — see [`docs/identity-and-levels.md`](docs/identity-and-levels.md). Members earn XP per message (rate-limited by a per-member cooldown); reaching a level can grant a role reward and, optionally, NanoCoins.

| Command | Description |
|---------|-------------|
| `/rank [user]` | Show a member's server level and rank, plus their global account level |
| `/level top [page]` | Server XP leaderboard |

**Admin subcommands** *(Manage Server):*

| Command | Description |
|---------|-------------|
| `/level toggle <on/off>` | Turn leveling on or off for the server |
| `/level set <user> <xp>` | Set a member's XP to an exact amount |
| `/level give <user> <xp>` | Add (or subtract) XP for a member |
| `/level reset [user]` | Reset XP for one member, or the whole server |
| `/level rate <xp> <cooldown>` | XP earned per message and the cooldown (seconds) |
| `/level announce <...>` | Configure level-up announcements |
| `/level globalannounce [channel] [on/off]` | Where **global** (account-wide) level-ups are announced in this server — a channel, `off` (members still get a DM), or nothing to follow `/level announce` |
| `/level reward <level> <role>` | Grant a role automatically at a level |
| `/level ignore <channel>` | Toggle a channel as XP-ignored |
| `/level config` | Show the current leveling settings |

---

### 🪙 Economy

**NanoCoin** currency with a **global wallet**: your balance, inventory, and progression belong to your account, not to one server — earn coins fishing in one community and spend them in another. Each server still sets its own currency name and emoji, reward amounts, shop, and which economy features are enabled. Earn coins from `/daily` (and optionally from leveling up), then spend or gamble them.

📖 **New to the economy? Read the [Player Guide](docs/player-guide.md)** — a beginner-friendly tour of fishing, jobs, the casino, crafting, achievements, and the global account, with every command in one place. For the architecture behind the global wallet (and the one-time migration), see [`docs/global-economy.md`](docs/global-economy.md).

| Command | Description |
|---------|-------------|
| `/profile [user]` | Your account **card image** — global level, this server's level, coins, fishing, casino, work, items, achievements, prestige emblem and equipped badges (alias: `/card`) |
| `/profile cosmetics` | Banners, borders, nameplates, badges, wallet banners and coin styles you own — and how to unlock the rest |
| `/profile equip <name>` | Wear a cosmetic on your card (`/profile unequip` to remove) |
| `/profile preview <name>` | Try any cosmetic on your own card before buying — nothing equipped, nothing charged |
| `/profile badges [user]` | The badge gallery |
| `/balance [user]` | Your **wallet card image** — balance, global rank, contribution, daily streak, wearing your wallet banner and coin style |
| `/daily` | Claim the daily reward (24h cooldown, consecutive-day streak bonus) |
| `/pay <user> <amount>` | Transfer coins to another member |
| `/coin top [page] [scope]` | Richest members — this server's members, or every server |
| `/coin gamble <amount>` | Double-or-nothing bet (~45% win) |
| `/shop` | The three aisles: profile cosmetics, wallet cosmetics, and this server's rewards |
| `/shop profile` · `/shop wallet` | The **cosmetic shop** — banners, borders, nameplates, badges and coin styles for coins (bot-wide prices, since a cosmetic is worn on a global account). Includes the **Gallery**: 36 pieces of real public-domain artwork spanning ukiyo-e, Persian and Mughal manuscript painting, a Mesoamerican codex, Art Nouveau, William Morris textiles, European oils and Hubble/Webb/Cassini/Apollo imagery — credited in `assets/profile/CREDITS.md` |
| `/shop unlock <name>` | Buy a cosmetic |
| `/shop server` · `/shop buy <item>` | Browse and redeem the rewards this server's mods set up |

**Admin subcommands** *(Manage Server):*

| Command | Description |
|---------|-------------|
| `/coin name <name>` | Set the currency name (e.g. NanoCoin) |
| `/coin emoji <emoji>` | Set the currency emoji |
| `/coin config` | Show the current economy settings |

**Bot-owner tools** — prefix only, deliberately kept off the slash tree so
commands nobody else can run don't sit in every member's picker:

| Command | Description |
|---------|-------------|
| `!coin grant <amount> [@user…]` | Add coins to members' (global) balances |
| `!coin take <amount> [@user…]` | Remove coins from members' (global) balances |
| `!coin reset [@user]` | Wipe a global wallet, or every wallet |
| `!fish event <key> [minutes]` | Force-start a fishing event |
| `!profile grant <@user> <cosmetic>` | Award a cosmetic |
| `!profile grantall <cosmetic> [guild_id]` | Award a cosmetic to a whole server |
| `!profile revoke <@user> <cosmetic>` | Take a cosmetic back |

---

### 🚪 Gatekeeper

New-account gate. On join, it can role-mute (`Muted (NanoBot)`) accounts that are **too young**, use the **default avatar**, or use a known **"stock" avatar** (matched by perceptual hash against a shared catalog). How the age and avatar checks combine is set per server via `matchmode` (`or` = mute on either signal, default; `and` = mute only when both are true).

Muted members get a math-captcha verification prompt (DM first, falling back to a quarantine channel) with a persistent button. Correct answer unmutes. Account-age mutes auto-unmute once the account ages out; unverified members are kicked after a timeout. All schedules persist in SQLite and restore on restart.

The `/gatekeeper` group requires **Manage Server**.

| Command | Description |
|---------|-------------|
| `/gatekeeper setup` | Guided first-time setup |
| `/gatekeeper status` | Show the current settings |
| `/gatekeeper enable` / `disable` | Turn the gatekeeper on or off |
| `/gatekeeper role <role>` | Use an existing role as the mute role |
| `/gatekeeper channel <channel>` | Set the fallback quarantine channel |
| `/gatekeeper logchannel <channel>` | Set the gatekeeper log channel |
| `/gatekeeper minage <duration>` | Mute accounts younger than this |
| `/gatekeeper unmuteage <duration>` | Auto-unmute once accounts reach this age |
| `/gatekeeper kicktimeout <duration>` | Kick unverified members after this long |
| `/gatekeeper newaccounts <on/off>` | Toggle muting by account age |
| `/gatekeeper noavatar <on/off>` | Toggle muting members with no avatar |
| `/gatekeeper stockavatar <on/off>` | Toggle muting catalogued stock avatars |
| `/gatekeeper ageunmute <on/off>` | Toggle account-age auto-unmute |
| `/gatekeeper matchmode <and/or>` | How the age and avatar checks combine |
| `/gatekeeper sensitivity <distance>` | Stock-avatar match distance (default 8) |
| `/gatekeeper verify <on/off>` | Toggle captcha verification |
| `/gatekeeper message <text>` | Set the verification prompt text |
| `/gatekeeper learnavatar <image>` | Add an image to the stock-avatar catalog |
| `/gatekeeper checkavatar <user>` | Check whether a member's avatar matches the catalog |

---

### 🗳️ Bot Lists & Voting

NanoBot supports three bot list sites. All integrations are optional -- skip the config keys you don't need.

| Command | Description |
|---------|-------------|
| `/vote` | Vote links for all configured bot lists + your voting history |

Server count is posted to all configured bot lists automatically every 12 hours. Vote webhooks run on the port set in `vote_webhook_port`.

---

### ⚙️ Config & Info

| Command | Description |
|---------|-------------|
| `/prefix [new_prefix]` | View or change the server prefix. Admins only for changes. |
| `/ping` | WebSocket latency |
| `/about` | NanoBot's story, philosophy, and tech stack |
| `/invite` | Invite link with exactly the permissions NanoBot needs |
| `/support` | Link to the NanoBot support server |
| `/uptime` | How long NanoBot has been running since last start |
| `/stats` | Runtime statistics -- commands run, servers, members, latency |
| `/help` | Full paginated command reference |

---

## Data Storage

All persistent data lives in a single `data/nanobot.db` SQLite file (a second file, `data/cache.db`, holds regenerable external-content cache — anime images, stories). No external database, no cloud setup -- back up `nanobot.db` with one `cp`.

| Table | Contents |
|-------|----------|
| `tags` | Personal and global tags per guild |
| `notes` | Mod notes per user per guild |
| `prefixes` | Per-guild custom prefixes |
| `warnings` | Warning records per user per guild |
| `warn_config` | Per-guild warning thresholds and DM settings |
| `welcome_config` | Per-guild welcome message settings |
| `leave_config` | Per-guild leave message settings |
| `unban_schedules` | Pending timed unbans |
| `slow_schedules` | Pending timed slowmode removals |
| `reminders` | Active one-time reminders |
| `recurring_reminders` | Recurring reminders with interval, status, and next fire time |
| `votes` | Vote records per user per bot list site |
| `role_panels` | Role panel definitions (title, description, mode) |
| `role_panel_entries` | Individual roles assigned to each panel |
| `auditlog_config` | Per-guild audit log channel, enabled state, and event toggles |
| `automod_config` | Per-guild AutoMod master switch, rule states, actions, thresholds, exemptions |
| `automod_badwords` | Per-guild bad word filter list |
| `automod_regex_patterns` | Per-guild regex filter patterns |
| `automod_attachment_words` | Per-guild word + attachment filter list |
| `music_settings` | Per-guild 24/7 mode, volume, and other music toggles |
| `music_queue` | Persisted per-guild queue for resume on restart |
| `music_history` | Recently played tracks per guild |
| `music_autoplaylist` | Per-guild autoplay seed tracks |
| `music_song_blocklist` | Per-guild blocked songs |
| `music_user_blocklist` | Per-guild blocked requesters |

Logs: `logs/nanobot.log` (50 KB rotating, 5 files kept).

---

## Project Structure

```
NanoBot/
├── main.py                ← Bot core, prefix resolution, event handlers, tag shortcuts
├── run.py                 ← Pre-flight checker + launcher
├── run.sh / run.bat       ← Platform launchers (find Python, prefer ./venv, run run.py)
├── install.sh             ← Linux/macOS setup (system deps, venv, pip, config)
├── install.ps1            ← Windows setup (WinGet deps, venv, pip, config)
├── install.bat            ← Double-click wrapper for install.ps1
├── migrate.py             ← One-time JSON → SQLite migration script
├── example_config.ini     ← Config template (copy to config.ini)
├── config.ini             ← Your config (gitignored)
├── requirements.txt
├── .gitignore
├── LICENSE
├── README.md
├── data/
│   └── nanobot.db         ← SQLite database (auto-created on first run)
├── logs/
│   └── nanobot.log        ← Rotating log file (auto-created)
├── cogs/
│   ├── admin.py           ← reload / restart / shutdown / update / sync / logs / servers
│   ├── moderation.py      ← ban / cban / tempban / massban / kick / freeze / slow / lock
│   │                         purge / snailpurge / clean / echo / nuke / hide / unhide
│   │                         moveall / addrole / removerole / note / notes / clearnotes
│   │                         channelinfo / last
│   ├── warnings.py        ← /warn issue/list/clear/config + prefix equivalents
│   ├── automod.py         ← Passive rule enforcement (spam, invites, links, caps,
│   │                         mentions, badwords, regex) with exemptions
│   ├── auditlog.py        ← Live server event feed (12 event types)
│   ├── roles.py           ← Button-based role panels + autogen templates
│   ├── welcome.py         ← welcome / leave (set + test for each)
│   ├── tags.py            ← Tag system (personal + global, images, shortcuts,
│   │                         import/export)
│   ├── reminders.py       ← remindme / remind / reminders list+cancel
│   ├── recurring.py       ← /recurring every/list/pause/resume/cancel (prefix: !every)
│   ├── utility.py         ← help / prefix / ping / about / invite / support / server
│   │                         user / avatar / banner / roleinfo / uptime / stats
│   ├── fun.py             ← 26 social + 33 reaction commands, ship, 8-ball (nekos.best)
│   ├── images.py          ← husbando / kitsune / neko / waifu (nekos.best)
│   ├── music.py           ← Voice player: yt-dlp streaming, queue, autoplay, 24/7
│   ├── votes.py           ← Bot list integrations (top.gg, DBL, discord.bots.gg)
│   └── debug.py           ← Owner-only debug REPL / shell
└── utils/
    ├── checks.py          ← Combined user + bot permission decorators
    ├── config.py          ← Config loader and validation
    ├── db.py              ← Async SQLite layer (aiosqlite)
    ├── helpers.py         ← Embed builders, duration parser, color constants
    └── storage.py         ← Legacy JSON helpers (kept for backward compatibility)
```

---

## Philosophy

NanoBot is intentionally small. It doesn't try to replace every mod bot -- it tries to make the things you do every day faster and less annoying on a phone. Not enterprise. Not overengineered. Just useful.

---

## Contributing

Pull requests welcome. Keep the spirit in mind: if a new command doesn't make moderation on mobile faster or easier, it probably doesn't belong here.

---

## License

MIT -- do whatever you want, just don't remove the credits.
