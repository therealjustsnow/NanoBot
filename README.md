# ⚡ NanoBot

> **Small. Fast. Built for Mobile Mods.**

NanoBot is a lightweight Discord moderation bot built for one specific goal: help moderators on their phone get things done fast. No bloated dashboards. No confusing panels. Just clean, fast commands that work.

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

- ✅ Slash commands + prefix commands + @mention — your choice
- ✅ "Last sender" targeting — no need to copy IDs on mobile
- ✅ Timed bans (`/tempban`, `/cban`) with auto-unban — survives bot restarts
- ✅ Timed slowmode with auto-disable
- ✅ Personal + global tag system with multi-word names and image support
- ✅ Tag shortcuts — `n!tagname` fires any tag in one tap
- ✅ Per-server warning system with configurable auto-kick/ban thresholds
- ✅ Per-server welcome and leave messages with template variables
- ✅ Channel hide/unhide, nuke, echo, and voice moveall
- ✅ Server, user, avatar, banner, and role info cards
- ✅ Mod notes — private, invisible to users, SQLite-backed
- ✅ Owner-only admin commands — reload, restart, shutdown, live log viewer
- ✅ Per-server custom prefix
- ✅ Button-based self-assignable role panels — persistent, mobile-first, toggle or single-choice modes
- ✅ Role panel autogen — colours, pronouns, age ranges, regions in one command
- ✅ Passive auto-moderation — spam, invite links, caps, mass mentions, bad words, external URLs
- ✅ Per-server audit log — 12 toggleable event types posted to a configurable channel
- ✅ top.gg and discordbotlist.com integration — vote webhooks, streaks, voter rewards
- ✅ SQLite storage — single portable file, zero cloud dependency, easy to back up
- ✅ Configurable log level (no restart needed)

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

Edit `config.json`:

```json
{
  "token": "YOUR_BOT_TOKEN_HERE",
  "default_prefix": "n!",
  "owner_id": null,
  "log_level": "INFO",
  "log_http": false,
  "topgg_v1_token": null,
  "dbl_token": null,
  "vote_webhook_port": 5000,
  "vote_webhook_secret": null
}
```

| Key | Required | Description |
|-----|----------|-------------|
| `token` | ✅ | Your bot token from the Developer Portal |
| `default_prefix` | | Default prefix for all servers (changeable per-server with `/prefix`). Default: `n!` |
| `owner_id` | | Your Discord user ID — overrides app owner for admin commands. Leave `null` to use app owner |
| `log_level` | | `DEBUG` / `INFO` / `WARNING` / `ERROR` — changeable live with `!setloglevel`. Default: `INFO` |
| `log_http` | | `true` to log every raw HTTP request (very verbose, for debugging). Default: `false` |
| `topgg_v1_token` | | top.gg v1 API token (Bearer) for command syncing |
| `dbl_token` | | discordbotlist.com bot token for stat posting and command syncing |
| `vote_webhook_port` | | Port for the vote webhook HTTP server. Default: `5000` |
| `vote_webhook_secret` | | Shared secret to verify incoming vote webhooks from both sites |

> ⚠️ **Never commit `config.json` to git.** It's in `.gitignore`.

Token via environment variable also works — and takes priority over `config.json`:
```bash
export DISCORD_TOKEN=your_token_here
```

### 4. Discord Developer Portal

Enable these **Privileged Gateway Intents** in your app's Bot settings:
- ✅ **Server Members Intent**
- ✅ **Message Content Intent**

Without these, prefix commands and most mod commands will silently fail.

### 5. Run

```bash
# Recommended — pre-flight check then auto-launch
python run.py

# Check only, don't launch
python run.py --check

# Or launch directly
python main.py
```

Logs are written to `logs/nanobot.log` (rotating, max 5 MB × 3 files).

---

## Commands

All commands work as slash commands (`/`), prefix commands (default `n!`), and @mention.

Most commands that take a `user` argument will automatically **target the last person who sent a message** in the channel if left blank — ideal for mobile where copying IDs is a pain.

Use `/help` for the full paginated reference, `/help <command>` for detail on any command, or `/help <category>` to browse a section — e.g. `/help banning`, `/help tags`, `/help channel`.

---

### 🔧 Admin *(owner only)*

Restricted to the bot owner set via `owner_id` in `config.json`, or the Discord application owner. These are **prefix-only by design** — slash commands appear in the `/` menu for every user in the server, which would expose admin controls publicly.

| Command | Description |
|---------|-------------|
| `!reload [cog\|all]` | Hot-reload a cog without restarting |
| `!restart` | Gracefully close and re-execute the process |
| `!shutdown` | Flush logs and close cleanly |
| `!setloglevel <level>` | Change log verbosity live (`DEBUG` / `INFO` / `WARNING` / `ERROR`) |
| `!logs [lines]` | Tail `logs/nanobot.log` in Discord — default 20, max 50 lines |

