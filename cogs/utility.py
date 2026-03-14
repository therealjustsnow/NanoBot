"""
cogs/utility.py
Bot utility & configuration commands.

Commands:
  help    — !help (overview) or !help <cmd> (detailed)
  prefix  — view / change the guild prefix
  ping    — latency check
  info    — runtime stats
  invite  — bot invite link with correct permissions
  about   — what NanoBot is and why it exists
"""

import logging
import platform
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import helpers as h

log = logging.getLogger("NanoBot.utility")


# ── Help data ──────────────────────────────────────────────────────────────────
# Structured so both the overview and per-command detail views share one source.
# Fields: name, aliases, usage, short, desc, args (list of (name, desc)), perms, example
# ──────────────────────────────────────────────────────────────────────────────

_HELP = {
    "🔨 Banning": [
        {
            "name": "cban",
            "aliases": ["cleanban"],
            "usage": "cban [user] [days] [wait] [message]",
            "short": "Ban + wipe message history + optional timed unban",
            "desc": (
                "The mobile mod's best friend. Always deletes message history (1–7 days), "
                "optionally DMs the user, and optionally auto-unbans after a set time. "
                "Use this when someone spammed or posted bad content and you need the evidence gone too. "
                "Defaults to the last message sender if no user is given."
            ),
            "args": [
                ("user", "Who to ban (blank = last sender)"),
                ("days", "Days of message history to delete (1–7, default 7)"),
                (
                    "wait",
                    "Auto-unban after e.g. `30m`, `1h`, `7d` (omit for permanent)",
                ),
                ("message", "DM to send the user (omit for default)"),
            ],
            "perms": "Ban Members",
            "example": "!cban @user 7 24h See you tomorrow.",
        },
        {
            "name": "ban",
            "aliases": [],
            "usage": "ban [user] [message]",
            "short": "Permanently ban a user with optional DM",
            "desc": "Permanent ban with no message history deletion. Targets last sender if no user is specified.",
            "args": [
                ("user", "Who to ban (blank = last sender)"),
                ("message", "DM to send (omit for default)"),
            ],
            "perms": "Ban Members",
            "example": "!ban @user You have been permanently banned.",
        },
        {
            "name": "massban",
            "aliases": [],
            "usage": "massban <id1 id2 ...> [reason]",
            "short": "Ban multiple users by ID at once",
            "desc": "Paste a space-separated list of user IDs. Maximum 50 per command. Useful after a raid.",
            "args": [
                ("user_ids", "Space-separated list of user IDs to ban"),
                ("reason", "Reason applied to all bans"),
            ],
            "perms": "Ban Members",
            "example": "!massban 111 222 333 Raid cleanup",
        },
        {
            "name": "unban",
            "aliases": [],
            "usage": "unban <user_id> [reason]",
            "short": "Unban a user by their ID",
            "desc": "Unbans by User ID. Enable Developer Mode → right-click any user → Copy ID.",
            "args": [
                ("user_id", "The user's Discord ID"),
                ("reason", "Optional reason (shown in audit log)"),
            ],
            "perms": "Ban Members",
            "example": "!unban 123456789012345678",
        },
        {
            "name": "tempban",
            "aliases": [],
            "usage": "tempban [user] [duration] [reason]",
            "short": "Timed ban — no message deletion, just a duration",
            "desc": (
                "Simple timed ban with no history deletion. Use this when you want to cool someone off "
                "for a set time without touching their messages. "
                "Auto-unban survives restarts. Defaults to last sender if no user given.\n\n"
                "**vs /cban:** cban always deletes message history — use it when content needs to be wiped. "
                "tempban leaves messages intact."
            ),
            "args": [
                ("user", "Who to ban (blank = last sender)"),
                ("duration", "How long: `1h`, `12h`, `7d` (default 24h, min 1 minute)"),
                ("reason", "Optional reason"),
            ],
            "perms": "Ban Members",
            "example": "!tempban @user 3d Repeated rule violations",
        },
    ],
    "👢 Kicking & Timeouts": [
        {
            "name": "kick",
            "aliases": [],
            "usage": "kick [user] [message]",
            "short": "Kick a user — they can rejoin",
            "desc": "Kicks with an optional DM. Targets last sender if no user specified.",
            "args": [
                ("user", "Who to kick (blank = last sender)"),
                ("message", "DM to send (omit for default)"),
            ],
            "perms": "Kick Members",
            "example": "!kick @user Please review the rules.",
        },
        {
            "name": "freeze",
            "aliases": [],
            "usage": "freeze [user] [duration] [reason]",
            "short": "Timeout a user (default 10m)",
            "desc": "Discord Timeout — they can't speak, react, or join VCs. Max 28 days.",
            "args": [
                ("user", "Who to freeze (blank = last sender)"),
                ("duration", "`5m`, `1h`, `1d` (default 10m, max 28 days)"),
                ("reason", "Optional reason"),
            ],
            "perms": "Moderate Members",
            "example": "!freeze @user 30m Please cool down.",
        },
        {
            "name": "unfreeze",
            "aliases": [],
            "usage": "unfreeze <user>",
            "short": "Remove a timeout early",
            "desc": "Removes an active Discord Timeout from a user before it expires.",
            "args": [("user", "User to unfreeze (required)")],
            "perms": "Moderate Members",
            "example": "!unfreeze @user",
        },
    ],
    "📢 Channel Controls": [
        {
            "name": "slow",
            "aliases": [],
            "usage": "slow [delay] [length]",
            "short": "Toggle or set slowmode with optional auto-disable",
            "desc": "No args = toggle. With delay = set slowmode. With length = auto-disable after that time (survives restarts).",
            "args": [
                (
                    "delay",
                    "Slowmode delay: `30s`, `2m`, `5m` (max 5 min). Omit to toggle.",
                ),
                ("length", "Auto-disable after: `10m`, `1h`, `3d` (max 7 days)."),
            ],
            "perms": "Manage Channels",
            "example": "!slow 2m 1h",
        },
        {
            "name": "lock",
            "aliases": [],
            "usage": "lock [channel] [reason]",
            "short": "Toggle @everyone channel lock",
            "desc": "Prevents @everyone from sending messages. Run again to unlock.",
            "args": [
                ("channel", "Channel to lock (default: current)"),
                ("reason", "Optional reason in audit log"),
            ],
            "perms": "Manage Channels",
            "example": "!lock #general Temporary lock during raid.",
        },
        {
            "name": "purge",
            "aliases": [],
            "usage": "purge <amount> [bots] [user] [contains] [starts_with] [ends_with]",
            "short": "Bulk delete with optional filters (1–100)",
            "desc": "Deletes up to 100 messages. Combine filters: bots only, by user (ID/mention/nickname), text matching.",
            "args": [
                ("amount", "Number of messages to scan (1–100, required)"),
                ("bots", "Only delete bot messages"),
                ("user", "Only delete from this user (mention, ID, or nickname)"),
                ("contains", "Only messages containing this text"),
                ("starts_with", "Only messages starting with this text"),
                ("ends_with", "Only messages ending with this text"),
            ],
            "perms": "Manage Messages",
            "example": "!purge 50 user:@spammer",
        },
        {
            "name": "snailpurge",
            "aliases": [],
            "usage": "snailpurge <amount>",
            "short": "Slow delete up to 500 messages — no 14-day limit",
            "desc": "Deletes messages one-by-one (~80/min) so it works on messages older than 14 days. Requires a confirmation code. Sends a private warning before starting.",
            "args": [("amount", "Number of messages to delete (1–500)")],
            "perms": "Manage Messages",
            "example": "!snailpurge 200",
        },
        {
            "name": "clean",
            "aliases": [],
            "usage": "clean [amount]",
            "short": "Delete NanoBot's own recent messages",
            "desc": "Removes NanoBot's own messages from the channel. Good for tidying up after a command spam session.",
            "args": [("amount", "Messages to scan (1–100, default 50)")],
            "perms": "Manage Messages",
            "example": "!clean 20",
        },
        {
            "name": "nuke",
            "aliases": [],
            "usage": "nuke [reason]",
            "short": "Wipe a channel — clones it then deletes the original",
            "desc": "Recreates the channel with identical settings and permissions, deleting all message history. Requires button confirmation. Cannot be undone.",
            "args": [("reason", "Optional reason (shown in audit log)")],
            "perms": "Manage Channels",
            "example": "!nuke raid cleanup",
        },
        {
            "name": "hide",
            "aliases": [],
            "usage": "hide [channel]",
            "short": "Hide a channel from @everyone",
            "desc": "Sets view_channel=False for @everyone on the target channel. Use /unhide to reverse.",
            "args": [("channel", "Channel to hide (default: current channel)")],
            "perms": "Manage Channels",
            "example": "!hide #staff-only",
        },
        {
            "name": "unhide",
            "aliases": [],
            "usage": "unhide [channel]",
            "short": "Restore @everyone visibility on a hidden channel",
            "desc": "Resets the view_channel override for @everyone. Use /hide to hide again.",
            "args": [("channel", "Channel to unhide (default: current channel)")],
            "perms": "Manage Channels",
            "example": "!unhide #announcements",
        },
        {
            "name": "echo",
            "aliases": [],
            "usage": "echo [channel] <message>",
            "short": "Send a message as NanoBot",
            "desc": "Posts a message in the current or specified channel. Prefix mode deletes your trigger message for a cleaner look.",
            "args": [
                ("channel", "Where to send it (default: current channel)"),
                ("message", "The text to send"),
            ],
            "perms": "Manage Messages",
            "example": "!echo #announcements Server maintenance in 10 minutes!",
        },
        {
            "name": "moveall",
            "aliases": [],
            "usage": "moveall <to_channel> [from_channel]",
            "short": "Move all VC members from one channel to another",
            "desc": "Moves every member from the source VC to the destination. If no source is given, uses your current voice channel.",
            "args": [
                ("to_channel", "Destination voice channel"),
                ("from_channel", "Source voice channel (blank = your current VC)"),
            ],
            "perms": "Move Members",
            "example": "!moveall #General",
        },
    ],
    "🎭 Roles": [
        {
            "name": "addrole",
            "aliases": ["ar", "giverole"],
            "usage": "addrole <user> <role>",
            "short": "Give a role to a user",
            "desc": "Assigns a role to a user. The role must be below NanoBot's top role.",
            "args": [
                ("user", "User to give the role to"),
                ("role", "Role to assign (mention or name)"),
            ],
            "perms": "Manage Roles",
            "example": "!addrole @user Verified",
        },
        {
            "name": "removerole",
            "aliases": ["rr", "takerole"],
            "usage": "removerole <user> <role>",
            "short": "Remove a role from a user",
            "desc": "Removes a role from a user. The role must be below NanoBot's top role.",
            "args": [
                ("user", "User to remove the role from"),
                ("role", "Role to remove (mention or name)"),
            ],
            "perms": "Manage Roles",
            "example": "!removerole @user Muted",
        },
    ],
    "⚠️ Warnings": [
        {
            "name": "warn",
            "aliases": [],
            "usage": "warn <user> [reason]",
            "short": "Warn a user — configurable auto-kick/ban thresholds",
            "desc": "Issues a warning. Auto-actions (kick/ban) trigger at configurable thresholds set via /warnconfig.",
            "args": [("user", "User to warn"), ("reason", "Reason for the warning")],
            "perms": "Manage Messages",
            "example": "!warn @user Spamming in #general",
        },
        {
            "name": "warnings",
            "aliases": [],
            "usage": "warnings <user>",
            "short": "View all warnings for a user",
            "desc": "Shows the last 8 warnings for a user with date and moderator.",
            "args": [("user", "User to look up")],
            "perms": "Manage Messages",
            "example": "!warnings @user",
        },
        {
            "name": "clearwarnings",
            "aliases": [],
            "usage": "clearwarnings <user>",
            "short": "Clear all warnings for a user (admin only)",
            "desc": "Permanently wipes all warnings for a user from this server.",
            "args": [("user", "User whose warnings to clear")],
            "perms": "Administrator",
            "example": "!clearwarnings @user",
        },
        {
            "name": "warnconfig",
            "aliases": [],
            "usage": "warnconfig [kick_at] [ban_at] [dm_user]",
            "short": "Configure auto-actions for warnings",
            "desc": "No args: shows current config. Set kick_at/ban_at to 0 to disable. Auto-kick must be lower than auto-ban.",
            "args": [
                ("kick_at", "Auto-kick after this many warnings (0 = disabled)"),
                ("ban_at", "Auto-ban after this many warnings (0 = disabled)"),
                ("dm_user", "DM users when they are warned (yes/no)"),
            ],
            "perms": "Administrator",
            "example": "!warnconfig kick_at:3 ban_at:5",
        },
    ],
    "🔎 Info & Notes": [
        {
            "name": "last",
            "aliases": [],
            "usage": "last",
            "short": "Show who last sent a message here",
            "desc": "Displays who last sent a message in this channel — the default target for /kick, /ban, /freeze, etc.",
            "args": [],
            "perms": "None",
            "example": "!last",
        },
        {
            "name": "note",
            "aliases": [],
            "usage": "note <user> <content>",
            "short": "Add a private mod note (invisible to the user)",
            "desc": "Saves an internal note about a user. The user never sees these.",
            "args": [
                ("user", "User to attach the note to"),
                ("content", "Note content (max 1000 chars)"),
            ],
            "perms": "Manage Messages",
            "example": "!note @user Warned about spam in #general.",
        },
        {
            "name": "notes",
            "aliases": [],
            "usage": "notes <user>",
            "short": "View mod notes for a user",
            "desc": "Shows up to 8 of the most recent mod notes. Only visible to you (ephemeral).",
            "args": [("user", "User to look up")],
            "perms": "Manage Messages",
            "example": "!notes @user",
        },
        {
            "name": "clearnotes",
            "aliases": [],
            "usage": "clearnotes <user>",
            "short": "Delete all notes for a user (admin only)",
            "desc": "Permanently wipes all mod notes for a user.",
            "args": [("user", "User whose notes to clear")],
            "perms": "Administrator",
            "example": "!clearnotes @user",
        },
        {
            "name": "channelinfo",
            "aliases": ["ci", "channel"],
            "usage": "channelinfo [channel]",
            "short": "Info card for a channel",
            "desc": "Shows channel type, ID, category, creation date, position, NSFW status, slowmode, and topic.",
            "args": [("channel", "Channel to inspect (default: current channel)")],
            "perms": "None",
            "example": "!channelinfo #general",
        },
    ],
    "🏷️ Tags": [
        {
            "name": "tag",
            "aliases": [],
            "usage": "tag [shorthand or subcommand]",
            "short": "Post text snippets in channel, or DM — personal or server-wide",
            "desc": (
                "Tags let you save text (and images) and fire them instantly.\n\n"
                "**Shorthand:**\n"
                "`n!tag <n>` — post tag\n"
                "`n!<n>` — same, even shorter\n"
                "`n!tag + <n> | <content>` — create personal tag\n"
                "`n!tag - <n>` — delete tag\n"
                "`n!tag g+ <n> | <content>` — create global tag (mods)\n\n"
                "**Subcommands:**\n"
                "`/tag create`, `/tag global`, `/tag use`, `/tag preview`,\n"
                "`/tag edit`, `/tag delete`, `/tag list`, `/tag export`"
            ),
            "args": [],
            "perms": "None (global creation requires Manage Messages)",
            "example": "!tag + rules | Read #rules before posting!\n!rules",
        },
    ],
    "👋 Welcome & Leave": [
        {
            "name": "welcome",
            "aliases": [],
            "usage": "welcome set [enabled] [channel] [title] [content] [image_url] [dm]",
            "short": "Configure welcome messages for new members",
            "desc": "Posts an embed when a user joins. Supports custom title, content, image, and DM mode. Variables: {user}, {mention}, {server}, {count}.",
            "args": [
                ("enabled", "Enable or disable welcome messages"),
                ("channel", "Channel to post in (blank = system channel)"),
                ("title", "Embed title — supports {user}, {server}"),
                (
                    "content",
                    "Message body — supports {user}, {mention}, {server}, {count}",
                ),
                ("image_url", "Image URL to show in the embed"),
                ("dm", "DM the joining user instead of posting in channel"),
            ],
            "perms": "Administrator",
            "example": "!welcome set enabled:True channel:#welcome content:Welcome {mention}!",
        },
        {
            "name": "leave",
            "aliases": [],
            "usage": "leave set [enabled] [channel] [title] [content] [image_url] [dm]",
            "short": "Configure leave messages when members depart",
            "desc": "Posts an embed when a user leaves. Same options as /welcome.",
            "args": [
                ("enabled", "Enable or disable leave messages"),
                ("channel", "Channel to post in (blank = system channel)"),
                ("title", "Embed title"),
                ("content", "Message body"),
                ("image_url", "Image URL"),
                ("dm", "DM the leaving user instead"),
            ],
            "perms": "Administrator",
            "example": "!leave set enabled:True content:Goodbye {user}!",
        },
    ],
    "🔍 Server & User Info": [
        {
            "name": "server",
            "aliases": ["serverinfo", "si", "guild"],
            "usage": "server",
            "short": "Full server info card",
            "desc": "Member counts, boost level, channel breakdown, features, creation date and more.",
            "args": [],
            "perms": "None",
            "example": "!server",
        },
        {
            "name": "user",
            "aliases": ["userinfo", "ui", "member"],
            "usage": "user [user]",
            "short": "Public user info — status, roles, badges",
            "desc": "Shows a clean user card with status, activity, join date, account age, roles and badges. Mods also see note count.",
            "args": [("user", "User to look up (blank = yourself)")],
            "perms": "None",
            "example": "!user @someone",
        },
        {
            "name": "avatar",
            "aliases": ["av", "pfp", "icon"],
            "usage": "avatar [user]",
            "short": "Show a user's avatar full-size",
            "desc": "Fetches the avatar at 1024px with PNG/JPG/WEBP/GIF download links.",
            "args": [("user", "Whose avatar to show (blank = yourself)")],
            "perms": "None",
            "example": "!avatar @someone",
        },
        {
            "name": "banner",
            "aliases": ["userbanner"],
            "usage": "banner [user]",
            "short": "Show a user's profile banner",
            "desc": "Fetches and displays the user's profile banner with download links.",
            "args": [("user", "Whose banner to show (blank = yourself)")],
            "perms": "None",
            "example": "!banner @someone",
        },
        {
            "name": "roleinfo",
            "aliases": ["role", "ri"],
            "usage": "roleinfo <role>",
            "short": "Details about a server role",
            "desc": "Color, position, member count, creation date, hoist/mentionable status, and notable permissions.",
            "args": [("role", "Mention it or type the name")],
            "perms": "None",
            "example": "!roleinfo @Moderator",
        },
        {
            "name": "uptime",
            "aliases": [],
            "usage": "uptime",
            "short": "How long the bot has been running",
            "desc": "Shows how long NanoBot has been online since its last start or restart.",
            "args": [],
            "perms": "None",
            "example": "!uptime",
        },
    ],
    "⏰ Reminders": [
        {
            "name": "remindme",
            "aliases": ["rm"],
            "usage": "remindme <message with duration>",
            "short": "Set a reminder for yourself",
            "desc": "Remind yourself about something. Put the duration at the end of your message. Delivered by DM, falls back to channel ping.",
            "args": [
                (
                    "message",
                    "What to remind you about — put the duration at the end (e.g. stand up 1h)",
                )
            ],
            "perms": "None",
            "example": "!remindme stand up in 1 hour",
        },
        {
            "name": "remind",
            "aliases": [],
            "usage": "remind <@user> <message with duration>",
            "short": "Set a reminder for another user",
            "desc": "Remind someone else. Posts a channel ping by default; use dm=yes to DM them.",
            "args": [
                ("user", "Who to remind"),
                ("message", "What to remind them about — duration at the end"),
            ],
            "perms": "None",
            "example": "!remind @user check that PR 2h",
        },
        {
            "name": "reminders",
            "aliases": ["reminder"],
            "usage": "reminders [cancel <id>]",
            "short": "List or cancel your active reminders",
            "desc": "No args: lists all your active reminders. `cancel <id>`: cancels that reminder.",
            "args": [("id", "6-character reminder ID shown when the reminder was set")],
            "perms": "None",
            "example": "!reminders cancel abc123",
        },
        {
            "name": "every",
            "aliases": [],
            "usage": "every <interval> <message> [label] [dm]",
            "short": "Set a recurring reminder — fires repeatedly on a schedule",
            "desc": (
                "Like a repeating calendar event. Set it once and NanoBot will remind you on that interval forever "
                "(until you pause or cancel it). Survives bot restarts. If the bot was offline when a fire was due, "
                "it fires once on restore — no catch-up spam.\n\n"
                "**Interval presets** (autocomplete on `/every`):\n"
                "`hourly` · `daily` · `weekly` · `biweekly` · `monthly`\n\n"
                "**Custom intervals:** `2w` · `3d` · `6h` · `every 2 weeks`\n\n"
                "**Label tip:** Add a short label (e.g. `Payday`) so your `/recurring` list "
                "stays readable on mobile instead of showing a truncated message."
            ),
            "args": [
                (
                    "interval",
                    "How often to remind you — pick a preset or type your own (min 1 hour)",
                ),
                ("message", "What to remind you about (up to 500 characters)"),
                (
                    "label",
                    "Short display name shown in your list, e.g. 'Payday' (optional, max 50 chars)",
                ),
                (
                    "dm",
                    "DM you the reminder (default: yes, falls back to channel ping if DMs are closed)",
                ),
            ],
            "perms": "None",
            "example": "!every 2w Payday!\n!every daily Stand up meeting\n!every weekly Review my goals",
        },
        {
            "name": "recurring",
            "aliases": ["repeating", "repeat"],
            "usage": "recurring [pause|resume|cancel <id>]",
            "short": "List, pause, resume, or cancel your recurring reminders",
            "desc": (
                "No args: shows all your recurring reminders with their interval, next fire time, and status.\n\n"
                "**Subcommands:**\n"
                "`pause <id>` — stop a reminder from firing until you resume it\n"
                "`resume <id>` — re-enable a paused reminder (next fire = now + interval)\n"
                "`cancel <id>` — permanently delete the recurring reminder\n\n"
                "IDs are 6 characters, shown when you set the reminder and in the list."
            ),
            "args": [
                (
                    "id",
                    "6-character recurring reminder ID (from `/recurring` list or when set)",
                )
            ],
            "perms": "None",
            "example": "!recurring\n!recurring pause abc123\n!recurring resume abc123\n!recurring cancel abc123",
        },
    ],
    "📋 Audit Log": [
        {
            "name": "auditlog channel",
            "aliases": [],
            "usage": "/auditlog channel <#channel>",
            "short": "Set the channel for audit log entries",
            "desc": "Designates a text channel to receive audit log events. Run this first before enabling logging.",
            "args": [("channel", "The text channel to post audit events in")],
            "perms": "Manage Server",
            "example": "/auditlog channel #audit-log",
        },
        {
            "name": "auditlog enable",
            "aliases": [],
            "usage": "/auditlog enable",
            "short": "Turn audit logging on",
            "desc": "Enables the audit log feed. A channel must be set first with `/auditlog channel`.",
            "args": [],
            "perms": "Manage Server",
            "example": "/auditlog enable",
        },
        {
            "name": "auditlog disable",
            "aliases": [],
            "usage": "/auditlog disable",
            "short": "Turn audit logging off",
            "desc": "Stops all audit log entries from being posted. Config is preserved — re-enable with `/auditlog enable`.",
            "args": [],
            "perms": "Manage Server",
            "example": "/auditlog disable",
        },
        {
            "name": "auditlog events",
            "aliases": [],
            "usage": "/auditlog events",
            "short": "Toggle individual event types via a dropdown",
            "desc": (
                "Opens a multi-select dropdown to choose exactly which events get logged.\n\n"
                "**Available events:** Message Delete, Message Edit, Member Join, Member Leave, "
                "Member Ban, Member Unban, Nickname Change, Roles Updated, "
                "Channel Created, Channel Deleted, Role Created, Role Deleted."
            ),
            "args": [],
            "perms": "Manage Server",
            "example": "/auditlog events",
        },
        {
            "name": "auditlog status",
            "aliases": [],
            "usage": "/auditlog status",
            "short": "Show current audit log configuration",
            "desc": "Displays the log channel, enabled/disabled state, and the full list of active event types.",
            "args": [],
            "perms": "Manage Server",
            "example": "/auditlog status",
        },
    ],
    "🛡️ Auto Mod": [
        {
            "name": "automod enable",
            "aliases": [],
            "usage": "/automod enable",
            "short": "Turn auto-moderation on",
            "desc": "Master switch — enables all configured auto-mod rules. Individual rules can still be toggled independently with `/automod rule`.",
            "args": [],
            "perms": "Manage Server",
            "example": "/automod enable",
        },
        {
            "name": "automod disable",
            "aliases": [],
            "usage": "/automod disable",
            "short": "Turn auto-moderation off",
            "desc": "Master switch — disables all auto-mod rules without deleting configuration.",
            "args": [],
            "perms": "Manage Server",
            "example": "/automod disable",
        },
        {
            "name": "automod rule",
            "aliases": [],
            "usage": "/automod rule <rule> <enabled> [action]",
            "short": "Toggle a rule on/off and set its action",
            "desc": (
                "Enables or disables a specific rule and optionally sets what happens when it triggers.\n\n"
                "**Rules:** `spam`, `invites`, `links`, `caps`, `mentions`, `badwords`\n"
                "**Actions:** `delete` (silent), `warn` (delete + formal warning), `timeout` (delete + 10-min timeout)"
            ),
            "args": [
                ("rule", "Which rule to configure"),
                ("enabled", "True to enable, False to disable"),
                ("action", "What to do when triggered (delete / warn / timeout)"),
            ],
            "perms": "Manage Server",
            "example": "/automod rule invites True warn",
        },
        {
            "name": "automod spam",
            "aliases": [],
            "usage": "/automod spam <count> <seconds>",
            "short": "Set the spam detection threshold",
            "desc": "Triggers when a user sends `count` or more messages within `seconds`. Default: 5 messages in 5 seconds.",
            "args": [
                ("count", "Number of messages that trigger detection"),
                ("seconds", "Time window in seconds"),
            ],
            "perms": "Manage Server",
            "example": "/automod spam 5 5",
        },
        {
            "name": "automod caps",
            "aliases": [],
            "usage": "/automod caps <percent> <min_length>",
            "short": "Set the caps-abuse threshold",
            "desc": "Triggers when a message exceeds `percent`% uppercase characters. `min_length` sets the shortest message to check (avoids false positives on short replies).",
            "args": [
                ("percent", "Uppercase % threshold (e.g. 70)"),
                ("min_length", "Minimum message length to check (default 10)"),
            ],
            "perms": "Manage Server",
            "example": "/automod caps 70 10",
        },
        {
            "name": "automod mentions",
            "aliases": [],
            "usage": "/automod mentions <limit>",
            "short": "Set the per-message mention limit",
            "desc": "Triggers when a single message contains more than `limit` @mentions (including @everyone and @here).",
            "args": [("limit", "Max mentions allowed per message before action")],
            "perms": "Manage Server",
            "example": "/automod mentions 5",
        },
        {
            "name": "automod badword",
            "aliases": [],
            "usage": "/automod badword <add|remove|list> [word]",
            "short": "Manage the per-server word filter",
            "desc": (
                "Maintain a custom list of words that trigger the `badwords` rule.\n\n"
                "`add <word>` — add a word to the filter\n"
                "`remove <word>` — remove a word from the filter\n"
                "`list` — view all filtered words (ephemeral)"
            ),
            "args": [("word", "The word to add or remove")],
            "perms": "Manage Server",
            "example": "/automod badword add slur",
        },
        {
            "name": "automod ignore",
            "aliases": [],
            "usage": "/automod ignore <add|remove> <channel or role>",
            "short": "Exempt a channel or role from all auto-mod rules",
            "desc": "Messages in exempt channels or sent by users with exempt roles are completely ignored by all auto-mod rules.",
            "args": [
                ("action", "`add` or `remove`"),
                ("target", "The channel or role to exempt / un-exempt"),
            ],
            "perms": "Manage Server",
            "example": "/automod ignore add #staff-chat",
        },
        {
            "name": "automod status",
            "aliases": [],
            "usage": "/automod status",
            "short": "Full auto-mod configuration overview",
            "desc": "Shows whether auto-mod is enabled, every rule's state and action, spam/caps/mention thresholds, bad word count, and exempt channels/roles.",
            "args": [],
            "perms": "Manage Server",
            "example": "/automod status",
        },
    ],
    "🎛️ Role Panels": [
        {
            "name": "roles panel create",
            "aliases": [],
            "usage": "/roles panel create <name> [description] [mode]",
            "short": "Create a new self-role panel (not yet posted)",
            "desc": (
                "Creates a panel definition without posting it. Add roles with `/roles add`, then post it with `/roles panel post`.\n\n"
                "**Modes:** `toggle` (click to add/remove — default) · `single` (radio-style, picking one removes the others)"
            ),
            "args": [
                ("name", "Panel name shown in the embed title"),
                ("description", "Optional subtitle text"),
                ("mode", "`toggle` or `single` (default: toggle)"),
            ],
            "perms": "Manage Roles",
            "example": "/roles panel create Colours Pick your colour role!",
        },
        {
            "name": "roles panel post",
            "aliases": [],
            "usage": "/roles panel post <panel_name> [channel]",
            "short": "Post (or re-post) a panel to a channel",
            "desc": "Posts the panel as a persistent button embed. If the panel was already posted, the old message is deleted and a fresh one is sent. Buttons survive bot restarts.",
            "args": [
                ("panel_name", "Name of the panel to post"),
                ("channel", "Where to post it (default: current channel)"),
            ],
            "perms": "Manage Roles",
            "example": "/roles panel post Colours #roles",
        },
        {
            "name": "roles panel edit",
            "aliases": [],
            "usage": "/roles panel edit <panel_name> [title] [description] [mode]",
            "short": "Edit a panel's title, description, or mode",
            "desc": "Updates the panel definition and refreshes the live message if it has been posted.",
            "args": [
                ("panel_name", "Panel to edit"),
                ("title", "New title"),
                ("description", "New description"),
                ("mode", "`toggle` or `single`"),
            ],
            "perms": "Manage Roles",
            "example": "/roles panel edit Colours mode:single",
        },
        {
            "name": "roles panel delete",
            "aliases": [],
            "usage": "/roles panel delete <panel_name>",
            "short": "Delete a panel and remove its message",
            "desc": "Permanently deletes the panel config and attempts to delete the posted message. Cannot be undone.",
            "args": [("panel_name", "Panel to delete")],
            "perms": "Manage Roles",
            "example": "/roles panel delete Colours",
        },
        {
            "name": "roles panel list",
            "aliases": [],
            "usage": "/roles panel list",
            "short": "List all role panels in this server",
            "desc": "Shows every panel with its mode, role count, and whether it's currently posted.",
            "args": [],
            "perms": "Manage Roles",
            "example": "/roles panel list",
        },
        {
            "name": "roles add",
            "aliases": [],
            "usage": "/roles add <panel_name> <role> [label] [emoji]",
            "short": "Add a role button to a panel",
            "desc": "Appends a role to an existing panel. If the panel is posted, the live message updates automatically. Up to 25 roles per panel.",
            "args": [
                ("panel_name", "Panel to add the role to"),
                ("role", "The role to assign when clicked"),
                ("label", "Button label (default: role name)"),
                ("emoji", "Optional button emoji"),
            ],
            "perms": "Manage Roles",
            "example": "/roles add Colours @Red 🔴",
        },
        {
            "name": "roles remove",
            "aliases": [],
            "usage": "/roles remove <panel_name> <role>",
            "short": "Remove a role button from a panel",
            "desc": "Removes a role from the panel and refreshes the live message.",
            "args": [
                ("panel_name", "Panel to remove from"),
                ("role", "Role to remove"),
            ],
            "perms": "Manage Roles",
            "example": "/roles remove Colours @Red",
        },
        {
            "name": "roles autogen",
            "aliases": [],
            "usage": "/roles autogen <colors|pronouns|age|region> [extra roles]",
            "short": "Auto-generate a preset role set + panel",
            "desc": (
                "Creates roles and a ready-to-post panel in one command.\n\n"
                "**Presets:**\n"
                "`colors` — 18 cosmetic colour roles\n"
                "`pronouns` — She/Her · He/Him · They/Them\n"
                "`age` — age-range roles\n"
                "`region` — 7 world-region roles\n\n"
                "Up to 5 existing roles can be appended to any preset panel. "
                "Only one autogen can run at a time per server."
            ),
            "args": [
                ("preset", "`colors`, `pronouns`, `age`, or `region`"),
                ("extra roles", "Up to 5 existing roles to append (optional)"),
            ],
            "perms": "Manage Roles",
            "example": "/roles autogen colors",
        },
    ],
    "🗳️ Voting": [
        {
            "name": "vote",
            "aliases": [],
            "usage": "vote",
            "short": "Vote for NanoBot and see your voting status",
            "desc": (
                "Shows vote links for top.gg (12h cooldown) and discordbotlist.com (24h cooldown), "
                "your current cooldown countdown on each site, and your vote streak.\n\n"
                "**Voter perk:** active voters get **50 reminder slots** instead of 25.\n\n"
                "NanoBot will DM you when your cooldown resets so you never miss a vote. "
                "Turn pings off with `/vote notify off`."
            ),
            "args": [],
            "perms": "None",
            "example": "!vote",
        },
        {
            "name": "vote notify",
            "aliases": [],
            "usage": "vote notify [on|off]",
            "short": "Toggle vote cooldown DM reminders",
            "desc": (
                "Controls whether NanoBot DMs you when your vote cooldown resets on each site.\n\n"
                "`/vote notify` — show current setting\n"
                "`/vote notify on` — enable DM pings (default)\n"
                "`/vote notify off` — silence DM pings"
            ),
            "args": [
                (
                    "on/off",
                    "Enable or disable cooldown pings (omit to view current setting)",
                )
            ],
            "perms": "None",
            "example": "!vote notify off",
        },
    ],
    "⚙️ Config & Info": [
        {
            "name": "prefix",
            "aliases": [],
            "usage": "prefix [new_prefix]",
            "short": "View or change the bot prefix for this server",
            "desc": "Shows the current prefix with no args. With a new prefix (max 5 chars, no spaces), updates it server-wide.",
            "args": [
                ("new_prefix", "New prefix (1–5 chars, no spaces). Omit to view.")
            ],
            "perms": "Administrator (to change)",
            "example": "!prefix ?",
        },
        {
            "name": "ping",
            "aliases": [],
            "usage": "ping",
            "short": "Check NanoBot's response time",
            "desc": "Returns the current WebSocket latency between NanoBot and Discord's servers.",
            "args": [],
            "perms": "None",
            "example": "!ping",
        },
        {
            "name": "info",
            "aliases": [],
            "usage": "info",
            "short": "Bot stats and runtime info",
            "desc": "Shows latency, server count, prefix, discord.py version, Python version, and storage type.",
            "args": [],
            "perms": "None",
            "example": "!info",
        },
        {
            "name": "invite",
            "aliases": [],
            "usage": "invite",
            "short": "Get the bot invite link",
            "desc": "Generates an invite link with exactly the permissions NanoBot needs — no unnecessary extras.",
            "args": [],
            "perms": "None",
            "example": "!invite",
        },
        {
            "name": "support",
            "aliases": ["helpserver"],
            "usage": "support",
            "short": "Link to the NanoBot support server",
            "desc": "Posts an invite link to the official NanoBot support server.",
            "args": [],
            "perms": "None",
            "example": "!support",
        },
        {
            "name": "about",
            "aliases": [],
            "usage": "about",
            "short": "What NanoBot is and why it exists",
            "desc": "The NanoBot story — why it was built, what it avoids, and what makes it different.",
            "args": [],
            "perms": "None",
            "example": "!about",
        },
    ],
    "🔧 Owner / Admin": [
        {
            "name": "reload",
            "aliases": ["rl"],
            "usage": "reload [cog|all]",
            "short": "Hot-reload a cog or all cogs (owner only)",
            "desc": "Reloads without restarting. Accepts `all`, the full dotted name, or just the short name.",
            "args": [("cog", "Cog to reload, or `all` (default: all)")],
            "perms": "Bot Owner",
            "example": "!reload all\n!reload moderation",
        },
        {
            "name": "update",
            "aliases": ["pull"],
            "usage": "update",
            "short": "Git pull + reload all cogs (owner only)",
            "desc": (
                "Runs `git pull` and reports the output, then reloads all cogs. "
                "Does NOT restart the process — main.py changes won't take effect until `!restart`. "
                "Cog changes (moderation, tags, etc.) take effect immediately."
            ),
            "args": [],
            "perms": "Bot Owner",
            "example": "!update",
        },
        {
            "name": "setloglevel",
            "aliases": ["loglevel", "loglvl"],
            "usage": "setloglevel <level>",
            "short": "Change log verbosity live (owner only)",
            "desc": "Changes logging level immediately and saves to config.json.",
            "args": [("level", "DEBUG / INFO / WARNING / ERROR / CRITICAL")],
            "perms": "Bot Owner",
            "example": "!setloglevel DEBUG",
        },
        {
            "name": "logs",
            "aliases": ["log"],
            "usage": "logs [lines]",
            "short": "Tail the log file in Discord (owner only)",
            "desc": "Fetches the last N lines of `logs/nanobot.log` as an ephemeral embed.",
            "args": [("lines", "How many lines to show (1–50, default 20)")],
            "perms": "Bot Owner",
            "example": "!logs 30",
        },
        {
            "name": "restart",
            "aliases": ["reboot", "rs"],
            "usage": "restart",
            "short": "Gracefully restart the bot process (owner only)",
            "desc": "Closes cleanly, then re-executes the Python process with the same arguments.",
            "args": [],
            "perms": "Bot Owner",
            "example": "!restart",
        },
        {
            "name": "shutdown",
            "aliases": ["die", "stop"],
            "usage": "shutdown",
            "short": "Gracefully shut down (owner only)",
            "desc": "Flushes all logs, sends a goodbye message, and closes the Discord connection.",
            "args": [],
            "perms": "Bot Owner",
            "example": "!shutdown",
        },
    ],
}


