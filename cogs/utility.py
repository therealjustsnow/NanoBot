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


# ── Help engine ───────────────────────────────────────────────────────────────
# Commands register their own help metadata via extras={...} on their decorator.
# The engine walks bot.commands at call-time so it never goes stale.
#
# extras keys (all required except aliases):
#   category  str   — e.g. "🔨 Banning"
#   short     str   — one-line summary shown in category listings
#   usage     str   — e.g. "cban [user] [days] [wait] [message]"
#   desc      str   — full description shown in detail view
#   args      list  — list of (name, desc) tuples
#   perms     str   — e.g. "Ban Members" or "None" or "Bot Owner"
#   example   str   — example invocation(s), newline-separated
#
# Pure app_commands.Group commands (auditlog, automod, roles panels) cannot
# carry extras on their group definition, so they live in _SLASH_GROUPS below.
# ──────────────────────────────────────────────────────────────────────────────

# Display order for the paginated overview. Categories not in this list are
# appended at the end (before Owner / Admin) so new cogs appear automatically.
_CATEGORY_ORDER: list[str] = [
    "🔨 Banning",
    "👢 Kicking & Timeouts",
    "📢 Channel Controls",
    "🎭 Roles",
    "⚠️ Warnings",
    "🔎 Info & Notes",
    "🏷️ Tags",
    "👋 Welcome & Leave",
    "🔍 Server & User Info",
    "⏰ Reminders",
    "📋 Audit Log",
    "🛡️ Auto Mod",
    "🎛️ Role Panels",
    "🗳️ Voting",
    "⚙️ Config & Info",
    "🔧 Owner / Admin",
]

_OWNER_CATEGORIES: set[str] = {"🔧 Owner / Admin"}

# Static entries for pure-slash app_commands.Group trees that cannot carry
# extras on their decorator. Each entry will appear in the category listing
# and support !help <name> detail lookups.
_SLASH_GROUPS: list[dict] = [
    {
        "name": "auditlog",
        "aliases": [],
        "category": "📋 Audit Log",
        "short": "Configure the server audit log feed",
        "usage": "/auditlog <channel|enable|disable|events|status>",
        "desc": (
            "Posts a live feed of server events to a dedicated channel. "
            "12 toggleable event types: message deletes/edits, member join/leave/ban/unban, "
            "nickname changes, role updates, channel and role creation/deletion.\n\n"
            "**Setup:** /auditlog channel #channel → /auditlog enable → /auditlog events"
        ),
        "args": [
            ("channel #channel", "Set the channel that receives log entries"),
            (
                "enable / disable",
                "Master on/off switch (config is preserved when disabled)",
            ),
            ("events", "Opens a dropdown to toggle individual event types"),
            ("status", "Show current channel, enabled state, and active events"),
        ],
        "perms": "Manage Server",
        "example": "/auditlog channel #audit-log\n/auditlog enable\n/auditlog events",
    },
    {
        "name": "automod",
        "aliases": [],
        "category": "🛡️ Auto Mod",
        "short": "Passive rule-based message moderation",
        "usage": "/automod <enable|disable|rule|spam|caps|mentions|badword|regex|ignore|status>",
        "desc": (
            "Watches every message and enforces configurable rules automatically.\n\n"
            "**Rules:** spam, invites, links, caps, mentions, badwords, regex\n"
            "**Actions per rule:** delete (silent) · warn (delete + warning) · timeout (delete + 10-min timeout)\n\n"
            "Exempt channels and roles are ignored for all rules."
        ),
        "args": [
            ("enable / disable", "Master on/off switch"),
            ("rule <rule> <enabled> [action]", "Toggle a rule and set its action"),
            ("spam <count> <seconds>", "Set spam detection threshold"),
            ("caps <percent> <min_length>", "Set caps-abuse threshold"),
            ("mentions <limit>", "Set per-message mention limit"),
            ("badword add|remove|list [word]", "Manage the custom word filter"),
            ("regex add|remove|list|test", "Manage regex patterns"),
            (
                "ignore add|remove <channel or role>",
                "Exempt a channel or role from all rules",
            ),
            ("status", "Full configuration overview"),
        ],
        "perms": "Manage Server",
        "example": "/automod enable\n/automod rule invites True warn\n/automod badword add slur",
    },
    {
        "name": "roles",
        "aliases": [],
        "category": "🎛️ Role Panels",
        "short": "Button-based self-assignable role panels",
        "usage": "/roles panel <create|post|edit|delete|list> | /roles <add|remove|autogen>",
        "desc": (
            "Create persistent button panels that let members assign their own roles. "
            "Panels survive bot restarts.\n\n"
            "**Modes:** toggle (click to add/remove — default) · single (radio-style, one role at a time)\n\n"
            "**autogen presets:** colors (18 roles), pronouns, age ranges, world regions"
        ),
        "args": [
            (
                "panel create <name> [desc] [mode]",
                "Create a panel definition (not yet posted)",
            ),
            (
                "panel post <name> [channel]",
                "Post or re-post a panel as a button embed",
            ),
            ("panel edit <name> [title] [desc] [mode]", "Edit a posted panel"),
            ("panel delete <name>", "Delete the panel and its message"),
            ("panel list", "List all panels in this server"),
            ("add <panel> <role> [label] [emoji]", "Add a role button to a panel"),
            ("remove <panel> <role>", "Remove a role button from a panel"),
            (
                "autogen <colors|pronouns|age|region>",
                "Generate a preset role set + panel",
            ),
        ],
        "perms": "Manage Roles",
        "example": "/roles panel create Colours Pick your colour!\n/roles add Colours @Red 🔴\n/roles panel post Colours #roles",
    },
]

