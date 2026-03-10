"""
cogs/welcome.py
Per-server welcome and leave messages.

Supports channel messages and DMs, custom embed titles, content,
and image URLs. Variables in content: {user}, {mention}, {server}, {count}.

Commands:
  welcome        — configure or view welcome settings
  leave          — configure or view leave settings
  testwelcome    — preview the welcome message
  testleave      — preview the leave message
"""

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import db
from utils import helpers as h
from utils.checks import has_admin_perms

log = logging.getLogger("NanoBot.welcome")

_VARS_HELP = (
    "`{user}` — display name  ·  `{mention}` — ping  ·  "
    "`{server}` — server name  ·  `{count}` — member count"
)


def _fill(template: str, member: discord.Member) -> str:
    return (
        template
        .replace("{user}",    member.display_name)
        .replace("{mention}", member.mention)
        .replace("{server}",  member.guild.name)
        .replace("{count}",   str(member.guild.member_count or "?"))
        .replace("{username}", str(member))
    )


async def _send_event(
    bot:    commands.Bot,
    member: discord.Member,
    cfg:    dict,
    event:  str,         # "welcome" or "leave"
):
    """Build and deliver a welcome or leave embed."""
    title   = _fill(cfg["title"]   or ("👋 Welcome!" if event == "welcome" else "👋 Goodbye!"),  member)
    content = _fill(cfg["content"] or (
        f"Welcome to **{member.guild.name}**, {member.mention}! We're glad to have you." if event == "welcome"
        else f"**{member.display_name}** has left the server. Farewell!"
    ), member)

    e = discord.Embed(title=title, description=content, color=h.BLUE)
    e.set_thumbnail(url=member.display_avatar.url)
    if cfg.get("image_url"):
        e.set_image(url=cfg["image_url"])
    e.set_footer(text=member.guild.name)
    e.timestamp = discord.utils.utcnow()

    # DM delivery
    if cfg["dm"]:
        try:
            await member.send(embed=e)
            log.info(f"{event} DM sent to {member} ({member.id}) in {member.guild}")
            return
        except discord.Forbidden:
            log.debug(f"{event} DM failed for {member} ({member.id}) — closed DMs")

    # Channel delivery
    channel = None
    if cfg.get("channel_id"):
        channel = member.guild.get_channel(int(cfg["channel_id"]))
    if not channel:
        # Fall back to system channel
        channel = member.guild.system_channel

    if channel:
        try:
            await channel.send(embed=e)
            log.info(f"{event} message sent to #{channel} in {member.guild}")
        except discord.Forbidden:
            log.warning(f"Can't send {event} message to #{channel} in {member.guild}")