def _flat_commands() -> dict[str, dict]:
    """Flat {name: cmd_dict} lookup for !help <cmd>."""
    out = {}
    for cmds in _HELP.values():
        for cmd in cmds:
            out[cmd["name"]] = cmd
            for alias in cmd.get("aliases", []):
                out[alias] = cmd
    return out


_FLAT = _flat_commands()


# ── Category keyword → full category name ─────────────────────────────────────
# Used by !help <category> to let users browse by topic without needing
# to type the exact emoji-prefixed category title.
_CATEGORY_ALIASES: dict[str, str] = {
    # 🔨 Banning
    "ban": "🔨 Banning",
    "banning": "🔨 Banning",
    "bans": "🔨 Banning",
    # 👢 Kicking & Timeouts
    "kick": "👢 Kicking & Timeouts",
    "kicking": "👢 Kicking & Timeouts",
    "timeout": "👢 Kicking & Timeouts",
    "timeouts": "👢 Kicking & Timeouts",
    "mute": "👢 Kicking & Timeouts",
    "freeze": "👢 Kicking & Timeouts",
    # 📢 Channel Controls
    "channel": "📢 Channel Controls",
    "channels": "📢 Channel Controls",
    "purge": "📢 Channel Controls",
    "lock": "📢 Channel Controls",
    "nuke": "📢 Channel Controls",
    "voice": "📢 Channel Controls",
    # 🎭 Roles
    "roles": "🎭 Roles",
    # ⚠️ Warnings
    "warn": "⚠️ Warnings",
    "warning": "⚠️ Warnings",
    "warnings": "⚠️ Warnings",
    # 🔎 Info & Notes
    "note": "🔎 Info & Notes",
    "notes": "🔎 Info & Notes",
    # 🏷️ Tags
    "tag": "🏷️ Tags",
    "tags": "🏷️ Tags",
    # 👋 Welcome & Leave
    "welcome": "👋 Welcome & Leave",
    "leave": "👋 Welcome & Leave",
    "join": "👋 Welcome & Leave",
    # 🔍 Server & User Info
    "server": "🔍 Server & User Info",
    "profile": "🔍 Server & User Info",
    "avatar": "🔍 Server & User Info",
    "userinfo": "🔍 Server & User Info",
    # ⏰ Reminders
    "reminder": "⏰ Reminders",
    "reminders": "⏰ Reminders",
    "remind": "⏰ Reminders",
    "recurring": "⏰ Reminders",
    "repeating": "⏰ Reminders",
    "repeat": "⏰ Reminders",
    "every": "⏰ Reminders",
    # 📋 Audit Log
    "auditlog": "📋 Audit Log",
    "audit": "📋 Audit Log",
    "log": "📋 Audit Log",
    "logging": "📋 Audit Log",
    "logs": "📋 Audit Log",
    # 🛡️ Auto Mod
    "automod": "🛡️ Auto Mod",
    "auto": "🛡️ Auto Mod",
    "automoderation": "🛡️ Auto Mod",
    "spam": "🛡️ Auto Mod",
    "filter": "🛡️ Auto Mod",
    "badwords": "🛡️ Auto Mod",
    # 🎛️ Role Panels
    "panels": "🎛️ Role Panels",
    "panel": "🎛️ Role Panels",
    "rolepanels": "🎛️ Role Panels",
    "selfroles": "🎛️ Role Panels",
    "selfrole": "🎛️ Role Panels",
    "autogen": "🎛️ Role Panels",
    # 🗳️ Voting
    "vote": "🗳️ Voting",
    "voting": "🗳️ Voting",
    "votes": "🗳️ Voting",
    "topgg": "🗳️ Voting",
    "dbl": "🗳️ Voting",
    # ⚙️ Config & Info
    "config": "⚙️ Config & Info",
    "settings": "⚙️ Config & Info",
    "utility": "⚙️ Config & Info",
    "general": "⚙️ Config & Info",
    # 🔧 Owner / Admin
    "admin": "🔧 Owner / Admin",
    "owner": "🔧 Owner / Admin",
    "reload": "🔧 Owner / Admin",
    "update": "🔧 Owner / Admin",
}


