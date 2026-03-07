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
            "short": "Ban + purge history + optional timed unban",
            "desc": (
                "The mobile mod's best friend. Bans a user, deletes their message history, "
                "optionally sends them a DM, and can automatically unban them after a set time — "
                "all in one command. Targeting defaults to the last person who sent a message if no user is given."
            ),
            "args": [
                ("user",    "Who to ban (blank = last message sender in this channel)"),
                ("days",    "Days of message history to delete (1–7, default 7)"),
                ("wait",    "Auto-unban after this long: `30m`, `1h`, `7d` etc. (omit for permanent ban)"),
                ("message", "DM to send the user (omit for a sensible default)"),
            ],
            "perms":   "Ban Members",
            "example": "!cban @user 7 24h See you tomorrow.",
        },
        {
            "name": "ban",
            "aliases": [],
            "usage": "ban [user] [message]",
            "short": "Permanently ban a user with optional DM",
            "desc": (
                "Permanent ban. No auto-unban. Default DM does NOT include a rejoin invite. "
                "Targets the last sender if no user is specified."
            ),
            "args": [
                ("user",    "Who to ban (blank = last sender)"),
                ("message", "DM to send (omit for default)"),
            ],
            "perms":   "Ban Members",
            "example": "!ban @user You've been permanently banned.",
        },
        {
            "name": "unban",
            "aliases": [],
            "usage": "unban <user_id> [reason]",
            "short": "Unban a user by their ID",
            "desc": (
                "Unbans by User ID — the only way to unban since the user has already left. "
                "Enable Developer Mode in Discord settings, then right-click any user to copy their ID."
            ),
            "args": [
                ("user_id", "The user's Discord ID (numbers only)"),
                ("reason",  "Optional reason (shown in audit log)"),
            ],
            "perms":   "Ban Members",
            "example": "!unban 123456789012345678",
        },
    ],
    "👢 Kicking & Timeouts": [
        {
            "name": "kick",
            "aliases": [],
            "usage": "kick [user] [message]",
            "short": "Kick a user, they can rejoin",
            "desc": "Kicks a user with an optional DM. They can rejoin immediately. Targets last sender if no user is specified.",
            "args": [
                ("user",    "Who to kick (blank = last sender)"),
                ("message", "DM to send (omit for default)"),
            ],
            "perms":   "Kick Members",
            "example": "!kick @user Please review the rules before rejoining.",
        },
        {
            "name": "freeze",
            "aliases": [],
            "usage": "freeze [user] [duration] [reason]",
            "short": "Timeout a user (default 10m)",
            "desc": (
                "Applies a Discord Timeout to a user. They cannot speak, react, use threads, "
                "or join voice channels for the duration. Maximum: 28 days."
            ),
            "args": [
                ("user",     "Who to freeze (blank = last sender)"),
                ("duration", "How long: `5m`, `1h`, `1d` etc. (default 10m, max 28 days)"),
                ("reason",   "Optional reason"),
            ],
            "perms":   "Moderate Members",
            "example": "!freeze @user 30m Please cool down.",
        },
        {
            "name": "unfreeze",
            "aliases": [],
            "usage": "unfreeze <user>",
            "short": "Remove a timeout early",
            "desc": "Removes an active Discord Timeout from a user before it expires.",
            "args": [("user", "User to unfreeze (required)")],
            "perms":   "Moderate Members",
            "example": "!unfreeze @user",
        },
    ],
    "📢 Channel Controls": [
        {
            "name": "slow",
            "aliases": [],
            "usage": "slow [delay] [length]",
            "short": "Toggle or set slowmode with optional auto-disable",
            "desc": (
                "No args: toggles slowmode off if on, or on at 60s if off. "
                "With a delay: sets that slowmode. "
                "With a length: automatically removes slowmode after that time (survives restarts)."
            ),
            "args": [
                ("delay",  "Slowmode delay: `30s`, `2m`, `5m` (max 5 min). Omit to toggle."),
                ("length", "Auto-disable after: `10m`, `1h`, `3d` (max 7 days). Omit for indefinite."),
            ],
            "perms":   "Manage Channels",
            "example": "!slow 2m 1h",
        },
        {
            "name": "lock",
            "aliases": [],
            "usage": "lock [channel] [reason]",
            "short": "Toggle @everyone channel lock",
            "desc": (
                "Prevents @everyone from sending messages in a channel. "
                "Run again to unlock. Works on the current channel by default."
            ),
            "args": [
                ("channel", "Channel to lock (default: current channel)"),
                ("reason",  "Optional reason shown in audit log"),
            ],
            "perms":   "Manage Channels",
            "example": "!lock #general Temporary lock during raid.",
        },
        {
            "name": "purge",
            "aliases": [],
            "usage": "purge <amount> [user]",
            "short": "Delete the last X messages (1–100)",
            "desc": "Bulk-deletes recent messages. Optionally filter to only delete messages from a specific user.",
            "args": [
                ("amount", "Number of messages to delete (1–100, required)"),
                ("user",   "Only delete messages from this user (optional)"),
            ],
            "perms":   "Manage Messages",
            "example": "!purge 25 @spammer",
        },
    ],
    "🔎 Info & Notes": [
        {
            "name": "last",
            "aliases": [],
            "usage": "last",
            "short": "Show who last sent a message here",
            "desc": (
                "Displays who last sent a message in this channel. "
                "This is the user that commands like `!kick`, `!ban`, `!freeze` will target "
                "if you don't specify a user — useful to check before acting."
            ),
            "args": [],
            "perms":   "None",
            "example": "!last",
        },
        {
            "name": "note",
            "aliases": [],
            "usage": "note <user> <content>",
            "short": "Add a private mod note (invisible to the user)",
            "desc": "Saves an internal note about a user, stored in JSON. The user never sees these. Useful for tracking behaviour over time.",
            "args": [
                ("user",    "User to attach the note to"),
                ("content", "Note content (max 1000 chars)"),
            ],
            "perms":   "Manage Messages",
            "example": "!note @user Warned about spam in #general.",
        },
        {
            "name": "notes",
            "aliases": [],
            "usage": "notes <user>",
            "short": "View mod notes for a user",
            "desc": "Shows up to 8 of the most recent mod notes for a user. Only visible to you (ephemeral).",
            "args": [("user", "User to look up")],
            "perms":   "Manage Messages",
            "example": "!notes @user",
        },
        {
            "name": "clearnotes",
            "aliases": [],
            "usage": "clearnotes <user>",
            "short": "Delete all notes for a user (admin only)",
            "desc": "Permanently wipes all mod notes for a user from the JSON store.",
            "args": [("user", "User whose notes to clear")],
            "perms":   "Administrator",
            "example": "!clearnotes @user",
        },
    ],
    "🏷️ Tags": [
        {
            "name": "tag",
            "aliases": [],
            "usage": "tag [shorthand or subcommand]",
            "short": "Post text snippets in channel, or DM to a user — personal or server-wide",
            "desc": (
                "Tags let you save text (and images) and quickly DM them to yourself or others.\n\n"
                "**Shorthand (fastest on mobile):**\n"
                "`!tag <n>` — post tag *n* in the channel\n"
                "`!<n>` — same, even shorter (shortcut)\n"
                "`!tag + <n> | <content>` — create (| separates name from content)\n"
                "`!tag add <n> | <content>` — same\n"
                "`!tag - <n>` — delete tag\n"
                "`!tag remove <n>` — same\n"
                "`!tag g+ <n> <content>` — create global tag (mods)\n\n"
                "**Subcommands:**\n"
                "`/tag create`, `/tag global`, `/tag use`, `/tag preview`,\n"
                "`/tag image`, `/tag edit`, `/tag delete`, `/tag list`"
            ),
            "args": [],
            "perms":   "None (global creation requires Manage Messages)",
            "example": "!tag + rules Read #rules before posting!\n!rules",
        },
    ],
    "🔧 Owner / Admin": [
        {
            "name": "reload",
            "aliases": ["rl"],
            "usage": "reload [cog|all]",
            "short": "Hot-reload a cog or all cogs (owner only)",
            "desc": (
                "Reloads a cog without restarting the bot. Useful after editing a cog file. "
                "Accepts `all` to reload every cog at once, the full dotted name "
                "(`cogs.moderation`), or just the short name (`moderation`)."
            ),
            "args": [("cog", "Cog to reload, or `all` (default: all)")],
            "perms":   "Bot Owner",
            "example": "!reload all\n!reload moderation",
        },
        {
            "name": "setloglevel",
            "aliases": ["loglevel", "loglvl"],
            "usage": "setloglevel <level>",
            "short": "Change log verbosity live (owner only)",
            "desc": (
                "Changes the logging level immediately and saves it to config.json so it persists across restarts. "
                "DEBUG is useful for tracing issues; WARNING keeps things quiet in production."
            ),
            "args": [("level", "DEBUG / INFO / WARNING / ERROR / CRITICAL")],
            "perms":   "Bot Owner",
            "example": "!setloglevel DEBUG",
        },
        {
            "name": "logs",
            "aliases": ["log"],
            "usage": "logs [lines]",
            "short": "Tail the log file in Discord (owner only)",
            "desc": (
                "Fetches the last N lines of `logs/nanobot.log` and shows them in an ephemeral embed. "
                "Handy for diagnosing issues from your phone without needing SSH access."
            ),
            "args": [("lines", "How many lines to show (1–50, default 20)")],
            "perms":   "Bot Owner",
            "example": "!logs 30",
        },
        {
            "name": "restart",
            "aliases": ["reboot", "rs"],
            "usage": "restart",
            "short": "Gracefully restart the bot process (owner only)",
            "desc": (
                "Closes the Discord connection cleanly, then re-executes the Python process with the same arguments. "
                "Config changes, cog edits, and data updates all take effect. "
                "Works with both `python main.py` and `python run.py`."
            ),
            "args": [],
            "perms":   "Bot Owner",
            "example": "!restart",
        },
        {
            "name": "shutdown",
            "aliases": ["die", "stop"],
            "usage": "shutdown",
            "short": "Gracefully shut the bot down (owner only)",
            "desc": "Flushes all logs, sends a goodbye message, and closes the Discord connection cleanly.",
            "args": [],
            "perms":   "Bot Owner",
            "example": "!shutdown",
        },
    ],
    "🔍 Server & User Info": [
        {
            "name": "server",
            "aliases": ["serverinfo", "si", "guild"],
            "usage": "server",
            "short": "Full server info card",
            "desc": "Displays a detailed embed with member counts, boost level, channel breakdown, features, and more.",
            "args": [],
            "perms":   "None",
            "example": "!server",
        },
        {
            "name": "user",
            "aliases": ["userinfo", "ui", "member"],
            "usage": "user [user]",
            "short": "Public user info card — status, roles, badges",
            "desc": "Shows a clean user card with status, activity, join date, account age, roles and badges. Designed to be readable on mobile. Anyone can use this.",
            "args": [("user", "User to look up (blank = yourself)")],
            "perms":   "None",
            "example": "!user @someone",
        },
        {
            "name": "avatar",
            "aliases": ["av", "pfp", "icon"],
            "usage": "avatar [user]",
            "short": "Show a user's avatar full-size",
            "desc": "Fetches and displays a user's avatar in full 1024px resolution with download links for PNG, JPG, WEBP, and GIF (if animated). Shows server avatar if they have one set.",
            "args": [("user", "Whose avatar to show (blank = yourself)")],
            "perms":   "None",
            "example": "!avatar @someone",
        },
        {
            "name": "banner",
            "aliases": ["userbanner"],
            "usage": "banner [user]",
            "short": "Show a user's profile banner",
            "desc": "Fetches and displays a user's profile banner (requires a fresh API call — not cached locally).",
            "args": [("user", "Whose banner to show (blank = yourself)")],
            "perms":   "None",
            "example": "!banner @someone",
        },
        {
            "name": "roleinfo",
            "aliases": ["role", "ri"],
            "usage": "roleinfo <role>",
            "short": "Details about a server role",
            "desc": "Shows role color, position, member count, creation date, whether it's hoisted/mentionable, and any notable permissions it holds.",
            "args": [("role", "The role to inspect (mention it or type its name)")],
            "perms":   "None",
            "example": "!roleinfo @Moderator",
        },
        {
            "name": "uptime",
            "aliases": [],
            "usage": "uptime",
            "short": "How long the bot has been running",
            "desc": "Shows how long NanoBot has been online since its last start or restart.",
            "args": [],
            "perms":   "None",
            "example": "!uptime",
        },
    ],
    "🔍 Server & User Info": [
        {
            "name": "server", "aliases": ["serverinfo", "si", "guild"],
            "usage": "server", "short": "Full server info card",
            "desc": "Member counts, boost level, channel breakdown, features, creation date and more.",
            "args": [], "perms": "None", "example": "!server",
        },
        {
            "name": "user", "aliases": ["userinfo", "ui", "member"],
            "usage": "user [user]", "short": "Public user info — status, roles, badges",
            "desc": "Shows a clean user card with status, activity, join date, account age, roles and badges. Anyone can use this.",
            "args": [("user", "User to look up (blank = yourself)")],
            "perms": "None", "example": "!user @someone",
        },
        {
            "name": "avatar", "aliases": ["av", "pfp", "icon"],
            "usage": "avatar [user]", "short": "Show a user's avatar full-size",
            "desc": "Fetches the avatar at 1024px with PNG/JPG/WEBP/GIF download links. Shows server avatar if set.",
            "args": [("user", "Whose avatar to show (blank = yourself)")],
            "perms": "None", "example": "!avatar @someone",
        },
        {
            "name": "banner", "aliases": ["userbanner"],
            "usage": "banner [user]", "short": "Show a user's profile banner",
            "desc": "Fetches and displays the user's profile banner with download links.",
            "args": [("user", "Whose banner to show (blank = yourself)")],
            "perms": "None", "example": "!banner @someone",
        },
        {
            "name": "roleinfo", "aliases": ["role", "ri"],
            "usage": "roleinfo <role>", "short": "Details about a server role",
            "desc": "Color, position, member count, creation date, hoist/mentionable status, and notable permissions.",
            "args": [("role", "Mention it or type the name")],
            "perms": "None", "example": "!roleinfo @Moderator",
        },
        {
            "name": "uptime", "aliases": [],
            "usage": "uptime", "short": "How long the bot has been running",
            "desc": "Shows how long NanoBot has been online since its last start or restart.",
            "args": [], "perms": "None", "example": "!uptime",
        },
    ],
    "⏰ Reminders": [
        {
            "name": "remindme", "aliases": ["rm"],
            "usage": "remindme <message with duration>",
            "short": "Set a reminder for yourself",
            "desc": "Remind yourself about something after a set time. Include the duration in your message (e.g. 'call mum 30m') or pass it separately. Delivered by DM, falls back to channel ping if DMs are closed.",
            "args": [
                ("message", "What to remind you about — put the duration at the end"),
                ("time",    "Duration if not in message: 8h, 30m, 2 hours, 1 day"),
                ("dm",      "DM the reminder (default: yes)"),
            ],
            "perms": "None", "example": "!remindme stand up in 1 hour",
        },
        {
            "name": "remind", "aliases": [],
            "usage": "remind <@user> <message with duration>",
            "short": "Set a reminder for another user",
            "desc": "Remind someone else about something. By default posts a channel ping; use dm=yes to DM them instead.",
            "args": [
                ("user",    "Who to remind"),
                ("message", "What to remind them about — duration at the end"),
                ("time",    "Duration if not in message"),
                ("dm",      "DM them instead of pinging in channel (default: no)"),
            ],
            "perms": "None", "example": "!remind @user check that PR 2h",
        },
        {
            "name": "reminders", "aliases": ["reminder"],
            "usage": "reminders [cancel <id>]",
            "short": "List or cancel your active reminders",
            "desc": "With no args: lists all your active reminders sorted by due time. With 'cancel <id>': cancels the reminder with that ID (shown when the reminder was set).",
            "args": [("id", "6-character reminder ID to cancel")],
            "perms": "None", "example": "!reminders cancel abc123",
        },
    ],
    "⚙️ Config & Info": [
        {
            "name": "prefix",
            "aliases": [],
            "usage": "prefix [new_prefix]",
            "short": "View or change the bot prefix",
            "desc": "Shows the current prefix with no args. With a new prefix (max 5 chars), updates it for this server. Admin only to change.",
            "args": [("new_prefix", "New prefix (1–5 chars, no spaces). Omit to just view.")],
            "perms":   "Administrator (to change)",
            "example": "!prefix ?",
        },
        {
            "name": "ping",
            "aliases": [],
            "usage": "ping",
            "short": "Check NanoBot's response time",
            "desc": "Returns the current WebSocket latency between NanoBot and Discord's servers.",
            "args": [],
            "perms":   "None",
            "example": "!ping",
        },
        {
            "name": "info",
            "aliases": [],
            "usage": "info",
            "short": "Bot stats and runtime info",
            "desc": "Shows latency, server count, current prefix, discord.py version, Python version, and storage type.",
            "args": [],
            "perms":   "None",
            "example": "!info",
        },
        {
            "name": "invite",
            "aliases": [],
            "usage": "invite",
            "short": "Get the bot invite link",
            "desc": "Generates an invite link with exactly the permissions NanoBot needs to function. No unnecessary extras.",
            "args": [],
            "perms":   "None",
            "example": "!invite",
        },
        {
            "name": "support",
            "aliases": ["helpserver"],
            "usage": "support",
            "short": "Link to the NanoBot support server",
            "desc": "Posts an invite link to the official NanoBot support server where you can get help, report bugs, or suggest features.",
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
            "perms":   "None",
            "example": "!about",
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


# ── Help pagination ───────────────────────────────────────────────────────────

_OWNER_CATEGORIES = {"🔧 Owner / Admin"}


def _build_help_pages(prefix: str, bot_name: str, *, is_owner: bool = False) -> list[discord.Embed]:
    """
    Build one embed per help category, plus a cover page.
    Owner-only categories are hidden from non-owners.
    Returns a list of discord.Embed objects ready to display.
    """
    categories = [
        (cat, cmds) for cat, cmds in _HELP.items()
        if is_owner or cat not in _OWNER_CATEGORIES
    ]
    total = len(categories) + 1  # +1 for cover

    def footer(page_num):
        return "Page " + str(page_num) + " / " + str(total) + "  ·  NanoBot"

    pages = []

    # Cover page
    cover = h.embed(
        title       = chr(9889) + " NanoBot " + chr(8212) + " Command Reference",
        description = (
            "Prefix: `" + prefix + "` · Slash `/` · @" + bot_name + chr(10)
            + "Most mod commands default to the **last message sender** if no user is given." + chr(10)
            + chr(10)
            + "Use `" + prefix + "help <command>` for a full breakdown of any command." + chr(10)
            + chr(10)
            + chr(10).join(
                "**" + cat + "** " + chr(8212) + " " + str(len(cmds)) + " command" + ("s" if len(cmds) != 1 else "")
                for cat, cmds in categories
            )
        ),
        color = h.BLUE,
    )
    cover.set_footer(text=footer(1))
    pages.append(cover)

    # One page per category
    for i, (category, cmds) in enumerate(categories, start=2):
        lines = []
        for cmd in cmds:
            line = "`" + prefix + cmd["name"] + "`"
            if cmd.get("aliases"):
                line += " _(also: " + ", ".join("`" + a + "`" for a in cmd["aliases"]) + ")_"
            line += " — " + cmd["short"]
            lines.append(line)

        e = h.embed(title=category, color=h.BLUE)
        e.description = chr(10).join(lines) + chr(10) + chr(10) + "Use `" + prefix + "help <name>` for details on any command."
        e.set_footer(text=footer(i))
        pages.append(e)

    return pages


class HelpView(discord.ui.View):
    """
    Reaction-style navigation buttons for the paginated help command.
    Only the original invoker can interact.
    Buttons are disabled automatically after 120 seconds of inactivity.
    """

    def __init__(self, pages: list[discord.Embed], author: discord.Member):
        super().__init__(timeout=120)
        self.pages   = pages
        self.author  = author
        self.index   = 0
        self.message: discord.Message | None = None
        self._update_buttons()

    def _update_buttons(self):
        """Grey out ⬅️ on first page, ➡️ on last page."""
        self.prev_btn.disabled = (self.index == 0)
        self.next_btn.disabled = (self.index == len(self.pages) - 1)

    async def _edit(self, interaction: discord.Interaction):
        self._update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.author:
            await interaction.response.send_message(
                "Only " + self.author.display_name + " can navigate this help menu.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self):
        """Disable all buttons when the session expires."""
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(emoji=chr(11013) + chr(65039), style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index -= 1
        await self._edit(interaction)

    @discord.ui.button(emoji=chr(10060), style=discord.ButtonStyle.secondary)
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        if self.message:
            try:
                await self.message.delete()
            except discord.HTTPException:
                pass

    @discord.ui.button(emoji=chr(10145) + chr(65039), style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
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
    @commands.hybrid_command(
        name="help",
        description="Paginated command reference. Use !help <command> for detail on one command.",
    )
    @app_commands.describe(command="Command name for detailed help (leave blank for the full reference)")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def help(self, ctx: commands.Context, command: Optional[str] = None):
        prefix = self.bot.prefixes.get(str(ctx.guild.id), self.bot.default_prefix)

        # Detail view: !help cban  (single ephemeral embed, no pagination)
        if command:
            cmd = _FLAT.get(command.lower())
            # Hide owner-only commands from non-owners
            if cmd and cmd.get("perms") == "Bot Owner":
                if not await self.bot.is_owner(ctx.author):
                    cmd = None
            if not cmd:
                return await ctx.reply(
                    embed=h.err(
                        "No command named `" + command + "`.\n"
                        "Use `" + prefix + "help` to browse all commands."
                    ),
                    ephemeral=True,
                )

            e = h.embed(title=" " + prefix + cmd["usage"], color=h.BLUE)
            e.description = cmd["desc"] + "\n\u200b"

            if cmd["args"]:
                args_text = "\n".join("`" + a + "` — " + d for a, d in cmd["args"])
                e.add_field(name="Arguments", value=args_text, inline=False)

            e.add_field(name="Required Permission", value=cmd["perms"], inline=True)
            e.add_field(name="Example", value="`" + cmd["example"] + "`", inline=False)

            if cmd.get("aliases"):
                e.add_field(name="Aliases", value=", ".join("`" + a + "`" for a in cmd["aliases"]), inline=False)

            e.set_footer(text="[ ] = optional  ·  < > = required  ·  NanoBot")
            return await ctx.reply(embed=e, ephemeral=True)

        # Paginated overview
        is_owner = await self.bot.is_owner(ctx.author)
        pages = _build_help_pages(prefix, self.bot.user.display_name, is_owner=is_owner)
        view  = HelpView(pages=pages, author=ctx.author)
        msg   = await ctx.reply(embed=pages[0], view=view)
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
            return await ctx.reply(embed=h.err("Prefix must be **5 characters or fewer**."), ephemeral=True)
        if " " in new_prefix:
            return await ctx.reply(embed=h.err("Prefix can't contain spaces."), ephemeral=True)

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
        aliases=["server", "helpserver"],
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
        await ctx.reply(embed=e)

    # ══════════════════════════════════════════════════════════════════════════
    #  ping
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(name="ping", description="Check NanoBot's latency.")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def ping(self, ctx: commands.Context):
        ms     = round(self.bot.latency * 1000)
        status = "🟢 Great" if ms < 100 else ("🟡 Okay" if ms < 200 else "🔴 Slow")
        await ctx.reply(embed=h.ok(f"**{ms}ms** — {status}", "🏓 Pong!"))

    # ══════════════════════════════════════════════════════════════════════════
    #  info
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(name="info", description="NanoBot stats and runtime info.")
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def info(self, ctx: commands.Context):
        prefix  = self.bot.prefixes.get(str(ctx.guild.id), self.bot.default_prefix)
        latency = round(self.bot.latency * 1000)

        e = h.embed(title="⚡ NanoBot", color=h.BLUE)
        e.set_thumbnail(url=self.bot.user.display_avatar.url)
        e.description = "_Small. Fast. Built for Mobile Mods._\n\u200b"

        e.add_field(name="📡 Latency",  value=f"{latency}ms",                     inline=True)
        e.add_field(name="🌐 Servers",  value=str(len(self.bot.guilds)),           inline=True)
        e.add_field(name="⚙️ Prefix",   value=f"`{prefix}`",                      inline=True)
        e.add_field(name="📚 Library",  value=f"discord.py {discord.__version__}", inline=True)
        e.add_field(name="🐍 Python",   value=platform.python_version(),           inline=True)
        e.add_field(name="🗄️ Storage",  value="JSON (no database)",                inline=True)

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
            ban_members        = True,
            kick_members       = True,
            moderate_members   = True,   # Timeout
            manage_channels    = True,   # Slowmode / lock
            manage_messages    = True,   # Purge
            # Communication
            send_messages      = True,
            embed_links        = True,
            read_messages      = True,
            read_message_history = True,
            attach_files       = True,   # Tag image uploads
            add_reactions      = True,
            # Voice (freeze affects VC)
            move_members       = False,  # Not needed
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
            "Manage Channels · Manage Messages\n"
            "Send Messages · Embed Links · Read History\n"
            "Attach Files · Add Reactions"
        )
        e.add_field(name="🔐 Requested Permissions", value=perms_list, inline=False)
        e.add_field(
            name  = "⚠️ Required Intents",
            value = (
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
            name  = "🔥 What makes it different",
            value = (
                "**Last-sender targeting** — most mod commands work with no user specified; "
                "the bot targets whoever last spoke in the channel.\n"
                "**Tag shortcuts** — `!tagname` fires any tag with one tap.\n"
                "**Clean embeds** — every response is designed to be readable on a small screen.\n"
                "**No database** — everything is plain JSON. Portable, readable, easy to back up.\n"
                "**No bloat** — commands exist because mobile mods actually need them."
            ),
            inline=False,
        )

        e.add_field(
            name  = "🧬 Philosophy",
            value = (
                "NanoBot is intentionally small. It doesn't try to replace every mod bot — "
                "it tries to make the things you do every day faster and less annoying.\n"
                "Not enterprise. Not overengineered. Just useful."
            ),
            inline=False,
        )

        e.add_field(
            name  = "📦 Tech",
            value = (
                f"Built with Python {platform.python_version()} + discord.py {discord.__version__}\n"
                "Storage: JSON files (no database, no cloud dependency)\n"
                "Self-host friendly — if you can run Python, you can run NanoBot."
            ),
            inline=False,
        )

        e.add_field(
            name  = "🔗 Links",
            value = (
                "[GitHub](https://github.com/therealjustsnow/NanoBot) — Source code\n"
                "Open source · MIT License"
            ),
            inline=False,
        )

        e.set_footer(text="NanoBot — Built by someone who actually moderates on mobile.")
        await ctx.reply(embed=e)



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
        g   = ctx.guild
        now = discord.utils.utcnow()

        total  = g.member_count or 0
        bots   = sum(1 for m in g.members if m.bot)
        humans = total - bots
        online = sum(1 for m in g.members if m.status != discord.Status.offline and not m.bot)

        text_ch  = len(g.text_channels)
        voice_ch = len(g.voice_channels)
        cats     = len(g.categories)
        threads  = len(g.threads)

        color = g.me.color if g.me.color != discord.Color.default() else h.BLUE
        e = h.embed(title="🏰 " + g.name, color=color)

        if g.icon:
            e.set_thumbnail(url=g.icon.url)
        if g.banner:
            e.set_image(url=g.banner.with_size(1024).url)

        e.description = g.description or ""

        e.add_field(name="👑 Owner",    value=g.owner.mention if g.owner else str(g.owner_id), inline=True)
        e.add_field(name="🆔 ID",       value="`" + str(g.id) + "`",                           inline=True)
        e.add_field(name="📅 Created",  value=discord.utils.format_dt(g.created_at, style="R"), inline=True)

        members_val = (
            "**" + str(total) + "** total\n"
            + "🟢 " + str(online) + " online · "
            + "👤 " + str(humans) + " humans · "
            + "🤖 " + str(bots) + " bots"
        )
        e.add_field(name="👥 Members", value=members_val, inline=True)

        channels_val = (
            "📝 " + str(text_ch) + " text · 🔊 " + str(voice_ch) + " voice\n"
            + "📁 " + str(cats) + " categories · 🧵 " + str(threads) + " threads"
        )
        e.add_field(name="💬 Channels", value=channels_val, inline=True)
        e.add_field(name="🎭 Roles",    value=str(len(g.roles) - 1), inline=True)

        boosts   = g.premium_subscription_count
        tier     = g.premium_tier
        bar      = "🟣" * boosts + "⬛" * max(0, 14 - boosts)
        e.add_field(
            name  = "💎 Boost — Level " + str(tier),
            value = bar + "\n" + str(boosts) + " boosts",
            inline=False,
        )

        feature_map = {
            "VERIFIED":   "✅ Verified",
            "PARTNERED":  "🤝 Partner",
            "COMMUNITY":  "🏘️ Community",
            "DISCOVERABLE": "🔍 Discoverable",
            "NEWS":       "📰 News Channels",
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
        now    = discord.utils.utcnow()

        created = discord.utils.format_dt(target.created_at, style="R")
        joined  = discord.utils.format_dt(target.joined_at,  style="R") if target.joined_at else "Unknown"

        roles     = [r for r in reversed(target.roles) if r != ctx.guild.default_role]
        roles_str = " ".join(r.mention for r in roles[:8])
        if len(roles) > 8:
            roles_str += " _+" + str(len(roles) - 8) + " more_"
        if not roles_str:
            roles_str = "_None_"

        color = target.color.value if target.color != discord.Color.default() else h.GREY
        e = discord.Embed(title="👤 " + target.display_name, color=color)
        e.set_thumbnail(url=target.display_avatar.url)

        e.add_field(name="🏷️ Username",     value="`" + str(target) + "`",         inline=True)
        e.add_field(name="🆔 ID",            value="`" + str(target.id) + "`",      inline=True)
        e.add_field(name="🤖 Bot",           value="Yes" if target.bot else "No",   inline=True)
        e.add_field(name="📅 Joined Server", value=joined,                           inline=True)
        e.add_field(name="📅 Account Age",   value=created,                          inline=True)

        status_icons = {
            discord.Status.online:  "🟢 Online",
            discord.Status.idle:    "🟡 Idle",
            discord.Status.dnd:     "🔴 Do Not Disturb",
            discord.Status.offline: "⚫ Offline",
        }
        status_str = status_icons.get(target.status, "⚫ Offline")
        if target.activity:
            act = target.activity
            if isinstance(act, discord.Streaming):
                status_str += "\n🟣 Streaming **" + act.name + "**"
            elif isinstance(act, discord.Game):
                status_str += "\n🎮 Playing **" + act.name + "**"
            elif isinstance(act, discord.Spotify):
                status_str += "\n🎵 **" + act.title + "** by " + act.artist
            elif act.name:
                status_str += "\n▶️ " + act.name
        e.add_field(name="📡 Status", value=status_str, inline=True)

        if target.timed_out_until and target.timed_out_until > now:
            e.add_field(
                name  = "🧊 Timed Out",
                value = "Until " + discord.utils.format_dt(target.timed_out_until, style="R"),
                inline=True,
            )
        if target.premium_since:
            e.add_field(
                name  = "💎 Boosting Since",
                value = discord.utils.format_dt(target.premium_since, style="R"),
                inline=True,
            )

        e.add_field(name="🎭 Roles (" + str(len(roles)) + ")", value=roles_str, inline=False)

        flags  = target.public_flags
        badges = []
        if flags.staff:                  badges.append("🛡️ Discord Staff")
        if flags.partner:                badges.append("🤝 Partner")
        if flags.hypesquad:              badges.append("🏠 HypeSquad")
        if flags.bug_hunter:             badges.append("🐛 Bug Hunter")
        if flags.early_supporter:        badges.append("🏷️ Early Supporter")
        if flags.verified_bot_developer: badges.append("🔧 Bot Dev")
        if flags.active_developer:       badges.append("💻 Active Dev")
        if badges:
            e.add_field(name="🏅 Badges", value=" · ".join(badges), inline=False)

        # Note count — only shown if notes exist
        from utils import db as _db
        _note_count = await _db.get_note_count(ctx.guild.id, target.id)
        if _note_count:
            e.add_field(
                name  = "📜 Mod Notes",
                value = str(_note_count) + " note(s) on file. Use `notes @user` to view.",
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
    async def avatar(self, ctx: commands.Context, user: Optional[discord.Member] = None):
        target = user or ctx.author
        av     = target.display_avatar.with_size(1024)

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
                formats.append("[GIF](" + target.display_avatar.with_format("gif").with_size(1024).url + ")")
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
    async def banner(self, ctx: commands.Context, user: Optional[discord.Member] = None):
        target = user or ctx.author

        try:
            fetched = await self.bot.fetch_user(target.id)
        except discord.HTTPException:
            fetched = None

        banner = fetched.banner if fetched else None
        if not banner:
            return await ctx.reply(
                embed=h.info(
                    "**" + target.display_name + "** doesn't have a profile banner set.",
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
                formats.append("[GIF](" + banner.with_format("gif").with_size(1024).url + ")")
            except (ValueError, discord.InvalidArgument):
                pass

        e.description = " · ".join(formats) if formats else ""
        e.set_footer(text="NanoBot")
        await ctx.reply(embed=e)

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
            "administrator":    "⛔ Administrator",
            "ban_members":      "🔨 Ban Members",
            "kick_members":     "👢 Kick Members",
            "manage_guild":     "⚙️ Manage Server",
            "manage_channels":  "📢 Manage Channels",
            "manage_messages":  "🗑️ Manage Messages",
            "manage_roles":     "🎭 Manage Roles",
            "moderate_members": "🧊 Timeout Members",
            "mention_everyone": "📣 Mention Everyone",
            "manage_webhooks":  "🔗 Manage Webhooks",
        }
        active_perms = [label for perm, label in key_perms.items() if getattr(role.permissions, perm, False)]

        color = role.color if role.color != discord.Color.default() else h.GREY
        e = discord.Embed(title="🎭 " + role.name, color=color)

        e.add_field(name="🆔 Role ID",    value="`" + str(role.id) + "`",                              inline=True)
        e.add_field(name="👥 Members",    value=str(len(role.members)),                                 inline=True)
        e.add_field(name="📅 Created",    value=discord.utils.format_dt(role.created_at, style="R"),   inline=True)
        e.add_field(name="🎨 Color",      value=str(role.color),                                        inline=True)
        e.add_field(name="📌 Position",   value=str(role.position) + " / " + str(len(ctx.guild.roles)), inline=True)
        e.add_field(name="💬 Mentionable", value="Yes" if role.mentionable else "No",                   inline=True)
        e.add_field(name="📋 Hoisted",    value="Yes" if role.hoist else "No",                          inline=True)
        e.add_field(name="🤖 Managed",    value="Yes (bot/integration)" if role.managed else "No",      inline=True)

        e.add_field(
            name  = "🔐 Key Permissions",
            value = "\n".join(active_perms) if active_perms else "_None of note_",
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
        now     = discord.utils.utcnow()
        delta   = now - self.bot.start_time
        seconds = int(delta.total_seconds())

        days,    rem  = divmod(seconds, 86400)
        hours,   rem  = divmod(rem,     3600)
        minutes, secs = divmod(rem,     60)

        def unit(n, word):
            return str(n) + " " + word + ("s" if n != 1 else "")

        parts = []
        if days:                parts.append(unit(days,    "day"))
        if hours:               parts.append(unit(hours,   "hour"))
        if minutes:             parts.append(unit(minutes, "minute"))
        if secs or not parts:   parts.append(unit(secs,    "second"))

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