# ══════════════════════════════════════════════════════════════════════════════
class Welcome(commands.Cog):
    """Welcome and leave message configuration."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Events ─────────────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        cfg = await db.get_welcome_config(member.guild.id)
        if cfg and cfg["enabled"]:
            await _send_event(self.bot, member, cfg, "welcome")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return
        cfg = await db.get_leave_config(member.guild.id)
        if cfg and cfg["enabled"]:
            await _send_event(self.bot, member, cfg, "leave")

    # ══════════════════════════════════════════════════════════════════════════
    #  /welcome group
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_group(
        name="welcome",
        description="Configure or view welcome message settings.",
        invoke_without_command=True,
    )
    async def welcome(self, ctx: commands.Context):
        await self._show_config(ctx, "welcome")

    @welcome.command(name="set", description="Configure the welcome message.")
    @app_commands.describe(
        enabled   = "Enable or disable welcome messages",
        channel   = "Channel to post in (leave blank for DM or system channel)",
        title     = "Embed title (supports {user}, {server})",
        content   = "Message body (supports {user}, {mention}, {server}, {count})",
        image_url = "Image URL to show in the embed (https://...)",
        dm        = "DM the joining user instead of posting in a channel",
    )
    @has_admin_perms()
    async def welcome_set(
        self,
        ctx:       commands.Context,
        enabled:   Optional[bool]               = None,
        channel:   Optional[discord.TextChannel] = None,
        title:     Optional[str]                = None,
        content:   Optional[str]                = None,
        image_url: Optional[str]                = None,
        dm:        Optional[bool]               = None,
    ):
        await self._do_set(ctx, "welcome", enabled, channel, title, content, image_url, dm)

    @welcome.command(name="test", description="Preview the welcome message as if you just joined.")
    @has_admin_perms()
    async def welcome_test(self, ctx: commands.Context):
        cfg = await db.get_welcome_config(ctx.guild.id)
        if not cfg or not cfg.get("enabled"):
            return await ctx.reply(
                embed=h.warn("Welcome messages are not enabled. Use `/welcome set enabled:True` first."),
                ephemeral=True,
            )
        await ctx.reply(embed=h.info("Sending test welcome message...", "🧪 Test"), ephemeral=True)
        await _send_event(self.bot, ctx.author, cfg, "welcome")  # type: ignore

    # ══════════════════════════════════════════════════════════════════════════
    #  /leave group
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_group(
        name="leave",
        description="Configure or view leave message settings.",
        invoke_without_command=True,
    )
    async def leave(self, ctx: commands.Context):
        await self._show_config(ctx, "leave")

    @leave.command(name="set", description="Configure the leave message.")
    @app_commands.describe(
        enabled   = "Enable or disable leave messages",
        channel   = "Channel to post in (leave blank for system channel)",
        title     = "Embed title (supports {user}, {server})",
        content   = "Message body (supports {user}, {mention}, {server}, {count})",
        image_url = "Image URL to show in the embed (https://...)",
        dm        = "DM the leaving user instead of posting in a channel",
    )
    @has_admin_perms()
    async def leave_set(
        self,
        ctx:       commands.Context,
        enabled:   Optional[bool]               = None,
        channel:   Optional[discord.TextChannel] = None,
        title:     Optional[str]                = None,
        content:   Optional[str]                = None,
        image_url: Optional[str]                = None,
        dm:        Optional[bool]               = None,
    ):
        await self._do_set(ctx, "leave", enabled, channel, title, content, image_url, dm)

    @leave.command(name="test", description="Preview the leave message as if you just left.")
    @has_admin_perms()
    async def leave_test(self, ctx: commands.Context):
        cfg = await db.get_leave_config(ctx.guild.id)
        if not cfg or not cfg.get("enabled"):
            return await ctx.reply(
                embed=h.warn("Leave messages are not enabled. Use `/leave set enabled:True` first."),
                ephemeral=True,
            )
        await ctx.reply(embed=h.info("Sending test leave message...", "🧪 Test"), ephemeral=True)
        await _send_event(self.bot, ctx.author, cfg, "leave")  # type: ignore

    # ── Shared implementation ──────────────────────────────────────────────────
    async def _show_config(self, ctx: commands.Context, event: str):
        getter = db.get_welcome_config if event == "welcome" else db.get_leave_config
        cfg    = await getter(ctx.guild.id)
        emoji  = "👋" if event == "welcome" else "🚪"

        e = h.embed(title=f"{emoji} {event.title()} Config", color=h.BLUE)

        if not cfg:
            e.description = f"No {event} config set. Use `/{event} set` to configure."
        else:
            ch = ctx.guild.get_channel(int(cfg["channel_id"])) if cfg.get("channel_id") else None
            e.add_field(name="✅ Enabled",   value="Yes" if cfg["enabled"] else "No",         inline=True)
            e.add_field(name="📢 Channel",   value=ch.mention if ch else "_System channel_",   inline=True)
            e.add_field(name="📨 DM Mode",   value="Yes" if cfg["dm"] else "No",               inline=True)
            e.add_field(name="📝 Title",     value=cfg.get("title")   or "_Default_",           inline=False)
            e.add_field(name="💬 Content",   value=(cfg.get("content") or "_Default_")[:500],   inline=False)
            if cfg.get("image_url"):
                e.add_field(name="🖼️ Image", value=cfg["image_url"],                           inline=False)

        e.set_footer(text=f"{_VARS_HELP}  ·  NanoBot")
        await ctx.reply(embed=e, ephemeral=True)

    async def _do_set(
        self,
        ctx:       commands.Context,
        event:     str,
        enabled:   Optional[bool],
        channel:   Optional[discord.TextChannel],
        title:     Optional[str],
        content:   Optional[str],
        image_url: Optional[str],
        dm:        Optional[bool],
    ):
        getter = db.get_welcome_config if event == "welcome" else db.get_leave_config
        setter = db.set_welcome_config if event == "welcome" else db.set_leave_config

        existing = await getter(ctx.guild.id) or {}

        new_cfg = {
            "enabled":    enabled    if enabled    is not None else existing.get("enabled", False),
            "channel_id": str(channel.id) if channel else existing.get("channel_id"),
            "title":      title     if title     is not None else existing.get("title"),
            "content":    content   if content   is not None else existing.get("content"),
            "image_url":  image_url if image_url is not None else existing.get("image_url"),
            "dm":         dm        if dm        is not None else existing.get("dm", False),
        }

        if image_url and not image_url.startswith("https://"):
            return await ctx.reply(embed=h.err("Image URL must start with `https://`."), ephemeral=True)

        await setter(ctx.guild.id, **new_cfg)
        log.info(f"{event} config updated in {ctx.guild} ({ctx.guild.id}) by {ctx.author}")

        ch  = ctx.guild.get_channel(int(new_cfg["channel_id"])) if new_cfg.get("channel_id") else None
        lines = [
            f"✅ Enabled: **{'Yes' if new_cfg['enabled'] else 'No'}**",
            f"📢 Channel: {ch.mention if ch else '_System channel_'}",
            f"📨 DM Mode: **{'Yes' if new_cfg['dm'] else 'No'}**",
        ]
        if new_cfg.get("title"):
            lines.append(f"📝 Title: {new_cfg['title'][:100]}")

        emoji = "👋" if event == "welcome" else "🚪"
        await ctx.reply(
            embed=h.ok(
                "\n".join(lines) + f"\n\nTest it with `/{event} test`.",
                f"{emoji} {event.title()} Config Updated",
            ),
            ephemeral=True,
        )


# ── Registration ───────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))