def _build_category_embed(cat_name: str, cmds: list, prefix: str) -> discord.Embed:
    """Single-embed view of all commands in one help category."""
    lines = []
    for cmd in cmds:
        entry = f"`/{cmd['name']}`"
        if cmd.get("aliases"):
            shown = cmd["aliases"][:2]
            entry += " _(also: " + ", ".join(f"`{a}`" for a in shown) + ")_"
        entry += f"  —  {cmd['short']}"
        if cmd.get("perms") and cmd["perms"] not in ("None", "Bot Owner"):
            entry += f"  · _{cmd['perms']}_"
        lines.append(entry)

    e = h.embed(title=cat_name, color=h.BLUE)
    e.description = "\n".join(lines)
    e.set_footer(
        text=f"Use `{prefix}help <command>` for full argument details  ·  NanoBot"
    )
    return e


_OWNER_CATEGORIES = {"🔧 Owner / Admin"}


def _build_help_pages(
    prefix: str, bot_name: str, *, is_owner: bool = False
) -> list[discord.Embed]:
    """
    Build one embed per help category, plus a cover page.
    Owner-only categories are hidden from non-owners.
    Returns a list of discord.Embed objects ready to display.
    """
    categories = [
        (cat, cmds)
        for cat, cmds in _HELP.items()
        if is_owner or cat not in _OWNER_CATEGORIES
    ]
    total = len(categories) + 1  # +1 for cover

    def footer(page_num):
        return "Page " + str(page_num) + " / " + str(total) + "  ·  NanoBot"

    pages = []

    # Cover page
    cover = h.embed(
        title=chr(9889) + " NanoBot " + chr(8212) + " Command Reference",
        description=(
            "Prefix: `"
            + prefix
            + "` · Slash `/` · @"
            + bot_name
            + chr(10)
            + "Most mod commands default to the **last message sender** if no user is given."
            + chr(10)
            + chr(10)
            + "`"
            + prefix
            + "help <command>` — full detail on any command"
            + chr(10)
            + "`"
            + prefix
            + "help <category>` — browse a category (e.g. `"
            + prefix
            + "help banning`)"
            + chr(10)
            + chr(10)
            + chr(10).join(
                "**"
                + cat
                + "** "
                + chr(8212)
                + " "
                + str(len(cmds))
                + " command"
                + ("s" if len(cmds) != 1 else "")
                for cat, cmds in categories
            )
        ),
        color=h.BLUE,
    )
    cover.set_footer(text=footer(1))
    pages.append(cover)

    # One page per category
    for i, (category, cmds) in enumerate(categories, start=2):
        lines = []
        for cmd in cmds:
            line = "`" + prefix + cmd["name"] + "`"
            if cmd.get("aliases"):
                line += (
                    " _(also: "
                    + ", ".join("`" + a + "`" for a in cmd["aliases"])
                    + ")_"
                )
            line += " — " + cmd["short"]
            lines.append(line)

        e = h.embed(title=category, color=h.BLUE)
        e.description = (
            chr(10).join(lines)
            + chr(10)
            + chr(10)
            + "Use `"
            + prefix
            + "help <name>` for details on any command."
        )
        e.set_footer(text=footer(i))
        pages.append(e)

    return pages


