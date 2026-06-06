"""
cogs/utility.py
Bot utility & configuration commands.

Commands:
  help     — !help (overview) or !help <cmd> (detailed)
  prefix   — view / change the guild prefix
  ping     — latency check
  support  — link to the support server
  mc       — quick member count for this server
  id       — get the ID of a user, role, or channel
  invite   — bot invite link with correct permissions
  about    — what NanoBot is and why it exists
  server   — info card for this server
  user     — public info card for a user
  avatar   — show a user's avatar
  banner   — show a user's profile banner
  roleinfo — info card for a server role
  uptime   — how long NanoBot has been running
  stats    — bot statistics
  source   — show source code for a command or symbol
  firstmsg — link to the first message in a channel
"""

import asyncio
import inspect
import logging
import os
import platform
import sys
import textwrap
import time
from typing import Optional

import psutil

import discord
from discord import app_commands
from discord.ext import commands

from utils import db
from utils import helpers as h

from .help_engine import (
    _OWNER_CATEGORIES,
    _SLASH_GROUP_LOOKUP,
    _collect_categories,
    _flat_lookup,
    _CATEGORY_ALIASES,
    _build_category_embed,
    _build_help_pages,
    HelpView,
)
from .source import _gh_url, _related_callables, _symbol_search

log = logging.getLogger("NanoBot.utility")