---

### 🔨 Banning

| Command | Description |
|---------|-------------|
| `/ban [user] [message]` | Permanent ban with optional DM. Targets last sender if no user given. |
| `/cban [user] [days] [wait] [message]` | Clean ban — deletes message history, optional timed unban, optional DM |
| `/tempban [user] [duration] [reason]` | Quick timed ban with auto-unban. Defaults to 24h. Survives restarts. |
| `/softban [user] [days]` | Ban + immediately unban — wipes messages without a lasting ban. User can rejoin. |
| `/massban <id1 id2 ...> [reason]` | Ban up to 50 users by ID at once. Useful after a raid. |
| `/unban <user_id> [reason]` | Unban by Discord User ID |

---

### 👢 Kicking & Timeouts

| Command | Description |
|---------|-------------|
| `/kick [user] [message]` | Kick with optional DM. Targets last sender if no user given. |
| `/freeze [user] [duration] [reason]` | Discord Timeout — can't speak, react, or join VCs. Default 10m, max 28d. |
| `/unfreeze <user>` | Remove a timeout before it expires. |

---

### 📢 Channel Controls

| Command | Description |
|---------|-------------|
| `/lock [channel] [reason]` | Toggle @everyone send permissions. Run again to unlock. |
| `/hide [channel]` | Hide a channel from @everyone (`view_channel = false`). |
| `/unhide [channel]` | Restore @everyone visibility on a hidden channel. |
| `/slow [delay] [length]` | Set slowmode (`30s`–`5m`) with optional timed auto-disable. No args = toggle. |
| `/purge <amount>` | Bulk delete 1–100 messages. Filters: `bots`, `user`, `contains`, `starts_with`, `ends_with`. |
| `/snailpurge <amount>` | Slow one-by-one delete (1–500). No 14-day limit. Requires confirmation code. |
| `/clean [amount]` | Delete NanoBot's own recent messages from the channel. |
| `/echo [channel] <message>` | Send a message as NanoBot. Prefix mode auto-deletes your trigger. |
| `/nuke [reason]` | Clone channel + delete original — wipes all history. Button confirmation required. **Irreversible.** |
| `/moveall <to> [from]` | Move all members from one voice channel to another. Defaults to your current VC. |

---

### 🎭 Roles

#### Mod role assignment

| Command | Description |
|---------|-------------|
| `/addrole <user> <role>` | Assign a role to a user. Role must be below NanoBot's highest role. |
| `/removerole <user> <role>` | Remove a role from a user. |

#### Self-assignable role panels

Button-based panels members can use themselves — no commands, no typing, one tap. Panels are persistent and survive bot restarts. Requires Manage Roles.

**Panel management:**

| Command | Description |
|---------|-------------|
| `/roles panel create` | Create a new panel (not posted yet) |
| `/roles panel post` | Post or re-post a panel to a channel |
| `/roles panel edit` | Edit a panel's title, description, or mode |
| `/roles panel delete` | Delete a panel and remove its posted message |
| `/roles panel list` | List all panels in this server |
| `/roles add` | Add a role to a panel (label, emoji, and button colour are all customisable) |
| `/roles remove` | Remove a role from a panel |

**Panel modes:**

| Mode | Behaviour |
|------|-----------|
| `toggle` | Click to add, click again to remove — members can hold multiple roles |
| `single` | Radio-style: picking a new role automatically removes any other role from the same panel |

**Auto-generation** *(requires Administrator)*:

Instantly creates a full set of roles and posts a ready-to-use panel in one command. Skips roles that already exist. Auto-positions created roles just below NanoBot's top role. All autogen commands accept up to 5 existing roles to append to the generated panel.

| Command | What it creates |
|---------|----------------|
| `/roles autogen colors` | 18 cosmetic colour roles (Red, Blue, Gold…) — single-choice panel |
| `/roles autogen pronouns` | She/Her, He/Him, They/Them, It/Its, Any/All — toggle panel |
| `/roles autogen age` | 13-17, 18-20, 21-25, 26-30, 31+ — single-choice panel |
| `/roles autogen region` | 7 world regions (N. America, Europe, Asia…) — toggle panel |

> Only one autogen can run at a time per server — a second attempt is rejected immediately rather than queuing.

---

### ⚠️ Warnings

| Command | Description |
|---------|-------------|
| `/warn <user> [reason]` | Issue a warning. Configured auto-actions fire at thresholds. |
| `/warnings <user>` | View all warnings for a user (last 8 shown with dates and moderators). |
| `/clearwarnings <user>` | Permanently wipe all warnings for a user. Admin only. |
| `/warnconfig [kick_at] [ban_at] [dm_user]` | Configure per-server thresholds. No args shows current config. |

---

### 🛡️ Auto-Moderation

