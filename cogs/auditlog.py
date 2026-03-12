"""
cogs/auditlog.py
Per-server audit log — posts a feed of server events to a configurable channel.

Events logged (all toggleable per-server):
  msg_delete     — Message deleted
  msg_edit       — Message edited
  member_join    — Member joined
  member_leave   — Member left
  member_ban     — Member banned
  member_unban   — Member unbanned
  nick_change    — Nickname changed
  role_update    — Member roles added/removed
  channel_create — Channel created
  channel_delete — Channel deleted
  role_create    — Role created
  role_delete    — Role deleted

Commands (all require Manage Server):
  /auditlog channel <#channel>  — Set the log channel
  /auditlog enable              — Enable audit logging
  /auditlog disable             — Disable audit logging
  /auditlog events              — Toggle individual event types
  /auditlog status              — Show current configuration
"""

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import db
from utils import helpers as h
from utils.checks import has_admin_perms

log = logging.getLogger("NanoBot.auditlog")

# ── All supported event keys ───────────────────────────────────────────────────
ALL_EVENTS: list[str] = [
    "msg_delete",
    "msg_edit",
    "member_join",
    "member_leave",
    "member_ban",
    "member_unban",
    "nick_change",
    "role_update",
    "channel_create",
    "channel_delete",
    "role_create",
    "role_delete",
]

EVENT_LABELS: dict[str, str] = {
    "msg_delete":     "🗑️  Message Deleted",
    "msg_edit":       "✏️  Message Edited",
    "member_join":    "📥  Member Joined",
    "member_leave":   "📤  Member Left",
    "member_ban":     "🔨  Member Banned",
    "member_unban":   "🔓  Member Unbanned",
    "nick_change":    "📝  Nickname Changed",
    "role_update":    "🎭  Roles Updated",
    "channel_create": "📢  Channel Created",
    "channel_delete": "💥  Channel Deleted",
    "role_create":    "✨  Role Created",
    "role_delete":    "🗑️  Role Deleted",
}


# ── Helper: fetch log channel for a guild ─────────────────────────────────────
async def _get_log_channel(
    bot: commands.Bot,
    guild: discord.Guild,
    event_key: str,
) -> discord.TextChannel | None:
    """
    Return the audit log channel for this guild if logging is enabled
    and the given event is toggled on. Returns None otherwise.
    """
    cfg = await db.get_auditlog_config(guild.id)
    if not cfg or not cfg["enabled"]:
        return None
    if event_key not in cfg["events"]:
        return None
    if not cfg["channel_id"]:
        return None
    ch = guild.get_channel(int(cfg["channel_id"]))
    if not isinstance(ch, discord.TextChannel):
        return None
    return ch


async def _send_log(ch: discord.TextChannel, embed: discord.Embed) -> None:
    """Send an embed to the audit log channel, silently ignoring permission errors."""
    try:
        await ch.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException) as exc:
        log.debug(f"Audit log send failed in #{ch}: {exc}")