# Build a flat name→entry lookup for slash groups (used by !help <cmd>)
_SLASH_GROUP_LOOKUP: dict[str, dict] = {entry["name"]: entry for entry in _SLASH_GROUPS}
for _entry in _SLASH_GROUPS:
    for _alias in _entry.get("aliases", []):
        _SLASH_GROUP_LOOKUP[_alias] = _entry


def _collect_categories(
    bot: commands.Bot, *, is_owner: bool = False
) -> dict[str, list[dict]]:
    """
    Walk bot.commands and group commands by their extras['category'].

    Returns an ordered dict: {category_name: [cmd_entry, ...]}
      name, aliases, category, short, usage, desc, args, perms, example
    Commands without extras land in '📦 Uncategorized'.
    Owner categories are hidden from non-owners.
    Order follows _CATEGORY_ORDER; unknown categories append before Owner/Admin.
    """
    by_cat: dict[str, list[dict]] = {}

    # Collect hybrid / prefix commands
    seen: set[str] = set()
    for cmd in bot.commands:
        if cmd.name in seen:
            continue
        seen.add(cmd.name)

        extras = getattr(cmd, "extras", None) or {}
        cat = extras.get("category", "📦 Uncategorized")

        if cat in _OWNER_CATEGORIES and not is_owner:
            continue

        entry = {
            "name": cmd.name,
            "aliases": list(cmd.aliases) if hasattr(cmd, "aliases") else [],
            "category": cat,
            "short": extras.get("short", cmd.description or "—"),
            "usage": extras.get("usage", cmd.name),
            "desc": extras.get("desc", cmd.description or "No description available."),
            "args": extras.get("args", []),
            "perms": extras.get("perms", "None"),
            "example": extras.get("example", f"!{cmd.name}"),
        }
        by_cat.setdefault(cat, []).append(entry)

    # Inject slash-only groups into their categories
    for sg in _SLASH_GROUPS:
        cat = sg["category"]
        if cat in _OWNER_CATEGORIES and not is_owner:
            continue
        by_cat.setdefault(cat, []).append(sg)

    # Sort each category's commands: entries with extras first (has 'usage' key
    # from extras), slash-groups second, then alphabetically within each group.
    # Actually just sort alphabetically — natural enough.
    for cat in by_cat:
        by_cat[cat].sort(key=lambda e: e["name"])

    # Build ordered result following _CATEGORY_ORDER
    ordered: dict[str, list[dict]] = {}
    for cat in _CATEGORY_ORDER:
        if cat in by_cat:
            ordered[cat] = by_cat[cat]

    # Append any unknown categories (new cogs) before returning
    for cat, cmds in by_cat.items():
        if cat not in ordered:
            ordered[cat] = cmds

    return ordered


