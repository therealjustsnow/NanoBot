# ⚡ NanoBot

> **Small. Fast. Built for Mobile Mods.**

NanoBot is a lightweight Discord moderation and utility bot built for one specific goal: help moderators on their phone get things done fast. No bloated dashboards. No confusing panels. Just clean, fast commands that work.

**[💬 Support Server](https://discord.gg/M7fjxNg72s)** · **[GitHub](https://github.com/therealjustsnow/NanoBot)**

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
- Six passive rules: spam, invites, links, caps, mentions, bad words
- Regex pattern matching with test-before-you-save
- Three actions per rule: delete, warn, timeout
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

**AI**
- `/eli5` -- plain-English explanations via Groq (Llama 3.1 8B, free tier)

**Bot Lists**
- top.gg, discordbotlist.com, discord.bots.gg integration
- Vote tracking with per-user history
- Automatic stat posting on a 12-hour loop

**Infrastructure**
- SQLite storage -- single portable file, zero cloud dependency
- Owner-only admin: hot-reload, restart, git pull update, slash sync
- Per-server custom prefix
- Configurable log level (no restart needed)
- GitHub Actions CI with Black auto-formatting

---

## Setup

### 1. Prerequisites

- Python 3.11+
- A Discord bot application ([discord.com/developers](https://discord.com/developers/applications))

### 2. Install

```bash
git clone https://github.com/therealjustsnow/NanoBot.git
cd NanoBot
pip install -r requirements.txt
```

### 3. Config

Copy `example_config.json` to `config.json` and fill in your values:

```json
{
  "token": "YOUR_BOT_TOKEN_HERE",
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

| Key | Required | Description |
|-----|----------|-------------|
| `token` | **Yes** | Bot token from the Developer Portal |
| `default_prefix` | No | Default prefix for all servers (changeable per-server with `/prefix`). Default `n!` |
| `owner_id` | No | Your Discord user ID -- overrides app owner for admin commands. `null` = use app owner |
| `log_level` | No | `DEBUG` / `INFO` / `WARNING` / `ERROR`. Changeable live with `!setloglevel` |
| `log_http` | No | `true` to log every raw HTTP request (very verbose, for debugging) |
| `topgg_token` | No | top.gg API token -- enables stat posting and vote webhooks |
| `topgg_v1_token` | No | top.gg v1 API token -- enables command sync to top.gg |
| `dbl_token` | No | discordbotlist.com bot token -- enables stat posting, command sync, and vote webhooks |
| `discordbotsgg_token` | No | discord.bots.gg bot token -- enables stat posting and vote webhooks |
| `vote_webhook_port` | No | Port for the vote webhook HTTP server. Default `5000` |
| `vote_webhook_secret` | No | Shared secret for webhook verification. top.gg uses HMAC-SHA256; DBL and discord.bots.gg use a plain `Authorization` header match |
| `groq_api_key` | No | Groq API key for the `/eli5` command. Free at [console.groq.com](https://console.groq.com). Also accepts `GROQ_API_KEY` env var |

> ⚠️ **Never commit `config.json` to git.** It's already in `.gitignore`.

Token via environment variable also works:
```bash
export DISCORD_TOKEN=your_token_here
```

### 4. Discord Developer Portal

Enable these **Privileged Gateway Intents** in your app's Bot settings:
- ✅ **Server Members Intent**
- ✅ **Message Content Intent**

Without these, prefix commands and most mod commands will silently fail.

### 5. Migrating from JSON storage

If you're upgrading from an older version that used JSON files for data, run the migration script once before starting:

```bash
python migrate.py
```

This imports all existing JSON data into SQLite. Your JSON files are left untouched as a backup. Safe to run multiple times.

### 6. Run

```bash
# Recommended -- pre-flight check then launch
python run.py

# Or launch directly
python main.py
```

Logs are written to `logs/nanobot.log` (rotating, max 5 MB x 3 files).

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
| `!restart` | Gracefully close and re-execute the process |
| `!shutdown` | Flush logs and close cleanly |
| `!update` | `git pull` + reload all cogs. Does NOT sync slash commands |
| `!sync [guild_id]` | Push slash commands to Discord (global or one guild) |
| `!setloglevel <level>` | Change log verbosity live (`DEBUG` / `INFO` / `WARNING` / `ERROR`) |
| `!logs [lines]` | Tail `logs/nanobot.log` in Discord -- default 20, max 50 lines |
| `!servers` | List all servers the bot is in |

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
| `/purge <amount>` | Bulk delete 1-100 messages. Filters: `bots`, `user`, `contains`, `starts_with`, `ends_with`. |
| `/snailpurge <amount>` | Slow one-by-one delete (1-500). No 14-day limit. Requires confirmation code. |
| `/clean [amount]` | Delete NanoBot's own recent messages from the channel. |
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
| `/warn list <user>` | View all warnings for a user (last 8 shown with dates and moderators). |
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
| `/note <user> <content>` | Add a private mod note. Never visible to the target user. |
| `/notes <user>` | View notes for a user (last 8). Ephemeral. |
| `/clearnotes <user>` | Wipe all notes for a user. Admin only. |
| `/last` | Show who last sent a message here -- the auto-target for `/kick`, `/ban`, etc. |

---

### 🛡️ AutoMod

Passive rule enforcement. Watches every message and acts without manual intervention. All commands require **Manage Server**.

**Seven individually toggleable rules:**

| Rule | What it catches |
|------|----------------|
| `spam` | X messages from the same user within Y seconds |
| `invites` | Discord invite links (`discord.gg`, `discord.com/invite`) |
| `links` | Any external URL |
| `caps` | Messages above a configurable % uppercase (minimum length guard) |
| `mentions` | Too many @mentions in a single message |
| `badwords` | Per-server word list (case-insensitive substring match) |
| `regex` | Custom regex patterns with test-before-you-save |

**Three actions per rule (set independently):** `delete`, `warn` (adds a formal warning), `timeout` (10-minute Discord timeout).

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
| `/automod badword add <word>` | Add a word to the filter |
| `/automod badword remove <word>` | Remove a word from the filter |
| `/automod badword list` | List all filtered words (ephemeral) |
| `/automod regex add <pattern> [label]` | Add a regex pattern to the filter |
| `/automod regex remove <pattern>` | Remove a regex pattern |
| `/automod regex list` | List all regex patterns |
| `/automod regex test <pattern> <text>` | Test a pattern against sample text before saving |
| `/automod ignore channel <channel>` | Toggle a channel exemption |
| `/automod ignore role <role>` | Toggle a role exemption |

---

### 📋 Audit Log

Posts a live feed of server events to a configurable channel. Fully opt-in -- nothing fires until you set a channel and enable it. All commands require **Manage Server**.

**Twelve toggleable event types:** message delete, message edit, member join, member leave, member ban, member unban, nickname change, role update, channel create, channel delete, role create, role delete.

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
| `/tag use <n> [user]` | Post in channel, or DM to a specific user |
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
| `/banner [user]` | Profile banner with download links |
| `/roleinfo <role>` | Role color, position, member count, permissions, creation date |
| `/channelinfo [channel]` | Channel type, ID, category, creation date, NSFW status, slowmode, topic |

---

### ⏰ Reminders

**One-time reminders:**

| Command | Description |
|---------|-------------|
| `/remindme <message with duration>` | Set a reminder for yourself. Duration goes at the end. |
| `/remind <@user> <message with duration>` | Set a reminder for someone else |
| `/reminders list` | List your active reminders |
| `/reminders cancel <id>` | Cancel a reminder by its 6-character ID |

```
!remindme stand up 1h
!remindme check that PR 30m
```

**Recurring reminders:**

| Command | Description |
|---------|-------------|
| `/every <interval> <message> [label] [dm]` | Create a recurring reminder that fires repeatedly |
| `/recurring` or `/recurring list` | List your recurring reminders with interval, next fire time, and status |
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

### 🧒 ELI5

Explain any topic in plain English using Groq's free API (Llama 3.1 8B). Responses are kept short enough to read comfortably on a phone screen.

| Command | Description |
|---------|-------------|
| `/eli5 <topic>` | Get a plain-English explanation of any topic |

Requires a Groq API key in config or the `GROQ_API_KEY` environment variable. Free at [console.groq.com](https://console.groq.com) (14,400 requests/day). Per-user cooldown: 1 use per 15 seconds. The command degrades gracefully if no key is set -- it tells the user and moves on.

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

All data lives in a single `data/nanobot.db` SQLite file. No external database, no cloud setup -- back it up with one `cp`.

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

Logs: `logs/nanobot.log` (5 MB rotating, 3 files kept).

---

## Project Structure

```
NanoBot/
├── main.py                ← Bot core, prefix resolution, event handlers, tag shortcuts
├── run.py                 ← Pre-flight checker + launcher
├── migrate.py             ← One-time JSON → SQLite migration script
├── example_config.json    ← Config template (copy to config.json)
├── config.json            ← Your config (gitignored)
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
│   ├── recurring.py       ← /every + /recurring list/pause/resume/cancel
│   ├── utility.py         ← help / prefix / ping / about / invite / support / server
│   │                         user / avatar / banner / roleinfo / uptime / stats
│   ├── fun.py             ← 26 social + 33 reaction commands, ship, 8-ball (nekos.best)
│   ├── images.py          ← husbando / kitsune / neko / waifu (nekos.best)
│   ├── eli5.py            ← AI explanations via Groq (Llama 3.1 8B)
│   └── votes.py           ← Bot list integrations (top.gg, DBL, discord.bots.gg)
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