Passive rule enforcement — watches every message and acts automatically. All rules are individually togglable with their own action. Exempt channels and roles are skipped for all rules.

**Commands** (all require Manage Server):

| Command | Description |
|---------|-------------|
| `/automod status` | Full config overview for the server |
| `/automod enable` | Master on switch |
| `/automod disable` | Master off switch |
| `/automod rule` | Toggle a rule on/off and set its action |
| `/automod spam` | Set spam detection message count and time window |
| `/automod caps` | Set caps percentage threshold and minimum message length |
| `/automod mentions` | Set per-message mention limit |
| `/automod badword add` | Add a word to the server filter |
| `/automod badword remove` | Remove a word from the server filter |
| `/automod badword list` | List all filtered words (ephemeral) |
| `/automod ignore` | Add or remove exempt channels or roles |

**Rules:**

| Rule | What it catches |
|------|----------------|
| `spam` | X messages from the same user within Y seconds |
| `invites` | Discord invite links (`discord.gg`, `discord.com/invite`) |
| `links` | Any external URL |
| `caps` | Messages above a configurable % uppercase (min length guard) |
| `mentions` | Too many @mentions in a single message |
| `badwords` | Configurable per-server word list |

**Actions** (set per rule):

| Action | What happens |
|--------|-------------|
| `delete` | Silently delete the offending message |
| `warn` | Delete + add a formal warning (triggers warnconfig auto-kick/ban) |
| `timeout` | Delete + 10-minute Discord timeout |

---

### 📋 Audit Log

Per-server event feed posted to a configurable channel. Each event type is individually toggleable.

**Commands** (all require Manage Server):

| Command | Description |
|---------|-------------|
| `/auditlog channel <#channel>` | Set the channel to post events to |
| `/auditlog enable` | Enable audit logging for this server |
| `/auditlog disable` | Disable audit logging for this server |
| `/auditlog events` | Toggle individual event types on/off |
| `/auditlog status` | Show current configuration |

**Events logged:**

| Event | Description |
|-------|-------------|
| `msg_delete` | Message deleted |
| `msg_edit` | Message edited |
| `member_join` | Member joined the server |
| `member_leave` | Member left the server |
| `member_ban` | Member banned |
| `member_unban` | Member unbanned |
| `nick_change` | Nickname changed |
| `role_update` | Member roles added or removed |
| `channel_create` | Channel created |
| `channel_delete` | Channel deleted |
| `role_create` | Role created |
| `role_delete` | Role deleted |

---

### 🔎 Info & Notes

| Command | Description |
|---------|-------------|
| `/note <user> <content>` | Add a private mod note. Never visible to the target user. |
| `/notes <user>` | View notes for a user (last 8). Ephemeral. |
| `/clearnotes <user>` | Wipe all notes for a user. Admin only. |
| `/channelinfo [channel]` | Channel type, ID, category, creation date, NSFW status, slowmode, topic. |
| `/last` | Show who last sent a message here — the auto-target for `/kick`, `/ban`, etc. |

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

- **Personal tags** — only you can create and use them
- **Global tags** — anyone can use; Manage Messages required to create

#### Slash commands

| Command | Description |
|---------|-------------|
| `/tag` or `/tag list` | List your personal tags and all global tags |
| `/tag create <n> [content] [image]` | Create a personal tag |
| `/tag global <n> [content] [image]` | Create a server-wide global tag *(Manage Messages)* |
| `/tag use <n> [user]` | Post in channel, or DM to a specific user |
| `/tag preview <n>` | Preview a tag — only you see the response |
| `/tag edit <n> [content] [image]` | Update a tag's content or image |
| `/tag delete <n>` | Delete a tag |
| `/tag export` | Download all your personal tags as a JSON file |

#### Prefix shorthands

| Shorthand | Description |
|-----------|-------------|
| `n!tag` | List all tags |
| `n!tag <n>` | Post tag in channel |
| `n!<n>` | Even shorter — fires any tag directly |
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
| `/server` | Full server info — members, channels, boost level, features, creation date |
| `/user [user]` | User card — status, roles, badges, join date, boost, timeout status |
| `/avatar [user]` | Avatar at 1024px with PNG/JPG/WEBP/GIF download links |
| `/banner [user]` | Profile banner with download links |
| `/roleinfo <role>` | Role color, position, member count, permissions, creation date |

---

### ⏰ Reminders

Voters on top.gg or discordbotlist.com get an increased limit of 50 active reminders (default is 25).

| Command | Description |
|---------|-------------|
| `/remindme <message with duration>` | Set a reminder for yourself. Duration goes at the end. |
| `/remind <@user> <message with duration>` | Set a reminder for someone else |
| `/reminders` | List your active reminders |
| `/reminders cancel <id>` | Cancel a reminder by its 6-character ID |

