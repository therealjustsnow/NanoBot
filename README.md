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
- Some bots assume you're on desktop

NanoBot fixes that.

---

## Features

- ✅ Slash commands + prefix commands + @mention — your choice
- ✅ "Last sender" targeting — no need to copy IDs on mobile
- ✅ Timed bans with auto-unban (survives bot restarts)
- ✅ Timed slowmode with auto-disable
- ✅ Personal + global tag system (DMs you snippets instantly)
- ✅ Mod notes stored in JSON — no database needed
- ✅ Mobile-optimized info cards
- ✅ Per-server custom prefix
- ✅ Zero database — everything is plain JSON files

---

## Setup

### 1. Prerequisites

- Python 3.11+
- A Discord bot application ([discord.com/developers](https://discord.com/developers/applications))

### 2. Required Bot Permissions

Enable these in the Discord Developer Portal:

**Privileged Gateway Intents (required):**
- ✅ Server Members Intent
- ✅ Message Content Intent

**Bot Permissions:**
- Ban Members
- Kick Members
- Manage Channels
- Manage Messages
- Moderate Members (for timeouts)
- Read Messages / View Channels
- Send Messages
- Embed Links

### 3. Install & Run

```bash
# Clone the repo
git clone https://github.com/YOUR_USER/nanobot.git
cd nanobot

# Install dependencies
pip install -r requirements.txt

# Add your token (edit config.json)
# OR set as an environment variable:
export DISCORD_TOKEN=your_token_here

# Run
python main.py
```

### 4. config.json

```json
{
  "token": "YOUR_BOT_TOKEN_HERE",
  "default_prefix": "!"
}
```

> ⚠️ **Never commit `config.json` to git.** It's already in `.gitignore`.

---

## Commands

All commands work as slash commands (`/`), prefix commands (default `!`), and @mention (`@NanoBot command`).

Most commands that take a `user` argument will automatically **target the last person who sent a message** in the channel if you leave it blank — ideal for mobile where copying IDs is painful.

---

### 🔨 Banning

#### `/cban` (alias: `cleanban`)
Ban + delete message history + optional timed auto-unban + optional DM.

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `user` | Member | _last sender_ | Who to ban |
| `days` | 1–7 | `7` | Days of message history to delete |
| `wait` | Duration | _none_ | Auto-unban after this time (e.g. `1h`, `30m`, `7d`) |
| `message` | Text | _default_ | DM to send the user |

```
/cban                          → ban the last sender, 7d history, no auto-unban
/cban @user                    → ban @user, 7d history
/cban @user 1                  → ban, 1 day of history deleted
/cban @user 7 1h               → ban, 7d history, unban in 1 hour
/cban @user 7 24h Read the rules before rejoining.
```

Auto-unbans survive bot restarts (persisted to `data/unban_schedules.json`).

---

#### `/ban`
Permanent ban with optional DM. No auto-unban.

```
/ban                  → ban last sender
/ban @user            → ban @user
/ban @user You've violated rule 3.
```

---

#### `/unban`
Unban by User ID (the only way to unban since they've left the server).

```
/unban 123456789012345678
```

---

### 👢 Other Actions

#### `/kick`
Kick with optional DM. Defaults to last sender.

```
/kick               → kick last sender
/kick @user         → kick @user
/kick @user Please read the rules and rejoin.
```

---

#### `/freeze`
Discord Timeout (temp mute). User can't speak, react, or join VCs.

| Argument | Default | Description |
|----------|---------|-------------|
| `user` | _last sender_ | Who to freeze |
| `duration` | `10m` | How long (e.g. `5m`, `1h`, `1d`, max 28 days) |
| `reason` | _none_ | Optional reason |

```
/freeze                  → freeze last sender for 10 minutes
/freeze @user            → freeze @user for 10 minutes
/freeze @user 1h         → freeze for 1 hour
/freeze @user 30m Calm down please.
```

---

#### `/unfreeze`
Remove a timeout early.

---

### 📢 Channel Controls

#### `/slow`
Toggle slowmode on/off — or set a specific delay with an optional auto-disable timer.

| Argument | Default | Description |
|----------|---------|-------------|
| `delay` | _toggle_ | Delay e.g. `30s`, `2m`, `5m` (max 5 min) |
| `length` | _indefinite_ | Auto-disable after e.g. `10m`, `1h`, `3d` (max 7 days) |

```
/slow              → if slowmode is on, disable it. If off, enable at 60s.
/slow 30s          → set to 30 second slowmode
/slow 2m 1h        → 2 minute slowmode, auto-removes in 1 hour
```

Auto-disable survives bot restarts.

---

#### `/lock`
Toggle @everyone's ability to send messages in a channel. Run again to unlock.

```
/lock                → lock current channel
/lock #general       → lock a specific channel
/lock #general Raid happening, locking for safety.
```

---

#### `/purge`
Bulk delete the last X messages (1–100). Optional user filter.

```
/purge 10             → delete last 10 messages
/purge 50 @user       → delete last 50 messages from @user only
```

---

### 🔎 Info & Notes

#### `/whois`
User info card, designed to be readable on mobile. Shows ID, join date, account age, roles, timeout status, and badges.

```
/whois              → your own info
/whois @user        → info on @user
```

Also shows a note count if that user has mod notes on file.

---

#### `/last`
Show (or confirm) who last sent a message in the current channel — this is who commands like `/kick` with no args will target.

---

#### `/note`
Add an internal mod note about a user. Notes are stored in JSON and never visible to the user.

```
/note @user Warned about spam in #general.
```

---

#### `/notes`
View all notes for a user (shows last 8, mod-only).

---

#### `/clearnotes`
Wipe all notes for a user (admin-only).

---

### 🏷️ Tags

Tags are saved text snippets. Use them to DM yourself (or someone else) a quick piece of text — rules, warnings, FAQs, template messages, etc.

- **Personal tags** — only you can see and use them
- **Global tags** — anyone on the server can use them (mod-only creation)

#### `/tag` or `/tag list`
List your personal tags and the server's global tags.

#### `/tag create <name> <content>`
Create a personal tag.
```
/tag create rules Please read #rules before posting.
```

#### `/tag global <name> <content>` _(mods only)_
Create a global server tag anyone can use.
```
/tag global warn This message is a formal warning. Further violations may result in a ban.
```

#### `/tag use <name> [dm_user]`
DM yourself a tag. Or DM it to someone else.
```
/tag use rules              → DMs you the "rules" tag
/tag use warn @troublemaker → DMs the tag to @troublemaker
```

#### `/tag edit <name> <new_content>`
Update a tag's content.

#### `/tag delete <name>`
Delete a personal tag (or a global tag if you're a mod).

---

### ⚙️ Configuration

#### `/prefix [new_prefix]`
View or change the bot's prefix for this server. Leave blank to see the current prefix.

```
/prefix        → shows current prefix
/prefix ?      → changes prefix to ?
```

Only administrators can change the prefix.

---

## Data Storage

NanoBot stores everything in the `data/` folder as JSON files:

| File | Contents |
|------|----------|
| `prefixes.json` | Per-guild custom prefixes |
| `tags.json` | Personal and global tags per guild |
| `notes.json` | Mod notes per user per guild |
| `unban_schedules.json` | Pending timed unbans (survives restarts) |
| `slow_schedules.json` | Pending timed slowmode removals (survives restarts) |

All files are human-readable and easy to edit or back up. No migrations, no setup.

---

## Philosophy

NanoBot is:

- **Small** — one Python file per concern, no over-engineering
- **Fast** — JSON reads are instant at this scale
- **Intentional** — every feature exists because mobile mods actually need it
- **Honest** — no fake "enterprise" features, no upsell

Not enterprise. Not bloated. Just useful.

---

## Contributing

Pull requests welcome. Keep the spirit of the project in mind: if a new command doesn't make moderation on mobile easier, it probably doesn't belong here.

---

## License

MIT — do whatever you want, just don't remove the credits.