class Utility(commands.Cog):
    """Bot configuration and info commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Prime psutil so the first stats call returns a real CPU %
        self._proc = psutil.Process()
        self._proc.cpu_percent(interval=None)

    # ══════════════════════════════════════════════════════════════════════
    #  help
    # ══════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="help",
        description="Command reference. Use /help <command> for detail, or /help <category> to browse.",
        extras={
            "category": "⚙️ Config & Info",
            "short": "Browse all commands or get detail on one",
            "usage": "help [command|category|page]",
            "desc": (
                "Overview of all commands. Pass a command name for full details, "
                "a category keyword (e.g. banning, tags) to browse a section, "
                "or a page number to jump directly to that page."
            ),
            "args": [
                (
                    "command",
                    "Command name, category keyword, or page number (optional)",
                ),
            ],
            "perms": "None",
            "example": "{prefix}help\n{prefix}help ban\n{prefix}help banning\n{prefix}help 3",
        },
    )
    @app_commands.describe(
        command="Command name for detail, category keyword (e.g. banning, tags), or page number"
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def help(self, ctx: commands.Context, command: Optional[str] = None):
        prefix = self.bot.prefixes.get(str(ctx.guild.id), self.bot.default_prefix)
        is_owner = await self.bot.is_owner(ctx.author)

        if command:
            key = command.lower().strip()

            # ── 0. Page number jump ────────────────────────────────────────────
            if key.isdigit():
                pages = _build_help_pages(
                    self.bot, prefix, self.bot.user.display_name, is_owner=is_owner
                )
                page_num = int(key)
                if 1 <= page_num <= len(pages):
                    view = HelpView(
                        pages=pages, author=ctx.author, start_index=page_num - 1
                    )
                    msg = await ctx.reply(
                        embed=pages[page_num - 1], view=view, ephemeral=True
                    )
                    view.message = msg
                    return
                return await ctx.reply(
                    embed=h.err(
                        f"Page **{page_num}** doesn't exist. Valid range: **1–{len(pages)}**."
                    ),
                    ephemeral=True,
                )

            # ── 1. Exact command / alias lookup ───────────────────────────────
            flat = _flat_lookup(self.bot)
            cmd = flat.get(key)
            if cmd and cmd.get("perms") == "Bot Owner" and not is_owner:
                cmd = None  # hide owner commands from non-owners

            if cmd:
                is_slash_only = cmd["name"] in _SLASH_GROUP_LOOKUP
                title_prefix = "/" if is_slash_only else prefix
                e = h.embed(title=f"`{title_prefix}{cmd['usage']}`", color=h.BLUE)
                e.description = cmd["desc"].replace("{prefix}", prefix) + "\n\u200b"
                if cmd["args"]:
                    e.add_field(
                        name="Arguments",
                        value="\n".join(f"`{a}` — {d}" for a, d in cmd["args"]),
                        inline=False,
                    )
                e.add_field(name="Required Permission", value=cmd["perms"], inline=True)
                if cmd.get("example"):
                    resolved = cmd["example"].replace("{prefix}", prefix)
                    e.add_field(
                        name="Example",
                        value="\n".join(f"`{ex}`" for ex in resolved.split("\n")),
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
            "example": "{prefix}prefix ?",
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
    @commands.command(
        name="support",
        aliases=["helpserver"],
        extras={
            "category": "⚙️ Config & Info",
            "short": "Link to the NanoBot support server",
            "usage": "support",
            "desc": "Posts an invite link to the official NanoBot support server.",
            "args": [],
            "perms": "None",
            "example": "{prefix}support",
        },
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def pfx_support(self, ctx: commands.Context):
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
    #  /bot group — low-frequency bot meta commands (1 top-level slot).
    #  Prefix stays flat (!ping, !about, !stats, …); the slash twins build a
    #  Context from the interaction and reuse the prefix callbacks.
    # ══════════════════════════════════════════════════════════════════════════
    botinfo_slash = app_commands.Group(
        name="bot",
        description="NanoBot info: ping, about, invite, stats, uptime, source, support.",
    )

    @botinfo_slash.command(
        name="support", description="Link to the NanoBot support server."
    )
    async def slash_bot_support(self, interaction: discord.Interaction):
        ctx = await commands.Context.from_interaction(interaction)
        await self.pfx_support(ctx)

    @botinfo_slash.command(name="ping", description="Check NanoBot's latency.")
    async def slash_bot_ping(self, interaction: discord.Interaction):
        ctx = await commands.Context.from_interaction(interaction)
        await self.pfx_ping(ctx)

    @botinfo_slash.command(name="invite", description="Get NanoBot's invite link.")
    async def slash_bot_invite(self, interaction: discord.Interaction):
        ctx = await commands.Context.from_interaction(interaction)
        await self.pfx_invite(ctx)

    @botinfo_slash.command(
        name="about", description="What NanoBot is and why it exists."
    )
    async def slash_bot_about(self, interaction: discord.Interaction):
        ctx = await commands.Context.from_interaction(interaction)
        await self.pfx_about(ctx)

    @botinfo_slash.command(
        name="uptime", description="How long NanoBot has been running."
    )
    async def slash_bot_uptime(self, interaction: discord.Interaction):
        ctx = await commands.Context.from_interaction(interaction)
        await self.pfx_uptime(ctx)

    @botinfo_slash.command(name="stats", description="NanoBot runtime statistics.")
    async def slash_bot_stats(self, interaction: discord.Interaction):
        ctx = await commands.Context.from_interaction(interaction)
        await self.pfx_stats(ctx)

    @botinfo_slash.command(
        name="source", description="Show source for a command or symbol."
    )
    @app_commands.describe(command="Command name or symbol to inspect")
    async def slash_bot_source(self, interaction: discord.Interaction, command: str):
        ctx = await commands.Context.from_interaction(interaction)
        await self.pfx_source(ctx, command=command)

    # ══════════════════════════════════════════════════════════════════════════
    #  ping
    # ══════════════════════════════════════════════════════════════════════════
    @commands.command(
        name="ping",
        extras={
            "category": "⚙️ Config & Info",
            "short": "Check NanoBot's response time",
            "usage": "ping",
            "desc": "Returns the current WebSocket latency between NanoBot and Discord's servers.",
            "args": [],
            "perms": "None",
            "example": "{prefix}ping",
        },
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def pfx_ping(self, ctx: commands.Context):
        ws_ms = round(self.bot.latency * 1000)
        ws_status = "🟢" if ws_ms < 100 else ("🟡" if ws_ms < 200 else "🔴")

        t0 = time.perf_counter()
        await db._conn().execute("SELECT 1")
        db_ms = round((time.perf_counter() - t0) * 1000)
        db_status = "🟢" if db_ms < 10 else ("🟡" if db_ms < 50 else "🔴")

        await ctx.reply(
            embed=h.ok(
                f"{ws_status} WebSocket: **{ws_ms}ms**\n{db_status} Database: **{db_ms}ms**",
                "🏓 Pong!",
            ),
            ephemeral=True,
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  mc — quick member count
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="mc",
        aliases=["membercount"],
        description="Quick member count for this server.",
        extras={
            "category": "🔍 Server & User Info",
            "short": "Quick member count",
            "usage": "mc",
            "desc": "One-tap member count. Faster than {prefix}server for when you just need the number.",
            "args": [],
            "perms": "None",
            "example": "{prefix}mc",
        },
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def mc(self, ctx: commands.Context):
        count = ctx.guild.member_count or 0
        await ctx.reply(
            embed=h.info(f"👥 **{count:,}** members", f"🏰 {ctx.guild.name}"),
            ephemeral=True,
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  id — quick ID lookup (mobile lifesaver)
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="id",
        description="Get the ID of a user, role, or channel — copyable code block.",
        extras={
            "category": "🔍 Server & User Info",
            "short": "Show ID of a user, role, or channel",
            "usage": "id [user|role|channel]",
            "desc": (
                "Shows the Discord ID in a code block for easy copying on mobile. "
                "Accepts @mentions and #channel. No arg = your own ID."
            ),
            "args": [
                ("target", "User, role, or channel to look up (blank = yourself)")
            ],
            "perms": "None",
            "example": "{prefix}id\n{prefix}id @someone\n{prefix}id #general\n{prefix}id @Moderator",
        },
    )
    @app_commands.describe(target="User, role, or channel (blank = yourself)")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def id_cmd(
        self,
        ctx: commands.Context,
        *,
        target: Optional[str] = None,
    ):
        obj = None
        name = None

        if target:
            # Try member → role → channel in order
            try:
                obj = await commands.MemberConverter().convert(ctx, target)
                name = f"👤 {obj.display_name}"
            except commands.BadArgument:
                pass
            if obj is None:
                try:
                    obj = await commands.RoleConverter().convert(ctx, target)
                    name = f"🎭 @{obj.name}"
                except commands.BadArgument:
                    pass
            if obj is None:
                try:
                    obj = await commands.TextChannelConverter().convert(ctx, target)
                    name = f"💬 #{obj.name}"
                except commands.BadArgument:
                    pass
            if obj is None:
                return await ctx.reply(
                    embed=h.err(
                        f"Couldn't find `{target}`.\nTry mentioning them: `{ctx.prefix}id @user`"
                    ),
                    ephemeral=True,
                )
        else:
            obj = ctx.author
            name = f"👤 {ctx.author.display_name}"

        await ctx.reply(
            embed=h.info(f"{name}\n```\n{obj.id}\n```", "🆔 Discord ID"),
            ephemeral=True,
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  invite
    # ══════════════════════════════════════════════════════════════════════════
    @commands.command(
        name="invite",
        extras={
            "category": "⚙️ Config & Info",
            "short": "Get the bot invite link",
            "usage": "invite",
            "desc": "Generates an invite link with exactly the permissions NanoBot needs — no unnecessary extras.",
            "args": [],
            "perms": "None",
            "example": "{prefix}invite",
        },
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def pfx_invite(self, ctx: commands.Context):
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
    @commands.command(
        name="about",
        extras={
            "category": "⚙️ Config & Info",
            "short": "What NanoBot is and why it exists",
            "usage": "about",
            "desc": "The NanoBot story — why it was built, what it avoids, and what makes it different.",
            "args": [],
            "perms": "None",
            "example": "{prefix}about",
        },
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def pfx_about(self, ctx: commands.Context):
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
            "example": "{prefix}server",
        },
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def server(self, ctx: commands.Context, guild_id: Optional[int] = None):
        # Hidden: prefix-only guild lookup by ID
        if guild_id and ctx.interaction is None:
            g = ctx.bot.get_guild(guild_id)
            if g is None:
                return await ctx.reply(
                    embed=h.err("I'm not in that server or the ID is invalid."),
                )
        else:
            g = ctx.guild
        now = discord.utils.utcnow()

        total = g.member_count or 0
        bots = sum(1 for m in g.members if m.bot)
        humans = total - bots

        text_ch = len(g.text_channels)
        voice_ch = len(g.voice_channels)
        cats = len(g.categories)
        threads = len(g.threads)

        color = g.me.color if g.me and g.me.color != discord.Color.default() else h.BLUE
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
            "example": "{prefix}user @someone",
        },
    )
    @app_commands.describe(user="User to look up (leave blank for yourself)")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def user(self, ctx: commands.Context, user: Optional[discord.Member] = None):
        target = user or ctx.author
        # Member args resolved from a slash interaction come from Discord's
        # `resolved` payload, which carries no presence — so target.status would
        # always read "offline".  Swap in the cached guild member (populated by
        # the presences intent) so status/activity are real.
        if ctx.guild is not None:
            cached = ctx.guild.get_member(target.id)
            if cached is not None:
                target = cached
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
                    + " note(s) on file. Use `/note list` or `/modcheck` to view.",
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
            "example": "{prefix}avatar @someone",
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
            "example": "{prefix}banner @someone",
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
            "example": "{prefix}roleinfo @Moderator",
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
    @commands.command(
        name="uptime",
        extras={
            "category": "🔍 Server & User Info",
            "short": "How long the bot has been running",
            "usage": "uptime",
            "desc": "Shows how long NanoBot has been online since its last start or restart.",
            "args": [],
            "perms": "None",
            "example": "{prefix}uptime",
        },
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def pfx_uptime(self, ctx: commands.Context):
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
    @commands.command(
        name="stats",
        extras={
            "category": "⚙️ Config & Info",
            "short": "Runtime stats — commands ran, servers, members, uptime, CPU, memory",
            "usage": "stats",
            "desc": (
                "Shows a snapshot of NanoBot's activity since the last start: "
                "commands run, uptime, server count, member breakdown, "
                "channel counts, CPU usage, memory usage, and current latency."
            ),
            "args": [],
            "perms": "None",
            "example": "{prefix}stats",
        },
    )
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def pfx_stats(self, ctx: commands.Context):
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

        # member_count is accurate; g.members cache is sparse for large guilds
        total_members = sum(g.member_count or 0 for g in self.bot.guilds)

        # Channel counts
        text_channels = sum(len(g.text_channels) for g in self.bot.guilds)
        voice_channels = sum(len(g.voice_channels) for g in self.bot.guilds)

        latency = round(self.bot.latency * 1000)
        commands_ran = getattr(self.bot, "commands_ran", 0)

        e = h.embed(title="📊 NanoBot Stats", color=h.BLUE)
        e.set_thumbnail(url=self.bot.user.display_avatar.url)

        e.add_field(name="⚡ Commands Ran", value=f"**{commands_ran:,}**", inline=True)
        e.add_field(name="⏱️ Uptime", value=f"**{uptime_str}**", inline=True)
        e.add_field(name="📡 Latency", value=f"**{latency}ms**", inline=True)

        e.add_field(
            name="🌐 Servers", value=f"**{len(self.bot.guilds):,}**", inline=True
        )
        e.add_field(
            name="👥 Members",
            value=f"**{total_members:,}** total",
            inline=True,
        )
        e.add_field(
            name="💬 Channels",
            value=f"**{text_channels:,}** text · **{voice_channels:,}** voice",
            inline=True,
        )
        # Process-level resource usage
        cpu = self._proc.cpu_percent(interval=None)
        mem = self._proc.memory_info().rss
        if mem >= 1 << 30:
            mem_str = f"{mem / (1 << 30):.2f} GB"
        else:
            mem_str = f"{mem / (1 << 20):.1f} MB"

        e.add_field(name="🖥️ CPU", value=f"**{cpu:.1f}%**", inline=True)
        e.add_field(name="🧠 Memory", value=f"**{mem_str}**", inline=True)

        e.add_field(
            name="📦 discord.py Version",
            value=f"**{discord.__version__}**",
            inline=True,
        )
        e.add_field(
            name="🐍 Python Version",
            value=f"**{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}**",
            inline=True,
        )

        e.set_footer(text="NanoBot — stats reset on restart")
        await ctx.reply(embed=e, ephemeral=True)

    # ══════════════════════════════════════════════════════════════════════════
    #  source — show source code for a command or any named symbol
    # ══════════════════════════════════════════════════════════════════════════
    @commands.command(
        name="source",
        extras={
            "category": "⚙️ Config & Info",
            "short": "Show source for a command or symbol",
            "usage": "source <command|symbol>",
            "desc": (
                "Shows source code for a bot command — including any helper functions "
                "and variables it uses — or any named symbol in the codebase (function, "
                "class, variable). The embed title links directly to the line on GitHub."
            ),
            "args": [("command", "Command name or any symbol to look up")],
            "perms": "None",
            "example": "{prefix}source ship\n{prefix}source ban\n{prefix}source _ship_score\n{prefix}source WyrView",
        },
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def pfx_source(self, ctx: commands.Context, *, command: str):
        query = command.strip()
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # ── 1. Try as a bot command ───────────────────────────────────────────
        cmd = self.bot.get_command(query.lower())
        if cmd is None:
            for tree_cmd in self.bot.tree.get_commands():
                if tree_cmd.name == query.lower():
                    cmd = tree_cmd
                    break
                if hasattr(tree_cmd, "commands"):
                    for sub in tree_cmd.commands:
                        if sub.name == query.lower():
                            cmd = sub
                            break

        if cmd is not None:
            callback = getattr(cmd, "callback", None)
            if callback is None:
                return await ctx.reply(
                    embed=h.err("That command has no inspectable source."),
                    ephemeral=True,
                )
            try:
                lines, start_line = inspect.getsourcelines(callback)
                filepath = inspect.getfile(callback)
            except (OSError, TypeError):
                return await ctx.reply(
                    embed=h.err("Could not retrieve source for that command."),
                    ephemeral=True,
                )

            end_line = start_line + len(lines) - 1
            source_code = textwrap.dedent("".join(lines))
            github_url = _gh_url(repo_root, filepath, start_line, end_line)
            rel_path = os.path.relpath(filepath, repo_root).replace(os.sep, "/")

            e = h.embed(title=f"`{query}` — source", color=h.BLUE)
            e.url = github_url
            e.set_footer(text=f"{rel_path} · L{start_line}–L{end_line}")

            code_block = f"```python\n{source_code}```"
            if len(code_block) <= 3800:
                e.description = code_block
            else:
                e.description = f"Source too long to display inline — [view on GitHub]({github_url})"

            related = _related_callables(callback, repo_root)
            if related:
                field_lines = []
                for sym_name, sym_file, sym_start, sym_end in related:
                    sym_url = _gh_url(repo_root, sym_file, sym_start, sym_end)
                    field_lines.append(
                        f"[`{sym_name}`]({sym_url}) · L{sym_start}–L{sym_end}"
                    )
                value = "\n".join(field_lines)
                if len(value) > 1024:
                    value = value[:1021] + "…"
                e.add_field(
                    name=f"Related symbols ({len(related)})",
                    value=value,
                    inline=False,
                )

            return await ctx.reply(embed=e, ephemeral=True)

        # ── 2. General symbol search across codebase ──────────────────────────
        # Walks + ast.parses every .py file — offload so it never blocks the loop.
        matches = await asyncio.to_thread(_symbol_search, repo_root, query)

        if not matches:
            return await ctx.reply(
                embed=h.err(
                    f"No command or symbol named `{query}` found in the codebase."
                ),
                ephemeral=True,
            )

        if len(matches) == 1:
            filepath, start_line, end_line = matches[0]
            rel_path = os.path.relpath(filepath, repo_root).replace(os.sep, "/")
            github_url = _gh_url(repo_root, filepath, start_line, end_line)

            try:
                with open(filepath, encoding="utf-8") as f:
                    all_lines = f.readlines()
                source_code = textwrap.dedent(
                    "".join(all_lines[start_line - 1 : end_line])
                )
            except OSError:
                source_code = None

            e = h.embed(title=f"`{query}` — source", color=h.BLUE)
            e.url = github_url
            e.set_footer(text=f"{rel_path} · L{start_line}–L{end_line}")

            if source_code:
                code_block = f"```python\n{source_code}```"
                if len(code_block) <= 3800:
                    e.description = code_block
                else:
                    e.description = (
                        f"Source too long to display inline — "
                        f"[view on GitHub]({github_url})"
                    )
            else:
                e.description = f"[View on GitHub]({github_url})"

            return await ctx.reply(embed=e, ephemeral=True)

        # Multiple matches — list them all
        e = h.embed(
            title=f"`{query}` — {len(matches)} matches found",
            color=h.BLUE,
        )
        field_lines = []
        for filepath, start_line, end_line in matches:
            rel_path = os.path.relpath(filepath, repo_root).replace(os.sep, "/")
            url = _gh_url(repo_root, filepath, start_line, end_line)
            field_lines.append(f"[`{rel_path}`]({url}) · L{start_line}–L{end_line}")
        e.description = "\n".join(field_lines)
        await ctx.reply(embed=e, ephemeral=True)

    # ══════════════════════════════════════════════════════════════════════════
    #  firstmsg — link to oldest message in a channel
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="firstmsg",
        aliases=["first", "oldest"],
        description="Get a link to the first (oldest) message in a channel.",
        extras={
            "category": "🔍 Server & User Info",
            "short": "Link to the first message in a channel",
            "usage": "firstmsg [channel]",
            "desc": (
                "Fetches a direct jump link to the oldest message in the channel. "
                "Handy for linking to #rules or pinned announcements on mobile "
                "without having to scroll to the top."
            ),
            "args": [("channel", "Channel to look up (blank = current channel)")],
            "perms": "None",
            "example": "{prefix}firstmsg\n{prefix}firstmsg #rules",
        },
    )
    @app_commands.describe(
        channel="Channel to look up (leave blank for current channel)"
    )
    @commands.cooldown(1, 10, commands.BucketType.channel)
    async def firstmsg(
        self,
        ctx: commands.Context,
        channel: Optional[discord.TextChannel] = None,
    ):
        ch = channel or ctx.channel
        async for msg in ch.history(limit=1, oldest_first=True):
            jump = f"[Jump to first message]({msg.jump_url})"
            created = discord.utils.format_dt(msg.created_at, style="R")
            preview = (msg.content or "_[no text content]_")[:100]
            await ctx.reply(
                embed=h.info(
                    f"{jump}\n📅 Sent {created} by **{msg.author.display_name}**\n\n{preview}",
                    f"📌 #{ch.name} — First Message",
                ),
                ephemeral=True,
            )
            return
        await ctx.reply(
            embed=h.info(f"#{ch.name} appears to be empty.", "📌 First Message"),
            ephemeral=True,
        )
