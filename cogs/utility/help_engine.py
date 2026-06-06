"""Help engine for the utility cog: category model, slash-group metadata, and
the paginated HelpView. Commands register help metadata via extras={...} on
their decorator; the engine walks bot.commands at call-time so it never goes
stale."""

import discord
from discord.ext import commands

from utils import helpers as h

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
    "🎵 Music",
    "🎉 Fun",
    "😄 React",
    "🖼️ Images",
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
        "usage": "/automod <enable|disable|rule|spam|caps|mentions|badword|regex|timeout|attachments|attachword|ignore|status>",
        "desc": (
            "Watches every message and enforces configurable rules automatically.\n\n"
            "**Rules:** spam, invites, links, caps, mentions, badwords, regex, word+attachment\n"
            "**Actions per rule:** delete (silent) · warn (delete + warning) · timeout (delete + Discord timeout) · kick · softban\n\n"
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
            ("timeout <minutes>", "Set how long the timeout action lasts (1–10080)"),
            (
                "attachments <count>",
                "Min attachments that trigger the word+attachment rule",
            ),
            (
                "attachword add|remove|list [word]",
                "Manage the word+attachment filter list",
            ),
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
        "usage": "/roles panel <create|post|edit|delete|list|reload> | /roles <add|remove|autogen>",
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


def _is_admin_cog(cmd) -> bool:
    """Return True if the command belongs to the Admin cog (owner-only)."""
    cog = getattr(cmd, "cog", None)
    return cog is not None and type(cog).__name__ == "Admin"


def _collect_categories(
    bot: commands.Bot, *, is_owner: bool = False
) -> dict[str, list[dict]]:
    """
    Walk bot.commands and group commands by their extras['category'].

    Returns an ordered dict: {category_name: [cmd_entry, ...]}
      name, aliases, category, short, usage, desc, args, perms, example
    Commands without extras and not in Admin cog land in '📦 Uncategorized'.
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
        in_admin = _is_admin_cog(cmd)
        cat = extras.get(
            "category", "🔧 Owner / Admin" if in_admin else "📦 Uncategorized"
        )
        perms = extras.get("perms", "Bot Owner" if in_admin else "None")

        if cat in _OWNER_CATEGORIES and not is_owner:
            continue

        entry = {
            "name": cmd.name,
            "aliases": list(cmd.aliases) if hasattr(cmd, "aliases") else [],
            "category": cat,
            "short": extras.get("short", cmd.description or "—"),
            "usage": extras.get("usage", cmd.name),
            "desc": extras.get(
                "desc",
                getattr(cmd, "help", None)
                or cmd.description
                or "No description available.",
            ),
            "args": extras.get("args", []),
            "perms": perms,
            "example": extras.get("example", "{prefix}" + cmd.name),
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
    Return {name: entry, alias: entry} for ALL commands.
    Commands without extras get best-effort fallback values.
    Admin-cog commands get perms='Bot Owner' so the owner check hides them.
    Includes slash group entries from _SLASH_GROUP_LOOKUP.
    """
    out: dict[str, dict] = {}

    for cmd in bot.commands:
        extras = getattr(cmd, "extras", None) or {}
        in_admin = _is_admin_cog(cmd)
        cat = extras.get("category", "🔧 Owner / Admin" if in_admin else "")
        perms = extras.get("perms", "Bot Owner" if in_admin else "None")
        entry = {
            "name": cmd.name,
            "aliases": list(cmd.aliases) if hasattr(cmd, "aliases") else [],
            "category": cat,
            "short": extras.get("short", cmd.description or "—"),
            "usage": extras.get("usage", cmd.name),
            "desc": extras.get(
                "desc",
                getattr(cmd, "help", None)
                or cmd.description
                or "No description available.",
            ),
            "args": extras.get("args", []),
            "perms": perms,
            "example": extras.get("example", "{prefix}" + cmd.name),
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
    # 🎉 Fun
    "fun": "🎉 Fun",
    "social": "🎉 Fun",
    "ship": "🎉 Fun",
    "8ball": "🎉 Fun",
    # 😄 React
    "react": "😄 React",
    "reaction": "😄 React",
    "reactions": "😄 React",
    "emote": "😄 React",
    "emotes": "😄 React",
    # 🖼️ Images
    "images": "🖼️ Images",
    "image": "🖼️ Images",
    "anime": "🖼️ Images",
    "waifu": "🖼️ Images",
    "neko": "🖼️ Images",
    "kitsune": "🖼️ Images",
    "husbando": "🖼️ Images",
    # 🔧 Owner / Admin
    "admin": "🔧 Owner / Admin",
    "owner": "🔧 Owner / Admin",
    "reload": "🔧 Owner / Admin",
    "update": "🔧 Owner / Admin",
    # 📦 Uncategorized
    "uncategorized": "📦 Uncategorized",
    "other": "📦 Uncategorized",
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
            f"`{prefix}help <category>` — browse a category (e.g. `{prefix}help banning`)\n"
            f"`{prefix}help <number>` — jump to a page (e.g. `{prefix}help 3`)\n\n"
            + "\n".join(cover_lines)
            + "\n\n[Support Server](https://discord.gg/M7fjxNg72s)  ·  [Documentation](https://snowbuilds.dev/nanobot-docs/)"
        ),
        color=h.BLUE,
    )
    cover.set_footer(text="NanoBot")
    cover.timestamp = discord.utils.utcnow()
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

    def __init__(
        self,
        pages: list[discord.Embed],
        author: discord.Member,
        start_index: int = 0,
    ):
        super().__init__(timeout=120)
        self.pages = pages
        self.author = author
        self.index = start_index
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