def _flat_lookup(bot: commands.Bot) -> dict[str, dict]:
    """
    Return {name: entry, alias: entry} for all commands that have extras.
    Used by !help <cmd> for detail lookups.
    Includes slash group entries from _SLASH_GROUP_LOOKUP.
    """
    out: dict[str, dict] = {}

    for cmd in bot.commands:
        extras = getattr(cmd, "extras", None) or {}
        if not extras.get("category"):
            continue  # skip commands without extras
        entry = {
            "name": cmd.name,
            "aliases": list(cmd.aliases) if hasattr(cmd, "aliases") else [],
            "category": extras.get("category", ""),
            "short": extras.get("short", ""),
            "usage": extras.get("usage", cmd.name),
            "desc": extras.get("desc", ""),
            "args": extras.get("args", []),
            "perms": extras.get("perms", "None"),
            "example": extras.get("example", f"!{cmd.name}"),
        }
        out[cmd.name] = entry
        for alias in cmd.aliases or []:
            out[alias] = entry

    # Merge slash group entries
    out.update(_SLASH_GROUP_LOOKUP)

    return out


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
        is_slash_only = cmd["name"] in _SLASH_GROUP_LOOKUP
        name_str = f"`/{cmd['name']}`" if is_slash_only else f"`{prefix}{cmd['name']}`"
        entry = name_str
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


