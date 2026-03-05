# ⚡ NanoBot

> **Small. Fast. Built for Mobile Mods.**

NanoBot is a lightweight Discord moderation bot built for one specific goal: help moderators who are on their phone get things done fast. No bloated dashboards. No confusing panels. No database setup. Just clean, simple commands that work.

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
- ✅ Timed bans with auto-unban (survives bot restarts)
- ✅ Timed slowmode with auto-disable
- ✅ Personal + global tag system with multi-word names and image support
- ✅ Tag shortcuts — `n!tagname` fires any tag in one tap
- ✅ Mod notes stored in JSON — no database needed
- ✅ Server, user, avatar, banner, and role info cards
- ✅ Owner-only admin commands — reload, restart, shutdown, live log viewer
- ✅ Configurable log level via `config.json` (no restart needed)
- ✅ Per-server custom prefix
- ✅ Zero database — everything is plain JSON files

---

## Setup

### 1. Prerequisites

- Python 3.11+
- A Discord bot application ([discord.com/developers](https://discord.com/developers/applications))

### 2. Install

```bash
git clone https://github.com/YOUR_USER/nanobot.git
cd nanobot
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
  "log_http": false
}
```

| Key | Description |
|-----|-------------|
| `token` | Your bot token from the Developer Portal |
| `default_prefix` | Default prefix for all servers (can be changed per-server with `/prefix`) |
| `owner_id` | Your Discord user ID — overrides the app owner for admin commands. Leave `null` to use the app owner automatically |
| `log_level` | `DEBUG` / `INFO` / `WARNING` / `ERROR` — can also be changed live with `!setloglevel` |
| `log_http` | Set `true` to log every raw HTTP request (very verbose, useful for debugging) |

> ⚠️ **Never commit `config.json` to git.** It's already in `.gitignore`.

You can also set the token via environment variable:
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
# Recommended — runs a pre-flight check then launches
python run.py

# Or skip the check and launch directly
python main.py
```

Logs are written to `logs/nanobot.log` (rotating, max 5 MB × 3 files).

---

## Commands

All commands work as slash commands (`/`), prefix commands (default `n!`), and @mention.

Most commands that take a `user` argument will automatically **target the last person who sent a message** in the channel if you leave it blank — ideal for mobile where copying IDs is a pain.

Commands are listed alphabetically within each section.

---

### 🔧 Admin *(owner only)*

Restricted to the bot owner set via `owner_id` in `config.json`, or the Discord application owner.

#### `!logs [lines]`
Tail `logs/nanobot.log` right in Discord. Default 20 lines, max 50. Great for checking errors from your phone without SSH.

#### `!reload [cog|all]`
Hot-reload a cog without restarting the bot.
```
!reload all           → reload every cog
!reload moderation    → reload only cogs/moderation.py
```

#### `!restart`
Gracefully close the connection and re-execute the process. Config changes take effect immediately.

#### `!setloglevel <level>`
Change log verbosity live and save it to `config.json`. No restart needed.
```
!setloglevel DEBUG    → verbose
!setloglevel INFO     → normal
!setloglevel WARNING  → quiet
```

#### `!shutdown`
Flush logs and close the connection cleanly.

---

### 🔨 Banning

#### `/ban`
Permanent ban with optional DM. Defaults to last sender.

#### `/cban` *(alias: cleanban)*
Clean ban — delete message history + optional timed auto-unban + optional DM.

| Argument | Default | Description |
|----------|---------|-------------|
| `user` | *last sender* | Who to ban |
| `days` | `7` | Days of message history to delete (1–7) |
| `wait` | *none* | Auto-unban after (e.g. `1h`, `30m`, `7d`) |
| `message` | *default* | DM sent to the user |

Auto-unbans survive restarts (stored in `data/unban_schedules.json`).

#### `/unban`
Unban by user ID.

---

### 📢 Channel Controls

#### `/lock`
Toggle @everyone send permissions in a channel. Run again to unlock.

#### `/purge`
Bulk delete last X messages (1–100). Optional user filter.

#### `/slow`
Set slowmode with optional auto-disable timer.

| Argument | Default | Description |
|----------|---------|-------------|
| `delay` | *toggle* | e.g. `30s`, `2m` (max 5 min) |
| `length` | *indefinite* | Auto-disable after e.g. `10m`, `1h` (max 7 days) |

Auto-disable survives restarts.

---

### ⚙️ Configuration & Info

#### `/about`
NanoBot's story, philosophy, and tech stack.

#### `/info`
Bot stats — latency, servers, prefix, Python and discord.py version.

#### `/invite`
Invite link with exactly the permissions NanoBot needs.

#### `/ping`
Check latency.

#### `/prefix [new_prefix]`
View or change the prefix for this server. Admins only for changes.

#### `/uptime`
How long NanoBot has been running since the last start or `!restart`.

---

### 🔎 Info & Notes

#### `/clearnotes`
Wipe all mod notes for a user. Admin only.

#### `/last`
Show who last sent a message in the channel — the target for no-arg commands like `/kick`.

#### `/note`
Add an internal mod note about a user. Never visible to the user.

#### `/notes`
View notes for a user (last 8). Mod only.

#### `/whois`
Detailed mod-focused user card — ID, join date, account age, roles, timeout status, badges, and note count.

---

### 👢 Kicking & Timeouts

#### `/freeze`
Discord Timeout. User can't speak, react, or join VCs.

| Argument | Default | Description |
|----------|---------|-------------|
| `user` | *last sender* | Who to freeze |
| `duration` | `10m` | How long (max 28 days) |
| `reason` | *none* | Optional reason |

#### `/kick`
Kick with optional DM. Defaults to last sender.

#### `/unfreeze`
Remove a timeout early.

---

### 🔍 Server & User Info

#### `/avatar` *(aliases: av, pfp, icon)*
Show a user's avatar at 1024px with PNG/JPG/WEBP/GIF download links. Shows server avatar if set.

#### `/banner` *(alias: userbanner)*
Show a user's profile banner with download links.

#### `/roleinfo` *(aliases: role, ri)*
Role details — color, position, member count, creation date, hoist/mentionable flags, and notable permissions.

#### `/server` *(aliases: serverinfo, si, guild)*
Full server info — member counts, channel breakdown, boost level, role count, creation date, notable features.

#### `/user` *(aliases: userinfo, ui, member)*
Public user card — status, activity, join date, account age, roles, badges, boost and timeout status. Anyone can use this.

---

### 🏷️ Tags

Saved text snippets up to 2000 characters with optional images. Post in channel with one tap.

- **Personal tags** — only you can create and see them
- **Global tags** — anyone can use, mods-only to create

#### Slash commands

| Command | Description |
|---------|-------------|
| `/tag` or `/tag list` | List all tags (alphabetical) |
| `/tag create <n> [content] [image]` | Create a personal tag |
| `/tag global <n> [content] [image]` | Create a global tag *(mods only)* |
| `/tag use <n> [user]` | Post in channel, or DM to a specific user |
| `/tag preview <n>` | Preview a tag (ephemeral) |
| `/tag edit <n> <content>` | Update content |
| `/tag delete <n>` | Delete a tag |
| `/tag image <n>` | Add or replace a tag's image |

#### Prefix shorthands

Tag names can contain spaces. Use `|` to separate name from content when creating.

| Shorthand | Description |
|-----------|-------------|
| `n!tag` | List all tags |
| `n!tag <n>` | Post tag in channel |
| `n!<n>` | Same — even shorter |
| `n!tag + <n> \| <content>` | Create personal tag |
| `n!tag - <n>` | Delete personal tag |
| `n!tag g+ <n> \| <content>` | Create global tag *(mods only)* |

```
n!tag + server rules | Read #rules before posting!
n!server rules            → posts the "server rules" tag
n!welcome                 → posts the "welcome" tag
n!tag - server rules      → deletes it
```

> Tags over 1500 characters are sent as plain text to stay within Discord's embed limit.

---

## Data Storage

All data lives in `data/` as plain JSON. No migrations, no setup, easy to back up.

| File | Contents |
|------|----------|
| `notes.json` | Mod notes per user per guild |
| `prefixes.json` | Per-guild custom prefixes |
| `slow_schedules.json` | Pending timed slowmode removals |
| `tags.json` | Personal and global tags per guild |
| `unban_schedules.json` | Pending timed unbans |

Logs → `logs/nanobot.log` (5 MB rotating, 3 files kept).

---

## Project Structure

```
nanobot/
├── main.py              ← Bot core, error handler, tag shortcuts
├── run.py               ← Pre-flight checker + launcher
├── config.json          ← Token, prefix, log level, owner ID
├── requirements.txt
├── .gitignore
├── README.md
├── cogs/
│   ├── admin.py         ← reload / restart / shutdown / logs / setloglevel
│   ├── moderation.py    ← ban / cban / unban / kick / freeze / unfreeze / slow / lock / purge / whois / note / notes / clearnotes / last
│   ├── tags.py          ← tag system (personal + global, images, multi-word names, shortcuts)
│   └── utility.py       ← about / avatar / banner / help / info / invite / ping / prefix / roleinfo / server / uptime / user
└── utils/
    ├── helpers.py        ← embed builders, duration parser
    └── storage.py        ← JSON read/write
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