```
!remindme stand up 1h
!remindme check that PR 30m
```

---

### 🗳️ Voting

| Command | Description |
|---------|-------------|
| `/vote` | View vote links, your current streak, and cooldown status for each site |
| `/vote notify on\|off` | Opt in or out of DM cooldown reset notifications |

Voting on [top.gg](https://top.gg) or [discordbotlist.com](https://discordbotlist.com) unlocks a higher reminder limit (50 vs 25) and tracks your vote streak. You'll receive a DM when your cooldown resets so you can vote again.

**Webhook setup** (if self-hosting and want vote tracking):

| Site | Webhook URL |
|------|-------------|
| top.gg | `http://YOUR_IP:PORT/webhook/topgg` |
| discordbotlist.com | `http://YOUR_IP:PORT/webhook/dbl` |

Set `vote_webhook_port` and `vote_webhook_secret` in `config.json`, then register the URLs in each site's bot settings.

---

### ⚙️ Config & Info

| Command | Description |
|---------|-------------|
| `/prefix [new_prefix]` | View or change the server prefix. Admins only for changes. |
| `/ping` | WebSocket latency |
| `/info` | Bot stats — latency, servers, prefix, discord.py + Python version |
| `/invite` | Invite link with exactly the permissions NanoBot needs |
| `/support` | Link to the NanoBot support server |
| `/about` | NanoBot's story, philosophy, and tech stack |
| `/uptime` | How long NanoBot has been running since last start |

---

## Data Storage

All data lives in a single `data/nanobot.db` SQLite file. No external database, no cloud setup — back it up with one `cp`.

| Table | Contents |
|-------|----------|
| `tags` | Personal and global tags per guild |
| `notes` | Mod notes per user per guild |
| `prefixes` | Per-guild custom prefixes |
| `warnings` | Warning records per user per guild |
| `warn_config` | Per-guild thresholds and DM settings |
| `welcome_config` | Per-guild welcome message settings |
| `leave_config` | Per-guild leave message settings |
| `unban_schedules` | Pending timed unbans |
| `slow_schedules` | Pending timed slowmode removals |
| `reminders` | Active reminders |
| `votes` | Vote records, streaks, and notification preferences |
| `role_panels` | Panel definitions, entries, and posted message references |

Logs → `logs/nanobot.log` (5 MB rotating, 3 files kept).

---

## Project Structure

```
NanoBot/
├── main.py              ← Bot core, prefix resolution, event handlers, tag shortcuts
├── run.py               ← Pre-flight checker + launcher
├── config.json          ← Token, prefix, log level, owner ID  (not committed to git)
├── requirements.txt
├── .gitignore
├── README.md
├── data/
│   └── nanobot.db       ← SQLite database (auto-created on first run)
├── logs/
│   └── nanobot.log      ← Rotating log file (auto-created)
├── cogs/
│   ├── admin.py         ← reload / restart / shutdown / setloglevel / logs  (owner only)
│   ├── automod.py       ← passive rule enforcement (spam / invites / links / caps / mentions / badwords)
│   ├── auditlog.py      ← per-server event feed (12 toggleable event types)
│   ├── moderation.py    ← ban / cban / tempban / softban / massban / unban / kick
│   │                       freeze / unfreeze / slow / lock / hide / unhide
│   │                       purge / snailpurge / clean / echo / nuke / moveall
│   │                       addrole / removerole / note / notes / clearnotes
│   │                       channelinfo / last
│   ├── reminders.py     ← remindme / remind / reminders list+cancel
│   ├── roles.py         ← self-assignable role panels (persistent buttons, toggle/single modes, autogen)
│   ├── tags.py          ← tag system (personal + global, images, shortcuts)
│   ├── utility.py       ← help / prefix / ping / info / invite / about / support
│   │                       server / user / avatar / banner / roleinfo / uptime
│   ├── votes.py         ← top.gg + discordbotlist.com integration, vote webhooks, rewards
│   ├── warnings.py      ← warn / warnings / clearwarnings / warnconfig
│   └── welcome.py       ← welcome / leave  (set + test for each)
└── utils/
    ├── checks.py         ← Combined user + bot permission decorators
    ├── config.py         ← Config loader and validation
    ├── db.py             ← Async SQLite layer (aiosqlite)
    ├── helpers.py        ← Embed builders, duration parser, color constants
    └── storage.py        ← Atomic JSON key-value storage (legacy/internal use)
```

---

## Philosophy

NanoBot is intentionally small. It doesn't try to replace every mod bot — it tries to make the things you do every day faster and less annoying. Not enterprise. Not overengineered. Just useful.

---

## Contributing

Pull requests welcome. Keep the spirit in mind: if a new command doesn't make moderation on mobile faster or easier, it probably doesn't belong here.

---

## License

MIT — do whatever you want, just don't remove the credits.
