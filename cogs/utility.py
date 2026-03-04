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
            "name": "whois",
            "aliases": [],
            "usage": "whois [user]",
            "short": "User info card — mobile-optimised",
            "desc": (
                "Displays a clean embed with a user's ID, join date, account age, roles, "
                "timeout status, Discord badges, and mod note count. Designed to be readable on a phone screen."
            ),
            "args": [("user", "User to inspect (blank = yourself)")],
            "perms":   "None",
            "example": "!whois @user",
        },
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
            "short": "Save & retrieve text snippets — personal or server-wide",
            "desc": (
                "Tags let you save text (and images) and quickly DM them to yourself or others.\n\n"
                "**Shorthand (fastest on mobile):**\n"
                "`!tag <n>` — DM yourself tag *n*\n"
                "`!<n>` — same, even shorter\n"
                "`!tag + <n> <content>` — create personal tag\n"
                "`!tag add <n> <content>` — same\n"
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


# ══════════════════════════════════════════════════════════════════════════════
class Utility(commands.Cog):
    """Bot configuration and info commands."""

    # ══════════════════════════════════════════════════════════════════════════
    #  help
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="help",
        description="Command list, or !help <command> for detailed info on one command.",
    )
    @app_commands.describe(command="Command name for detailed help (leave blank for overview)")
    async def help(self, ctx: commands.Context, command: Optional[str] = None):
        prefix = self.bot.prefixes.get(str(ctx.guild.id), self.bot.default_prefix)

        # ── Detail view: !help cban ────────────────────────────────────────────
        if command:
            cmd = _FLAT.get(command.lower())
            if not cmd:
                return await ctx.reply(
                    embed=h.err(
                        f"No command named `{command}`.\n"
                        f"Use `{prefix}help` to see all commands."
                    ),
                    ephemeral=True,
                )

            e = h.embed(
                title = f"📖 {prefix}{cmd['usage']}",
                color = h.BLUE,
            )
            e.description = cmd["desc"] + "\n\u200b"

            if cmd["args"]:
                args_text = "\n".join(f"`{a}` — {d}" for a, d in cmd["args"])
                e.add_field(name="Arguments", value=args_text, inline=False)

            e.add_field(name="Required Permission", value=cmd["perms"],      inline=True)
            e.add_field(name="Example",             value=f"`{cmd['example']}`", inline=False)

            if cmd.get("aliases"):
                e.add_field(name="Aliases", value=", ".join(f"`{a}`" for a in cmd["aliases"]), inline=False)

            e.set_footer(text=f"[ ] = optional  ·  < > = required  ·  NanoBot")
            return await ctx.reply(embed=e, ephemeral=True)

        # ── Overview: !help ────────────────────────────────────────────────────
        e = h.embed(
            title       = "⚡ NanoBot — Command Reference",
            description = (
                f"Prefix: `{prefix}` · Slash `/` · @{self.bot.user.display_name}\n"
                f"Most mod commands default to the **last message sender** if no user is given.\n"
                f"Use `{prefix}help <command>` for detailed info on any command.\n\u200b"
            ),
            color = h.BLUE,
        )

        for category, cmds in _HELP.items():
            lines = []
            for cmd in cmds:
                line = f"`{prefix}{cmd['name']}`"
                if cmd.get("aliases"):
                    line += f" _(also: {', '.join(f'`{a}`' for a in cmd['aliases'])})_"
                line += f" — {cmd['short']}"
                lines.append(line)
            e.add_field(name=category, value="\n".join(lines) + "\n\u200b", inline=False)

        e.set_footer(text="NanoBot — Small. Fast. Built for Mobile Mods.")
        await ctx.reply(embed=e, ephemeral=True)

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

        self.bot.prefixes[str(ctx.guild.id)] = new_prefix
        self.bot.save_prefixes()

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
    #  ping
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(name="ping", description="Check NanoBot's latency.")
    async def ping(self, ctx: commands.Context):
        ms     = round(self.bot.latency * 1000)
        status = "🟢 Great" if ms < 100 else ("🟡 Okay" if ms < 200 else "🔴 Slow")
        await ctx.reply(embed=h.ok(f"**{ms}ms** — {status}", "🏓 Pong!"))

    # ══════════════════════════════════════════════════════════════════════════
    #  info
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(name="info", description="NanoBot stats and runtime info.")
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

        e.set_footer(text="NanoBot — Open Source · github.com/YOUR_USER/nanobot")
        await ctx.reply(embed=e)

    # ══════════════════════════════════════════════════════════════════════════
    #  invite
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="invite",
        description="Get NanoBot's invite link with the correct permissions.",
    )
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
                "[GitHub](https://github.com/YOUR_USER/nanobot) — Source code\n"
                "Open source · MIT License"
            ),
            inline=False,
        )

        e.set_footer(text="NanoBot — Built by someone who actually moderates on mobile.")
        await ctx.reply(embed=e)


# ── Registration ───────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(Utility(bot))