# ── Event-toggle select menu ───────────────────────────────────────────────────
class EventToggleSelect(discord.ui.Select):
    def __init__(self, enabled_events: set[str]):
        options = [
            discord.SelectOption(
                label=EVENT_LABELS[key],
                value=key,
                default=(key in enabled_events),
            )
            for key in ALL_EVENTS
        ]
        super().__init__(
            placeholder="Select events to enable…",
            min_values=0,
            max_values=len(ALL_EVENTS),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        chosen = set(self.values)
        await db.set_auditlog_events(interaction.guild_id, chosen)

        if chosen:
            lines = "\n".join(f"• {EVENT_LABELS[k]}" for k in ALL_EVENTS if k in chosen)
            desc  = f"Audit log events updated:\n\n{lines}"
        else:
            desc = "All audit log events have been **disabled**."

        await interaction.response.edit_message(
            embed=h.ok(desc, "✅ Events Updated"),
            view=None,
        )


class EventToggleView(discord.ui.View):
    def __init__(self, enabled_events: set[str], author: discord.Member):
        super().__init__(timeout=60)
        self.author = author
        self.add_item(EventToggleSelect(enabled_events))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.author:
            await interaction.response.send_message(
                "This menu isn't for you.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True  # type: ignore[attr-defined]


# ══════════════════════════════════════════════════════════════════════════════
class AuditLog(commands.Cog):
    """Audit log — passive event feed to a dedicated channel."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /auditlog group ────────────────────────────────────────────────────────
    auditlog_group = app_commands.Group(
        name="auditlog",
        description="Configure the server audit log.",
        default_permissions=discord.Permissions(manage_guild=True),
        guild_only=True,
    )

    @auditlog_group.command(name="channel", description="Set the channel for audit log entries.")
    @app_commands.describe(channel="The text channel to post audit logs in.")
    @has_admin_perms()
    async def al_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
    ):
        await db.set_auditlog_channel(interaction.guild_id, channel.id)
        await interaction.response.send_message(
            embed=h.ok(
                f"Audit log channel set to {channel.mention}.\n"
                f"Use `/auditlog enable` to start logging.",
                "📋 Audit Log Channel",
            ),
            ephemeral=True,
        )

    @auditlog_group.command(name="enable", description="Enable the audit log.")
    @has_admin_perms()
    async def al_enable(self, interaction: discord.Interaction):
        cfg = await db.get_auditlog_config(interaction.guild_id)
        if not cfg or not cfg["channel_id"]:
            await interaction.response.send_message(
                embed=h.err(
                    "Set a channel first with `/auditlog channel #channel`.",
                    "❌ No Channel Set",
                ),
                ephemeral=True,
            )
            return
        await db.set_auditlog_enabled(interaction.guild_id, True)
        ch = interaction.guild.get_channel(int(cfg["channel_id"]))
        mention = ch.mention if ch else f"`{cfg['channel_id']}`"
        await interaction.response.send_message(
            embed=h.ok(f"Audit log **enabled** → {mention}", "✅ Audit Log On"),
            ephemeral=True,
        )

    @auditlog_group.command(name="disable", description="Disable the audit log.")
    @has_admin_perms()
    async def al_disable(self, interaction: discord.Interaction):
        await db.set_auditlog_enabled(interaction.guild_id, False)
        await interaction.response.send_message(
            embed=h.ok("Audit log **disabled**. No events will be logged.", "🔕 Audit Log Off"),
            ephemeral=True,
        )

    @auditlog_group.command(name="events", description="Toggle which events get logged.")
    @has_admin_perms()
    async def al_events(self, interaction: discord.Interaction):
        cfg = await db.get_auditlog_config(interaction.guild_id)
        current_events = set(cfg["events"]) if cfg else set(ALL_EVENTS)

        view = EventToggleView(current_events, interaction.user)
        await interaction.response.send_message(
            embed=h.info(
                "Select which events to log. Deselect any you want to silence.",
                "🎛️ Audit Log Events",
            ),
            view=view,
            ephemeral=True,
        )

    @auditlog_group.command(name="status", description="Show the current audit log configuration.")
    @has_admin_perms()
    async def al_status(self, interaction: discord.Interaction):
        cfg = await db.get_auditlog_config(interaction.guild_id)

        if not cfg or not cfg["channel_id"]:
            await interaction.response.send_message(
                embed=h.info(
                    "Audit log is **not configured**.\n"
                    "Use `/auditlog channel #channel` to get started.",
                    "📋 Audit Log Status",
                ),
                ephemeral=True,
            )
            return

        ch = interaction.guild.get_channel(int(cfg["channel_id"]))
        ch_mention = ch.mention if ch else f"⚠️ Unknown (`{cfg['channel_id']}`)"
        status     = "🟢 Enabled" if cfg["enabled"] else "🔴 Disabled"
        events     = set(cfg["events"])
        on_lines   = "\n".join(f"✅ {EVENT_LABELS[k]}" for k in ALL_EVENTS if k in events)
        off_lines  = "\n".join(f"❌ {EVENT_LABELS[k]}" for k in ALL_EVENTS if k not in events)
        body_parts = [
            f"**Channel:** {ch_mention}",
            f"**Status:** {status}",
            "",
            "**Logged Events:**",
            on_lines or "_None_",
        ]
        if off_lines:
            body_parts += ["", "**Silenced Events:**", off_lines]

        e = h.embed("📋 Audit Log Status", "\n".join(body_parts), h.BLUE)
        await interaction.response.send_message(embed=e, ephemeral=True)

    # ── Discord Events ─────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        ch = await _get_log_channel(self.bot, message.guild, "msg_delete")
        if not ch:
            return

        e = discord.Embed(
            title="🗑️ Message Deleted",
            color=h.RED,
        )
        e.add_field(name="Author",   value=f"{message.author.mention} (`{message.author.id}`)", inline=True)
        e.add_field(name="Channel",  value=message.channel.mention,                             inline=True)
        if message.content:
            preview = message.content[:1000] + ("…" if len(message.content) > 1000 else "")
            e.add_field(name="Content", value=preview, inline=False)
        if message.attachments:
            e.add_field(
                name="Attachments",
                value="\n".join(a.filename for a in message.attachments),
                inline=False,
            )
        e.set_footer(text=f"NanoBot Audit Log  •  User ID: {message.author.id}")
        e.timestamp = discord.utils.utcnow()
        await _send_log(ch, e)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if not before.guild or before.author.bot:
            return
        if before.content == after.content:
            return   # embed-only updates, pin notifications, etc.
        ch = await _get_log_channel(self.bot, before.guild, "msg_edit")
        if not ch:
            return

        e = discord.Embed(
            title="✏️ Message Edited",
            color=h.YELLOW,
            url=after.jump_url,
        )
        e.add_field(name="Author",  value=f"{before.author.mention} (`{before.author.id}`)", inline=True)
        e.add_field(name="Channel", value=before.channel.mention,                             inline=True)
        b_prev = before.content[:500] + ("…" if len(before.content) > 500 else "")
        a_prev = after.content[:500]  + ("…" if len(after.content)  > 500 else "")
        e.add_field(name="Before", value=b_prev or "_empty_", inline=False)
        e.add_field(name="After",  value=a_prev or "_empty_", inline=False)
        e.set_footer(text=f"NanoBot Audit Log  •  User ID: {before.author.id}")
        e.timestamp = discord.utils.utcnow()
        await _send_log(ch, e)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        ch = await _get_log_channel(self.bot, member.guild, "member_join")
        if not ch:
            return

        age = discord.utils.utcnow() - member.created_at
        age_str = f"{age.days}d old"

        e = discord.Embed(
            title="📥 Member Joined",
            description=f"{member.mention} **{member}**",
            color=h.GREEN,
        )
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(name="Account Age", value=age_str,             inline=True)
        e.add_field(name="Member #",    value=str(member.guild.member_count), inline=True)
        e.set_footer(text=f"NanoBot Audit Log  •  ID: {member.id}")
        e.timestamp = discord.utils.utcnow()
        await _send_log(ch, e)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return
        ch = await _get_log_channel(self.bot, member.guild, "member_leave")
        if not ch:
            return

        roles = [r.mention for r in member.roles if r != member.guild.default_role]
        e = discord.Embed(
            title="📤 Member Left",
            description=f"**{member}** (`{member.id}`)",
            color=h.GREY,
        )
        e.set_thumbnail(url=member.display_avatar.url)
        if roles:
            e.add_field(name="Roles", value=" ".join(roles)[:1000], inline=False)
        e.set_footer(text=f"NanoBot Audit Log  •  ID: {member.id}")
        e.timestamp = discord.utils.utcnow()
        await _send_log(ch, e)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        ch = await _get_log_channel(self.bot, guild, "member_ban")
        if not ch:
            return

        e = discord.Embed(
            title="🔨 Member Banned",
            description=f"**{user}** (`{user.id}`)",
            color=h.RED,
        )
        e.set_thumbnail(url=user.display_avatar.url)

        # Try to fetch audit log reason
        try:
            async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban):
                if entry.target and entry.target.id == user.id:
                    if entry.user:
                        e.add_field(name="Banned By", value=f"{entry.user.mention} (`{entry.user.id}`)", inline=True)
                    if entry.reason:
                        e.add_field(name="Reason", value=entry.reason[:512], inline=False)
                    break
        except discord.Forbidden:
            pass

        e.set_footer(text=f"NanoBot Audit Log  •  ID: {user.id}")
        e.timestamp = discord.utils.utcnow()
        await _send_log(ch, e)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        ch = await _get_log_channel(self.bot, guild, "member_unban")
        if not ch:
            return

        e = discord.Embed(
            title="🔓 Member Unbanned",
            description=f"**{user}** (`{user.id}`)",
            color=h.GREEN,
        )
        e.set_thumbnail(url=user.display_avatar.url)
        e.set_footer(text=f"NanoBot Audit Log  •  ID: {user.id}")
        e.timestamp = discord.utils.utcnow()
        await _send_log(ch, e)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if before.bot:
            return

        # ── Nickname change ────────────────────────────────────────────────────
        if before.nick != after.nick:
            ch = await _get_log_channel(self.bot, before.guild, "nick_change")
            if ch:
                e = discord.Embed(title="📝 Nickname Changed", color=h.BLUE)
                e.add_field(name="Member", value=f"{after.mention} (`{after.id}`)", inline=False)
                e.add_field(name="Before", value=before.nick or before.name, inline=True)
                e.add_field(name="After",  value=after.nick  or after.name,  inline=True)
                e.set_footer(text=f"NanoBot Audit Log  •  ID: {after.id}")
                e.timestamp = discord.utils.utcnow()
                await _send_log(ch, e)

        # ── Role changes ───────────────────────────────────────────────────────
        before_roles = set(before.roles)
        after_roles  = set(after.roles)
        added   = after_roles  - before_roles
        removed = before_roles - after_roles
        if (added or removed):
            ch = await _get_log_channel(self.bot, before.guild, "role_update")
            if ch:
                e = discord.Embed(title="🎭 Roles Updated", color=h.BLUE)
                e.add_field(name="Member", value=f"{after.mention} (`{after.id}`)", inline=False)
                if added:
                    e.add_field(name="➕ Added",   value=" ".join(r.mention for r in added),   inline=True)
                if removed:
                    e.add_field(name="➖ Removed", value=" ".join(r.mention for r in removed), inline=True)
                e.set_footer(text=f"NanoBot Audit Log  •  ID: {after.id}")
                e.timestamp = discord.utils.utcnow()
                await _send_log(ch, e)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        ch = await _get_log_channel(self.bot, channel.guild, "channel_create")
        if not ch:
            return

        e = discord.Embed(
            title="📢 Channel Created",
            description=f"**#{channel.name}** (`{channel.id}`)",
            color=h.GREEN,
        )
        e.add_field(name="Type",     value=str(channel.type).replace("_", " ").title(), inline=True)
        if hasattr(channel, "category") and channel.category:
            e.add_field(name="Category", value=channel.category.name, inline=True)
        e.set_footer(text="NanoBot Audit Log")
        e.timestamp = discord.utils.utcnow()
        await _send_log(ch, e)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        ch = await _get_log_channel(self.bot, channel.guild, "channel_delete")
        if not ch:
            return

        e = discord.Embed(
            title="💥 Channel Deleted",
            description=f"**#{channel.name}** (`{channel.id}`)",
            color=h.RED,
        )
        e.add_field(name="Type", value=str(channel.type).replace("_", " ").title(), inline=True)
        if hasattr(channel, "category") and channel.category:
            e.add_field(name="Category", value=channel.category.name, inline=True)
        e.set_footer(text="NanoBot Audit Log")
        e.timestamp = discord.utils.utcnow()
        await _send_log(ch, e)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        ch = await _get_log_channel(self.bot, role.guild, "role_create")
        if not ch:
            return

        e = discord.Embed(
            title="✨ Role Created",
            description=f"**{role.name}** (`{role.id}`)",
            color=role.color if role.color.value else h.GREEN,
        )
        e.add_field(name="Mentionable", value="Yes" if role.mentionable else "No", inline=True)
        e.add_field(name="Hoisted",     value="Yes" if role.hoist       else "No", inline=True)
        e.set_footer(text="NanoBot Audit Log")
        e.timestamp = discord.utils.utcnow()
        await _send_log(ch, e)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        ch = await _get_log_channel(self.bot, role.guild, "role_delete")
        if not ch:
            return

        e = discord.Embed(
            title="🗑️ Role Deleted",
            description=f"**{role.name}** (`{role.id}`)",
            color=h.RED,
        )
        e.set_footer(text="NanoBot Audit Log")
        e.timestamp = discord.utils.utcnow()
        await _send_log(ch, e)


# ── Setup ──────────────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(AuditLog(bot))