class HelpView(discord.ui.View):
    """
    Paginated help menu — sent as an ephemeral message so only the invoker
    can see or navigate it. Only the original invoker can interact.

    Close behaviour: strips the buttons and leaves the embed visible so the
    invoker can still read it; Discord's own ✕ dismisses the ephemeral.
    Buttons are automatically disabled after 120 s of inactivity.
    """

    def __init__(self, pages: list[discord.Embed], author: discord.Member):
        super().__init__(timeout=120)
        self.pages = pages
        self.author = author
        self.index = 0
        self.message: discord.Message | None = None
        self._update_buttons()

    def _update_buttons(self):
        """Grey out ⬅️ on first page, ➡️ on last page."""
        self.prev_btn.disabled = self.index == 0
        self.next_btn.disabled = self.index == len(self.pages) - 1

    async def _edit(self, interaction: discord.Interaction):
        self._update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    async def _strip_buttons(self):
        """Remove all buttons from the public message without deleting it."""
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.author:
            await interaction.response.send_message(
                "Only " + self.author.display_name + " can navigate this help menu.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self):
        """Strip buttons when the session expires — keep the embed visible."""
        self.stop()
        await self._strip_buttons()

    @discord.ui.button(
        emoji=chr(11013) + chr(65039), style=discord.ButtonStyle.secondary
    )
    async def prev_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.index -= 1
        await self._edit(interaction)

    @discord.ui.button(
        emoji=chr(10060), style=discord.ButtonStyle.secondary, label="Close"
    )
    async def close_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """Strip buttons and leave the embed — don't delete a public message."""
        self.stop()
        await interaction.response.edit_message(view=None)

    @discord.ui.button(
        emoji=chr(10145) + chr(65039), style=discord.ButtonStyle.secondary
    )
    async def next_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.index += 1
        await self._edit(interaction)


# ══════════════════════════════════════════════════════════════════════════════
class Utility(commands.Cog):
    """Bot configuration and info commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ══════════════════════════════════════════════════════════════════════
    #  help
    # ══════════════════════════════════════════════════════════════════════
    # ══════════════════════════════════════════════════════════════════════
    #  help
    # ══════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="help",
        description="Command reference. Use /help <command> for detail, or /help <category> to browse.",
    )
    @app_commands.describe(
        command="Command name for detail, or a category keyword (e.g. banning, tags, channel)"
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def help(self, ctx: commands.Context, command: Optional[str] = None):
        prefix = self.bot.prefixes.get(str(ctx.guild.id), self.bot.default_prefix)
        is_owner = await self.bot.is_owner(ctx.author)

        if command:
            key = command.lower().strip()

            # ── 1. Exact command / alias lookup ───────────────────────────────
            cmd = _FLAT.get(key)
            if cmd and cmd.get("perms") == "Bot Owner" and not is_owner:
                cmd = None  # hide owner commands from non-owners

            if cmd:
                e = h.embed(title=" " + prefix + cmd["usage"], color=h.BLUE)
                e.description = cmd["desc"] + "\n\u200b"
                if cmd["args"]:
                    e.add_field(
                        name="Arguments",
                        value="\n".join("`" + a + "` — " + d for a, d in cmd["args"]),
                        inline=False,
                    )
                e.add_field(name="Required Permission", value=cmd["perms"], inline=True)
                e.add_field(
                    name="Example", value="`" + cmd["example"] + "`", inline=False
                )
                if cmd.get("aliases"):
                    e.add_field(
                        name="Aliases",
                        value=", ".join("`" + a + "`" for a in cmd["aliases"]),
                        inline=False,
                    )
                e.set_footer(text="[ ] = optional  ·  < > = required  ·  NanoBot")
                return await ctx.reply(embed=e, ephemeral=True)

            # ── 2. Category keyword lookup ──────────────────────────────────
            cat_name = _CATEGORY_ALIASES.get(key)
            if (
                cat_name
                and (cat_name not in _OWNER_CATEGORIES or is_owner)
                and cat_name in _HELP
            ):
                return await ctx.reply(
                    embed=_build_category_embed(cat_name, _HELP[cat_name], prefix),
                    ephemeral=True,
                )

            # ── 3. Nothing found ────────────────────────────────────────────
            return await ctx.reply(
                embed=h.err(
                    "No command or category named `" + command + "`.\n"
                    "Use `" + prefix + "help` to browse all categories, or try:\n"
                    "`"
                    + prefix
                    + "help banning`  ·  `"
                    + prefix
                    + "help tags`  ·  `"
                    + prefix
                    + "help channel`"
                ),
                ephemeral=True,
            )

        # Paginated category overview
        pages = _build_help_pages(prefix, self.bot.user.display_name, is_owner=is_owner)
        view = HelpView(pages=pages, author=ctx.author)
        msg = await ctx.reply(embed=pages[0], view=view, ephemeral=True)
        view.message = msg

    # ══════════════════════════════════════════════════════════════════════════
    #  prefix
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="prefix",
        description="View or change NanoBot's prefix for this server.",
    )
    @app_commands.describe(new_prefix="New prefix (leave blank to view current)")
    async def prefix(self, ctx: commands.Context, new_prefix: Optional[str] = None):
        current = self.bot.prefixes.get(str(ctx.guild.id), self.bot.default_prefix)

        if new_prefix is None:
            return await ctx.reply(
                embed=h.info(
                    f"**Current prefix:** `{current}`\n"
                    f"Slash commands and @mentions always work regardless of prefix.",
                    "⚙️ Prefix",
                ),
                ephemeral=True,
            )

        if not ctx.author.guild_permissions.administrator:
            e = discord.Embed(
                description=(
                    f"**{ctx.author.display_name}**, you don't have the permissions needed "
                    f"to change the prefix.\nRequired: **Administrator**"
                ),
                color=0xED4245,
            )
            e.set_footer(text="NanoBot")
            return await ctx.reply(embed=e, ephemeral=True)

        if len(new_prefix) > 5:
            return await ctx.reply(
                embed=h.err("Prefix must be **5 characters or fewer**."), ephemeral=True
            )
        if " " in new_prefix:
            return await ctx.reply(
                embed=h.err("Prefix can't contain spaces."), ephemeral=True
            )

        await self.bot.save_prefix(ctx.guild.id, new_prefix)

        await ctx.reply(
            embed=h.ok(
                f"Prefix updated to `{new_prefix}`\n"
                f"Commands: `{new_prefix}ban`, `{new_prefix}kick`, `{new_prefix}help`, etc.\n"
                f"Slash commands always work regardless of prefix.",
                "⚙️ Prefix Updated",
            ),
            ephemeral=True,
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  support
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="support",
        aliases=["helpserver"],
        description="Get a link to the NanoBot support server.",
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def support(self, ctx: commands.Context):
        e = h.embed(title="💬 NanoBot Support", color=h.BLUE)
        e.description = (
            "Need help? Found a bug? Have a suggestion?\n\n"
            "[**Join the NanoBot Support Server**](https://discord.gg/M7fjxNg72s)\n\n"
            "You can also open an issue on "
            "[GitHub](https://github.com/therealjustsnow/NanoBot/issues)."
        )
        e.set_footer(text="NanoBot")
        await ctx.reply(embed=e, ephemeral=True)

    # ══════════════════════════════════════════════════════════════════════════
    #  ping
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(name="ping", description="Check NanoBot's latency.")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def ping(self, ctx: commands.Context):
        ms = round(self.bot.latency * 1000)
        status = "🟢 Great" if ms < 100 else ("🟡 Okay" if ms < 200 else "🔴 Slow")
        await ctx.reply(embed=h.ok(f"**{ms}ms** — {status}", "🏓 Pong!"))

    # ══════════════════════════════════════════════════════════════════════════
    #  info
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(name="info", description="NanoBot stats and runtime info.")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def info(self, ctx: commands.Context):
        prefix = self.bot.prefixes.get(str(ctx.guild.id), self.bot.default_prefix)
        latency = round(self.bot.latency * 1000)

        e = h.embed(title="⚡ NanoBot", color=h.BLUE)
        e.set_thumbnail(url=self.bot.user.display_avatar.url)
        e.description = "_Small. Fast. Built for Mobile Mods._\n\u200b"

        e.add_field(name="📡 Latency", value=f"{latency}ms", inline=True)
        e.add_field(name="🌐 Servers", value=str(len(self.bot.guilds)), inline=True)
        e.add_field(name="⚙️ Prefix", value=f"`{prefix}`", inline=True)
        e.add_field(
            name="📚 Library", value=f"discord.py {discord.__version__}", inline=True
        )
        e.add_field(name="🐍 Python", value=platform.python_version(), inline=True)
        e.add_field(name="🗄️ Storage", value="SQLite (aiosqlite)", inline=True)

        e.set_footer(text="NanoBot — Open Source · github.com/therealjustsnow/NanoBot")
        await ctx.reply(embed=e)

    # ══════════════════════════════════════════════════════════════════════════
    #  invite
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="invite",
        description="Get NanoBot's invite link with the correct permissions.",
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def invite(self, ctx: commands.Context):
        # Exact permissions NanoBot needs — nothing more, nothing less
        perms = discord.Permissions(
            # Moderation
            ban_members=True,
            kick_members=True,
            moderate_members=True,  # Timeout (freeze/unfreeze)
            manage_channels=True,  # Slowmode, lock, hide, nuke
            manage_messages=True,  # Purge, snailpurge, clean
            manage_roles=True,  # addrole / removerole
            view_audit_log=True,  # Audit log cog
            # Communication
            send_messages=True,
            send_messages_in_threads=True,
            embed_links=True,
            read_messages=True,
            read_message_history=True,
            attach_files=True,  # Tag image uploads
            add_reactions=True,
            # Voice
            move_members=True,  # moveall command
            connect=True,  # Required alongside move_members
        )

        url = discord.utils.oauth_url(
            self.bot.user.id,
            permissions=perms,
            scopes=("bot", "applications.commands"),  # needed for slash commands
        )

        e = h.embed(title="📨 Invite NanoBot", color=h.BLUE)
        e.description = (
            f"[**Click here to invite NanoBot**]({url})\n\n"
            f"The link requests exactly the permissions NanoBot needs — no bloat.\n\u200b"
        )

        perms_list = (
            "Ban Members · Kick Members · Timeout Members\n"
            "Manage Channels · Manage Messages · Manage Roles\n"
            "View Audit Log · Move Members · Connect\n"
            "Send Messages · Send Messages in Threads\n"
            "Embed Links · Read History · Attach Files · Add Reactions"
        )
        e.add_field(name="🔐 Requested Permissions", value=perms_list, inline=False)
        e.add_field(
            name="⚠️ Required Intents",
            value=(
                "After inviting, go to the **Discord Developer Portal** → Your App → Bot "
                "and enable:\n"
                "✅ **Server Members Intent**\n"
                "✅ **Message Content Intent**"
            ),
            inline=False,
        )
        e.set_footer(text="NanoBot — Small. Fast. Built for Mobile Mods.")
        await ctx.reply(embed=e, ephemeral=True)

    # ══════════════════════════════════════════════════════════════════════════
    #  about
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="about",
        description="What NanoBot is and why it exists.",
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def about(self, ctx: commands.Context):
        prefix = (
            self.bot.prefixes.get(str(ctx.guild.id), self.bot.default_prefix)
            if ctx.guild
            else self.bot.default_prefix
        )

        e = h.embed(title="⚡ About NanoBot", color=h.BLUE)
        e.set_thumbnail(url=self.bot.user.display_avatar.url)

        e.description = (
            "**Small. Fast. Built for Mobile Mods.**\n\n"
            "NanoBot exists for one reason: most mod bots assume you're sitting at a desktop. "
            "If you've ever tried to ban someone while scrolling on your phone, you know the problem — "
            "copying IDs is awkward, finding the right command takes too many taps, "
            "and half the time the bot's UI is just not built for a 6-inch screen.\n\n"
            "NanoBot fixes that.\n\u200b"
        )

        e.add_field(
            name="🔥 What makes it different",
            value=(
                "**Last-sender targeting** — most mod commands work with no user specified; "
                "the bot targets whoever last spoke in the channel.\n"
                f"**Tag shortcuts** — `{prefix}tagname` fires any tag with one tap.\n"
                "**Clean embeds** — every response is designed to be readable on a small screen.\n"
                "**SQLite storage** — single portable file, zero cloud dependency, easy to back up.\n"
                "**No bloat** — commands exist because mobile mods actually need them."
            ),
            inline=False,
        )

        e.add_field(
            name="🧬 Philosophy",
            value=(
                "NanoBot is intentionally small. It doesn't try to replace every mod bot — "
                "it tries to make the things you do every day faster and less annoying.\n"
                "Not enterprise. Not overengineered. Just useful."
            ),
            inline=False,
        )

        e.add_field(
            name="📦 Tech",
            value=(
                f"Built with Python {platform.python_version()} + discord.py {discord.__version__}\n"
                "Storage: SQLite (aiosqlite) — single portable file, no server needed.\n"
                "Self-host friendly — if you can run Python, you can run NanoBot."
            ),
            inline=False,
        )

        e.add_field(
            name="🔗 Links",
            value=(
                "[GitHub](https://github.com/therealjustsnow/NanoBot) — Source code\n"
                "Open source · MIT License"
            ),
            inline=False,
        )

        e.set_footer(
            text="NanoBot — Built by someone who actually moderates on mobile."
        )
        await ctx.reply(embed=e, ephemeral=True)

    # ══════════════════════════════════════════════════════════════════════════

    # ══════════════════════════════════════════════════════════════════════════
    #  server
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="server",
        aliases=["serverinfo", "si", "guild"],
        description="Info card for this server.",
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def server(self, ctx: commands.Context):
        g = ctx.guild
        now = discord.utils.utcnow()

        total = g.member_count or 0
        bots = sum(1 for m in g.members if m.bot)
        humans = total - bots
        online = sum(
            1 for m in g.members if m.status != discord.Status.offline and not m.bot
        )

        text_ch = len(g.text_channels)
        voice_ch = len(g.voice_channels)
        cats = len(g.categories)
        threads = len(g.threads)

        color = g.me.color if g.me.color != discord.Color.default() else h.BLUE
        e = h.embed(title="🏰 " + g.name, color=color)

        if g.icon:
            e.set_thumbnail(url=g.icon.url)
        if g.banner:
            e.set_image(url=g.banner.with_size(1024).url)

        e.description = g.description or ""

        e.add_field(
            name="👑 Owner",
            value=g.owner.mention if g.owner else str(g.owner_id),
            inline=True,
        )
        e.add_field(name="🆔 ID", value="`" + str(g.id) + "`", inline=True)
        e.add_field(
            name="📅 Created",
            value=discord.utils.format_dt(g.created_at, style="R"),
            inline=True,
        )

        members_val = (
            "**"
            + str(total)
            + "** total\n"
            + "🟢 "
            + str(online)
            + " online · "
            + "👤 "
            + str(humans)
            + " humans · "
            + "🤖 "
            + str(bots)
            + " bots"
        )
        e.add_field(name="👥 Members", value=members_val, inline=True)

        channels_val = (
            "📝 "
            + str(text_ch)
            + " text · 🔊 "
            + str(voice_ch)
            + " voice\n"
            + "📁 "
            + str(cats)
            + " categories · 🧵 "
            + str(threads)
            + " threads"
        )
        e.add_field(name="💬 Channels", value=channels_val, inline=True)
        e.add_field(name="🎭 Roles", value=str(len(g.roles) - 1), inline=True)

        boosts = g.premium_subscription_count
        tier = g.premium_tier
        bar = "🟣" * boosts + "⬛" * max(0, 14 - boosts)
        e.add_field(
            name="💎 Boost — Level " + str(tier),
            value=bar + "\n" + str(boosts) + " boosts",
            inline=False,
        )

        feature_map = {
            "VERIFIED": "✅ Verified",
            "PARTNERED": "🤝 Partner",
            "COMMUNITY": "🏘️ Community",
            "DISCOVERABLE": "🔍 Discoverable",
            "NEWS": "📰 News Channels",
            "MEMBER_VERIFICATION_GATE_ENABLED": "🚪 Membership Screening",
        }
        if g.vanity_url_code:
            feature_map["VANITY_URL"] = "🔗 discord.gg/" + g.vanity_url_code
        features = [v for k, v in feature_map.items() if k in g.features]
        if features:
            e.add_field(name="🏅 Features", value=" · ".join(features), inline=False)

        e.set_footer(text="NanoBot · " + str(g.member_count) + " members")
        e.timestamp = now
        await ctx.reply(embed=e)

    # ══════════════════════════════════════════════════════════════════════════
    #  user
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="user",
        aliases=["userinfo", "ui", "member"],
        description="Public info card for a user.",
    )
    @app_commands.describe(user="User to look up (leave blank for yourself)")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def user(self, ctx: commands.Context, user: Optional[discord.Member] = None):
        target = user or ctx.author
        now = discord.utils.utcnow()

        created = discord.utils.format_dt(target.created_at, style="R")
        joined = (
            discord.utils.format_dt(target.joined_at, style="R")
            if target.joined_at
            else "Unknown"
        )

        roles = [r for r in reversed(target.roles) if r != ctx.guild.default_role]
        roles_str = " ".join(r.mention for r in roles[:8])
        if len(roles) > 8:
            roles_str += " _+" + str(len(roles) - 8) + " more_"
        if not roles_str:
            roles_str = "_None_"

        color = (
            target.color.value if target.color != discord.Color.default() else h.GREY
        )
        e = discord.Embed(title="👤 " + target.display_name, color=color)
        e.set_thumbnail(url=target.display_avatar.url)

        e.add_field(name="🏷️ Username", value="`" + str(target) + "`", inline=True)
        e.add_field(name="🆔 ID", value="`" + str(target.id) + "`", inline=True)
        e.add_field(name="🤖 Bot", value="Yes" if target.bot else "No", inline=True)
        e.add_field(name="📅 Joined Server", value=joined, inline=True)
        e.add_field(name="📅 Account Age", value=created, inline=True)

        status_icons = {
            discord.Status.online: "🟢 Online",
            discord.Status.idle: "🟡 Idle",
            discord.Status.dnd: "🔴 Do Not Disturb",
            discord.Status.offline: "⚫ Offline",
        }
        # Bots never receive their own PRESENCE_UPDATE events, so guild.me.status
        # is always "offline" in the cache.  Read from the bot object directly when
        # the target is the bot itself so the field shows the real presence.
        if target.id == self.bot.user.id:
            raw_status = self.bot.status
            raw_activity = self.bot.activity
        else:
            raw_status = target.status
            raw_activity = target.activity

        status_str = status_icons.get(raw_status, "⚫ Offline")
        if raw_activity:
            act = raw_activity
            if isinstance(act, discord.Streaming):
                status_str += "\n🟣 Streaming **" + act.name + "**"
            elif isinstance(act, discord.Game):
                status_str += "\n🎮 Playing **" + act.name + "**"
            elif isinstance(act, discord.Spotify):
                status_str += "\n🎵 **" + act.title + "** by " + act.artist
            elif (
                isinstance(act, discord.Activity)
                and act.type == discord.ActivityType.watching
            ):
                status_str += "\n👁️ Watching **" + act.name + "**"
            elif act.name:
                status_str += "\n▶️ " + act.name
        e.add_field(name="📡 Status", value=status_str, inline=True)

        if target.timed_out_until and target.timed_out_until > now:
            e.add_field(
                name="🧊 Timed Out",
                value="Until "
                + discord.utils.format_dt(target.timed_out_until, style="R"),
                inline=True,
            )
        if target.premium_since:
            e.add_field(
                name="💎 Boosting Since",
                value=discord.utils.format_dt(target.premium_since, style="R"),
                inline=True,
            )

        e.add_field(
            name="🎭 Roles (" + str(len(roles)) + ")", value=roles_str, inline=False
        )

        flags = target.public_flags
        badges = []
        if flags.staff:
            badges.append("🛡️ Discord Staff")
        if flags.partner:
            badges.append("🤝 Partner")
        if flags.hypesquad:
            badges.append("🏠 HypeSquad")
        if flags.bug_hunter:
            badges.append("🐛 Bug Hunter")
        if flags.early_supporter:
            badges.append("🏷️ Early Supporter")
        if flags.verified_bot_developer:
            badges.append("🔧 Bot Dev")
        if flags.active_developer:
            badges.append("💻 Active Dev")
        if badges:
            e.add_field(name="🏅 Badges", value=" · ".join(badges), inline=False)

        # Note count — only shown to mods (manage_messages) and only if notes exist
        if ctx.author.guild_permissions.manage_messages:
            from utils import db as _db

            _note_count = await _db.get_note_count(ctx.guild.id, target.id)
            if _note_count:
                e.add_field(
                    name="📜 Mod Notes",
                    value=str(_note_count)
                    + " note(s) on file. Use `/notes @user` to view.",
                    inline=False,
                )

        e.set_footer(text="NanoBot · " + target.name)
        e.timestamp = now
        await ctx.reply(embed=e)

    # ══════════════════════════════════════════════════════════════════════════
    #  avatar
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="avatar",
        aliases=["av", "pfp", "icon"],
        description="Show a user's avatar in full size.",
    )
    @app_commands.describe(user="User whose avatar to show (leave blank for yourself)")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def avatar(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ):
        target = user or ctx.author
        av = target.display_avatar.with_size(1024)

        color = target.color if target.color != discord.Color.default() else h.BLUE
        e = discord.Embed(title="🖼️ " + target.display_name + "'s Avatar", color=color)
        e.set_image(url=av.url)

        formats = []
        for fmt in ("png", "jpg", "webp"):
            try:
                url = target.display_avatar.with_format(fmt).with_size(1024).url  # type: ignore
                formats.append("[" + fmt.upper() + "](" + url + ")")
            except (ValueError, discord.InvalidArgument):
                pass
        if target.display_avatar.is_animated():
            try:
                formats.append(
                    "[GIF]("
                    + target.display_avatar.with_format("gif").with_size(1024).url
                    + ")"
                )
            except (ValueError, discord.InvalidArgument):
                pass

        e.description = " · ".join(formats) if formats else ""

        if target.guild_avatar:
            e.set_footer(text="Showing server avatar  ·  NanoBot")
        else:
            e.set_footer(text="NanoBot")

        await ctx.reply(embed=e)

    # ══════════════════════════════════════════════════════════════════════════
    #  banner
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="banner",
        aliases=["userbanner"],
        description="Show a user's profile banner.",
    )
    @app_commands.describe(user="User whose banner to show (leave blank for yourself)")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def banner(
        self, ctx: commands.Context, user: Optional[discord.Member] = None
    ):
        target = user or ctx.author

        try:
            fetched = await self.bot.fetch_user(target.id)
        except discord.HTTPException:
            fetched = None

        banner = fetched.banner if fetched else None
        if not banner:
            return await ctx.reply(
                embed=h.info(
                    "**"
                    + target.display_name
                    + "** doesn't have a profile banner set.",
                    "🖼️ No Banner",
                ),
                ephemeral=True,
            )

        color = target.color if target.color != discord.Color.default() else h.BLUE
        e = discord.Embed(title="🖼️ " + target.display_name + "'s Banner", color=color)
        e.set_image(url=banner.with_size(1024).url)

        formats = []
        for fmt in ("png", "jpg", "webp"):
            try:
                formats.append("[" + fmt.upper() + "](" + banner.with_format(fmt).with_size(1024).url + ")")  # type: ignore
            except (ValueError, discord.InvalidArgument):
                pass
        if banner.is_animated():
            try:
                formats.append(
                    "[GIF](" + banner.with_format("gif").with_size(1024).url + ")"
                )
            except (ValueError, discord.InvalidArgument):
                pass

        e.description = " · ".join(formats) if formats else ""
        e.set_footer(text="NanoBot")
        await ctx.reply(embed=e, ephemeral=True)

    # ══════════════════════════════════════════════════════════════════════════
    #  roleinfo
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="roleinfo",
        aliases=["role", "ri"],
        description="Info card for a server role.",
    )
    @app_commands.describe(role="The role to inspect")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def roleinfo(self, ctx: commands.Context, *, role: discord.Role):
        now = discord.utils.utcnow()

        key_perms = {
            "administrator": "⛔ Administrator",
            "ban_members": "🔨 Ban Members",
            "kick_members": "👢 Kick Members",
            "manage_guild": "⚙️ Manage Server",
            "manage_channels": "📢 Manage Channels",
            "manage_messages": "🗑️ Manage Messages",
            "manage_roles": "🎭 Manage Roles",
            "moderate_members": "🧊 Timeout Members",
            "mention_everyone": "📣 Mention Everyone",
            "manage_webhooks": "🔗 Manage Webhooks",
        }
        active_perms = [
            label
            for perm, label in key_perms.items()
            if getattr(role.permissions, perm, False)
        ]

        color = role.color if role.color != discord.Color.default() else h.GREY
        e = discord.Embed(title="🎭 " + role.name, color=color)

        e.add_field(name="🆔 Role ID", value="`" + str(role.id) + "`", inline=True)
        e.add_field(name="👥 Members", value=str(len(role.members)), inline=True)
        e.add_field(
            name="📅 Created",
            value=discord.utils.format_dt(role.created_at, style="R"),
            inline=True,
        )
        e.add_field(name="🎨 Color", value=str(role.color), inline=True)
        e.add_field(
            name="📌 Position",
            value=str(role.position) + " / " + str(len(ctx.guild.roles)),
            inline=True,
        )
        e.add_field(
            name="💬 Mentionable",
            value="Yes" if role.mentionable else "No",
            inline=True,
        )
        e.add_field(name="📋 Hoisted", value="Yes" if role.hoist else "No", inline=True)
        e.add_field(
            name="🤖 Managed",
            value="Yes (bot/integration)" if role.managed else "No",
            inline=True,
        )

        e.add_field(
            name="🔐 Key Permissions",
            value="\n".join(active_perms) if active_perms else "_None of note_",
            inline=False,
        )

        e.set_footer(text="NanoBot · " + role.name)
        e.timestamp = now
        await ctx.reply(embed=e)

    # ══════════════════════════════════════════════════════════════════════════
    #  uptime
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="uptime",
        description="How long NanoBot has been running since last (re)start.",
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def uptime(self, ctx: commands.Context):
        now = discord.utils.utcnow()
        delta = now - self.bot.start_time
        seconds = int(delta.total_seconds())

        days, rem = divmod(seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, secs = divmod(rem, 60)

        def unit(n, word):
            return str(n) + " " + word + ("s" if n != 1 else "")

        parts = []
        if days:
            parts.append(unit(days, "day"))
        if hours:
            parts.append(unit(hours, "hour"))
        if minutes:
            parts.append(unit(minutes, "minute"))
        if secs or not parts:
            parts.append(unit(secs, "second"))

        # Join: "1 day, 2 hours, 3 minutes and 4 seconds"
        if len(parts) > 1:
            uptime_str = ", ".join("**" + p + "**" for p in parts[:-1])
            uptime_str += " and **" + parts[-1] + "**"
        else:
            uptime_str = "**" + parts[0] + "**"

        e = h.embed(title="⏱️ Uptime", color=h.BLUE)
        e.description = (
            "NanoBot has been running for " + uptime_str + ".\n"
            "Online since "
            + discord.utils.format_dt(self.bot.start_time, style="F")
            + " ("
            + discord.utils.format_dt(self.bot.start_time, style="R")
            + ")"
        )
        e.set_footer(text="NanoBot")
        await ctx.reply(embed=e)


# ── Registration ───────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(Utility(bot))
