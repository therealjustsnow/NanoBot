# Utility Commands

Server info, reminders, tags, role panels, leveling, economy, and everything in between.

**31 commands.** Syntax: `[]` = optional, `<>` = required. Unless noted otherwise every command works as a slash command, a prefix command (default `n!`), and an @mention command.

**On this page:** [Tags](#tags) · [Reminders](#reminders) · [Recurring Reminders](#recurring-reminders) · [Config & Info](#config--info) · [Voting](#voting)

## Tags

Reusable text snippets and image tags, including fast mobile shorthand.

### `tag`

Manage and use tags. /tag list, /tag create, /tag use, etc.

- **Usage:** `/tag [shorthand or subcommand]` · `n!tag [shorthand or subcommand]`
- **Example:** `n!tag + rules | Read #rules before posting! | n!rules`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `tag create`

Create a personal tag with optional image.

- **Usage:** `/tag create <name> [content] [image] [image_url] or n!tag create <name> [content] [image] [image_url]`
- **Example:** `/tag create <name> [content] [image] [image_url] | n!tag create <name> [content] [image] [image_url]`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `tag delete`

Delete a tag (personal, or global if you're a mod).

- **Usage:** `/tag delete <name> or n!tag delete <name>`
- **Example:** `/tag delete <name> | n!tag delete <name>`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `tag edit`

Edit a tag's content and/or image.

- **Usage:** `/tag edit <name> [new_content] [image] [image_url] or n!tag edit <name> [new_content] [image] [image_url]`
- **Example:** `/tag edit <name> [new_content] [image] [image_url] | n!tag edit <name> [new_content] [image] [image_url]`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `tag export`

Download all your personal tags as a JSON file you can re-import later.

- **Usage:** `/tag export or n!tag export`
- **Example:** `/tag export | n!tag export`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `tag global`

Create a global server tag usable by anyone. Mods only.

- **Usage:** `/tag global <name> [content] [image] [image_url] or n!tag global <name> [content] [image] [image_url]`
- **Example:** `/tag global <name> [content] [image] [image_url] | n!tag global <name> [content] [image] [image_url]`
- **Permission:** Manage Messages
- **Access:** Restricted · Slash + prefix

### `tag import`

Import personal tags from a file exported by /tag export.

- **Usage:** `/tag import [file] or n!tag import [file]`
- **Example:** `/tag import [file] | n!tag import [file]`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `tag list`

List your personal tags and global server tags.

- **Usage:** `/tag list or n!tag list`
- **Example:** `/tag list | n!tag list`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `tag preview`

Preview a tag — only you see this response.

- **Usage:** `/tag preview <name> or n!tag preview <name>`
- **Example:** `/tag preview <name> | n!tag preview <name>`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `tag use`

Post a tag in this channel, or DM it to a specific user.

- **Usage:** `/tag use <name> [dm_user] or n!tag use <name> [dm_user]`
- **Example:** `/tag use <name> [dm_user] | n!tag use <name> [dm_user]`
- **Permission:** None
- **Access:** Public · Slash + prefix

## Reminders

One-time reminder flow plus reminder listing and cancel actions.

### `every`

Set a recurring reminder — like a repeating calendar event.

- **Usage:** `/every <interval> <message> [label] [dm]` · `n!every <interval> <message> [label] [dm]`
- **Example:** `n!every 2w Payday! | n!every daily Stand up meeting`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `recurring`

Manage your recurring reminders — list, pause, resume, or cancel.

- **Usage:** `/recurring [pause` · `resume` · `cancel <id>]` · `n!recurring [pause` · `resume` · `cancel <id>]`
- **Example:** `n!recurring | n!recurring pause abc123 | n!recurring cancel abc123`
- **Permission:** None
- **Aliases:** `repeating`, `repeat`
- **Access:** Public · Slash + prefix

### `remind`

Set a reminder for another user.

- **Usage:** `/remind <@user> <message with duration>` · `n!remind <@user> <message with duration>`
- **Example:** `n!remind @user check that PR 2h`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `reminders`

List your active reminders, or cancel one.

- **Usage:** `/reminders [cancel <number>]` · `n!reminders [cancel <number>]`
- **Example:** `n!reminders cancel 2`
- **Permission:** None
- **Aliases:** `reminder`
- **Access:** Public · Slash + prefix

### `reminders cancel`

Cancel an active reminder by its list number.

- **Usage:** `/reminders cancel <number> or n!reminders cancel <number>`
- **Example:** `/reminders cancel <number> | n!reminders cancel <number>`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `reminders list`

List your active reminders.

- **Usage:** `/reminders list or n!reminders list`
- **Example:** `/reminders list | n!reminders list`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `remindme`

Set a reminder for yourself. Duration can be part of the message.

- **Usage:** `/remindme <message with duration>` · `n!remindme <message with duration>`
- **Example:** `n!remindme stand up in 1 hour`
- **Permission:** None
- **Aliases:** `rm`
- **Access:** Public · Slash + prefix

## Recurring Reminders

Repeating reminders with pause, resume, and cancel flow.

### `recurring cancel`

Permanently delete a recurring reminder.

- **Usage:** `/recurring cancel <reminder_id> or n!recurring cancel <reminder_id>`
- **Example:** `/recurring cancel <reminder_id> | n!recurring cancel <reminder_id>`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `recurring list`

List all your recurring reminders.

- **Usage:** `/recurring list or n!recurring list`
- **Example:** `/recurring list | n!recurring list`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `recurring pause`

Pause a recurring reminder — it won't fire until you resume it.

- **Usage:** `/recurring pause <reminder_id> or n!recurring pause <reminder_id>`
- **Example:** `/recurring pause <reminder_id> | n!recurring pause <reminder_id>`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `recurring resume`

Resume a paused recurring reminder.

- **Usage:** `/recurring resume <reminder_id> or n!recurring resume <reminder_id>`
- **Example:** `/recurring resume <reminder_id> | n!recurring resume <reminder_id>`
- **Permission:** None
- **Access:** Public · Slash + prefix

## Config & Info

Prefix, help, status, server info, and general bot utility.

### `about`

What NanoBot is and why it exists.

- **Usage:** `/about` · `n!about`
- **Example:** `n!about`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `help`

Command reference. Use /help <command> for detail, or /help <category> to browse.

- **Usage:** `/help [command` · `category` · `page]` · `n!help [command` · `category` · `page]`
- **Example:** `n!help | n!help ban | n!help banning | n!help 3`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `invite`

Get NanoBot's invite link with the correct permissions.

- **Usage:** `/invite` · `n!invite`
- **Example:** `n!invite`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `ping`

Check NanoBot's latency.

- **Usage:** `/ping` · `n!ping`
- **Example:** `n!ping`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `prefix`

View or change NanoBot's prefix for this server.

- **Usage:** `/prefix [new_prefix]` · `n!prefix [new_prefix]`
- **Example:** `n!prefix ?`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `source`

Show the source code for a bot command. The embed title links to the exact lines on GitHub.

- **Usage:** `/source <command>` · `n!source <command>`
- **Example:** `n!source ban`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `stats`

NanoBot runtime statistics since last start.

- **Usage:** `/stats` · `n!stats`
- **Example:** `n!stats`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `support`

Get a link to the NanoBot support server.

- **Usage:** `/support` · `n!support`
- **Example:** `n!support`
- **Permission:** None
- **Aliases:** `helpserver`
- **Access:** Public · Slash + prefix

## Voting

Bot list vote tracking and vote status checks.

### `vote`

Vote for NanoBot on bot lists and see your voting status.

- **Usage:** `/vote [notify [on` · `off]]` · `n!vote [notify [on` · `off]]`
- **Example:** `n!vote | n!vote notify off`
- **Permission:** None
- **Access:** Public · Slash + prefix