def _build_help_pages(
    bot: commands.Bot, prefix: str, bot_name: str, *, is_owner: bool = False
) -> list[discord.Embed]:
    """
    Build one embed per help category, plus a cover page.
    Reads command extras at call-time — always current, never stale.
    Owner-only categories are hidden from non-owners.
    """
    categories = list(_collect_categories(bot, is_owner=is_owner).items())
    total = len(categories) + 1  # +1 for cover

    def footer(page_num: int) -> str:
        return f"Page {page_num} / {total}  ·  NanoBot"

    pages = []

    # Cover page
    cover_lines = []
    for cat, cmds in categories:
        n = len(cmds)
        cover_lines.append(f"**{cat}** — {n} command{'s' if n != 1 else ''}")

    cover = h.embed(
        title="⚡ NanoBot — Command Reference",
        description=(
            f"Prefix: `{prefix}` · Slash `/` · @{bot_name}\n"
            "Most mod commands default to the **last message sender** if no user is given.\n\n"
            f"`{prefix}help <command>` — full detail on any command\n"
            f"`{prefix}help <category>` — browse a category (e.g. `{prefix}help banning`)\n\n"
            + "\n".join(cover_lines)
        ),
        color=h.BLUE,
    )
    cover.set_footer(text=footer(1))
    pages.append(cover)

    # One page per category
    for i, (category, cmds) in enumerate(categories, start=2):
        lines = []
        for cmd in cmds:
            is_slash_only = cmd["name"] in _SLASH_GROUP_LOOKUP
            pfx = "/" if is_slash_only else prefix
            line = f"`{pfx}{cmd['name']}`"
            if cmd.get("aliases"):
                line += " _(also: " + ", ".join(f"`{a}`" for a in cmd["aliases"]) + ")_"
            line += f" — {cmd['short']}"
            lines.append(line)

        e = h.embed(title=category, color=h.BLUE)
        e.description = (
            "\n".join(lines)
            + f"\n\nUse `{prefix}help <command>` for details on any command."
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
            flat = _flat_lookup(self.bot)
            cmd = flat.get(key)
            if cmd and cmd.get("perms") == "Bot Owner" and not is_owner:
                cmd = None  # hide owner commands from non-owners

            if cmd:
                is_slash_only = cmd["name"] in _SLASH_GROUP_LOOKUP
                title_prefix = "/" if is_slash_only else prefix
                e = h.embed(title=f"`{title_prefix}{cmd['usage']}`", color=h.BLUE)
                e.description = cmd["desc"] + "\n\u200b"
                if cmd["args"]:
                    e.add_field(
                        name="Arguments",
                        value="\n".join(f"`{a}` — {d}" for a, d in cmd["args"]),
                        inline=False,
                    )
                e.add_field(name="Required Permission", value=cmd["perms"], inline=True)
                if cmd.get("example"):
                    e.add_field(
                        name="Example",
                        value="\n".join(f"`{ex}`" for ex in cmd["example"].split("\n")),
                        inline=False,
                    )
                if cmd.get("aliases"):
                    e.add_field(
                        name="Aliases",
                        value=", ".join(f"`{a}`" for a in cmd["aliases"]),
                        inline=False,
                    )
                e.set_footer(text="[ ] = optional  ·  < > = required  ·  NanoBot")
                return await ctx.reply(embed=e, ephemeral=True)

            # ── 2. Category keyword lookup ──────────────────────────────────
            cat_name = _CATEGORY_ALIASES.get(key)
            if cat_name and (cat_name not in _OWNER_CATEGORIES or is_owner):
                cats = _collect_categories(self.bot, is_owner=is_owner)
                if cat_name in cats:
                    return await ctx.reply(
                        embed=_build_category_embed(cat_name, cats[cat_name], prefix),
                        ephemeral=True,
                    )

            # ── 3. Nothing found ────────────────────────────────────────────
            return await ctx.reply(
                embed=h.err(
                    f"No command or category named `{command}`.\n"
                    f"Use `{prefix}help` to browse all categories, or try:\n"
                    f"`{prefix}help banning`  ·  `{prefix}help tags`  ·  `{prefix}help channel`"
                ),
                ephemeral=True,
            )

        # Paginated category overview
        pages = _build_help_pages(
            self.bot, prefix, self.bot.user.display_name, is_owner=is_owner
        )
        view = HelpView(pages=pages, author=ctx.author)
        msg = await ctx.reply(embed=pages[0], view=view, ephemeral=True)
        view.message = msg

    # ══════════════════════════════════════════════════════════════════════════
    #  prefix
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="prefix",
        description="View or change NanoBot's prefix for this server.",
        extras={
            "category": "⚙️ Config & Info",
            "short": "View or change the bot prefix for this server",
            "usage": "prefix [new_prefix]",
            "desc": "Shows the current prefix with no args. With a new prefix (max 5 chars, no spaces), updates it server-wide.",
            "args": [
                ("new_prefix", "New prefix (1–5 chars, no spaces). Omit to view."),
            ],
            "perms": "Administrator (to change)",
            "example": "!prefix ?",
        },
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
        extras={
            "category": "⚙️ Config & Info",
            "short": "Link to the NanoBot support server",
            "usage": "support",
            "desc": "Posts an invite link to the official NanoBot support server.",
            "args": [],
            "perms": "None",
            "example": "!support",
        },
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
    # ══════════════════════════════════════════════════════════════════════════,
    @commands.hybrid_command(
        name="ping",
        description="Check NanoBot's latency.",
        extras={
            "category": "⚙️ Config & Info",
            "short": "Check NanoBot's response time",
            "usage": "ping",
            "desc": "Returns the current WebSocket latency between NanoBot and Discord's servers.",
            "args": [],
            "perms": "None",
            "example": "!ping",
        },
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def ping(self, ctx: commands.Context):
        ms = round(self.bot.latency * 1000)
        status = "🟢 Great" if ms < 100 else ("🟡 Okay" if ms < 200 else "🔴 Slow")
        await ctx.reply(embed=h.ok(f"**{ms}ms** — {status}", "🏓 Pong!"))

    # ══════════════════════════════════════════════════════════════════════════
    #  info
    # ══════════════════════════════════════════════════════════════════════════,
    @commands.hybrid_command(
        name="info",
        description="NanoBot stats and runtime info.",
        extras={
            "category": "⚙️ Config & Info",
            "short": "Bot stats and runtime info",
            "usage": "info",
            "desc": "Shows latency, server count, prefix, discord.py version, Python version, and storage type.",
            "args": [],
            "perms": "None",
            "example": "!info",
        },
    )
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
        extras={
            "category": "⚙️ Config & Info",
            "short": "Get the bot invite link",
            "usage": "invite",
            "desc": "Generates an invite link with exactly the permissions NanoBot needs — no unnecessary extras.",
            "args": [],
            "perms": "None",
            "example": "!invite",
        },
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
        extras={
            "category": "⚙️ Config & Info",
            "short": "What NanoBot is and why it exists",
            "usage": "about",
            "desc": "The NanoBot story — why it was built, what it avoids, and what makes it different.",
            "args": [],
            "perms": "None",
            "example": "!about",
        },
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
        extras={
            "category": "🔍 Server & User Info",
            "short": "Full server info card",
            "usage": "server",
            "desc": "Member counts, boost level, channel breakdown, features, creation date and more.",
            "args": [],
            "perms": "None",
            "example": "!server",
        },
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
        extras={
            "category": "🔍 Server & User Info",
            "short": "Public user info — status, roles, badges",
            "usage": "user [user]",
            "desc": "Shows a clean user card with status, activity, join date, account age, roles and badges. Mods also see note count.",
            "args": [
                ("user", "User to look up (blank = yourself)"),
            ],
            "perms": "None",
            "example": "!user @someone",
        },
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
        extras={
            "category": "🔍 Server & User Info",
            "short": "Show a user's avatar full-size",
            "usage": "avatar [user]",
            "desc": "Fetches the avatar at 1024px with PNG/JPG/WEBP/GIF download links.",
            "args": [
                ("user", "Whose avatar to show (blank = yourself)"),
            ],
            "perms": "None",
            "example": "!avatar @someone",
        },
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
        extras={
            "category": "🔍 Server & User Info",
            "short": "Show a user's profile banner",
            "usage": "banner [user]",
            "desc": "Fetches and displays the user's profile banner with download links.",
            "args": [
                ("user", "Whose banner to show (blank = yourself)"),
            ],
            "perms": "None",
            "example": "!banner @someone",
        },
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
        extras={
            "category": "🔍 Server & User Info",
            "short": "Details about a server role",
            "usage": "roleinfo <role>",
            "desc": "Color, position, member count, creation date, hoist/mentionable status, and notable permissions.",
            "args": [
                ("role", "Mention it or type the name"),
            ],
            "perms": "None",
            "example": "!roleinfo @Moderator",
        },
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
        extras={
            "category": "🔍 Server & User Info",
            "short": "How long the bot has been running",
            "usage": "uptime",
            "desc": "Shows how long NanoBot has been online since its last start or restart.",
            "args": [],
            "perms": "None",
            "example": "!uptime",
        },
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

    # ══════════════════════════════════════════════════════════════════════════
    #  stats
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="stats",
        description="NanoBot runtime statistics since last start.",
        extras={
            "category": "⚙️ Config & Info",
            "short": "Runtime stats — commands run, servers, members, uptime",
            "usage": "stats",
            "desc": (
                "Shows a snapshot of NanoBot's activity since the last start: "
                "commands run, uptime, server count, member breakdown, "
                "channel counts, and current latency."
            ),
            "args": [],
            "perms": "None",
            "example": "!stats",
        },
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def stats(self, ctx: commands.Context):
        now = discord.utils.utcnow()
        delta = now - self.bot.start_time
        seconds = int(delta.total_seconds())

        days, rem = divmod(seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, secs = divmod(rem, 60)

        parts = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if minutes:
            parts.append(f"{minutes}m")
        if secs or not parts:
            parts.append(f"{secs}s")
        uptime_str = " ".join(parts)

        # Member breakdown across all guilds
        total_members = sum(g.member_count or 0 for g in self.bot.guilds)
        total_bots = sum(
            sum(1 for m in g.members if m.bot) for g in self.bot.guilds
        )
        total_humans = total_members - total_bots

        # Channel counts
        text_channels = sum(len(g.text_channels) for g in self.bot.guilds)
        voice_channels = sum(len(g.voice_channels) for g in self.bot.guilds)

        latency = round(self.bot.latency * 1000)
        commands_run = getattr(self.bot, "commands_run", 0)

        e = h.embed(title="📊 NanoBot Stats", color=h.BLUE)
        e.set_thumbnail(url=self.bot.user.display_avatar.url)

        e.add_field(name="⚡ Commands Run", value=f"**{commands_run:,}**", inline=True)
        e.add_field(name="⏱️ Uptime", value=f"**{uptime_str}**", inline=True)
        e.add_field(name="📡 Latency", value=f"**{latency}ms**", inline=True)

        e.add_field(name="🌐 Servers", value=f"**{len(self.bot.guilds):,}**", inline=True)
        e.add_field(
            name="👥 Members",
            value=f"**{total_humans:,}** humans · **{total_bots:,}** bots",
            inline=True,
        )
        e.add_field(
            name="💬 Channels",
            value=f"**{text_channels:,}** text · **{voice_channels:,}** voice",
            inline=True,
        )

        e.add_field(
            name="🕐 Online Since",
            value=discord.utils.format_dt(self.bot.start_time, style="R"),
            inline=False,
        )

        e.set_footer(text="NanoBot — stats reset on restart")
        await ctx.reply(embed=e, ephemeral=True)


# ── Registration ───────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(Utility(bot))
