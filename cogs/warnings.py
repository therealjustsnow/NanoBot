"""
cogs/warnings.py
Per-server warning system with configurable auto-actions.

Slash: /warn issue, /warn list, /warn clear, /warn config  (1 top-level slot via app_commands.Group)
Prefix: !warn, !warnings, !clearwarnings, !warnconfig       (flat, unchanged)
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import db
from utils import helpers as h
from utils.checks import has_mod_perms, has_admin_perms

log = logging.getLogger("NanoBot.warnings")


# ══════════════════════════════════════════════════════════════════════════════
class Warnings(commands.Cog):
    """Per-server warning system."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── shared logic ──────────────────────────────────────────────────────────

    async def _do_warn(self, guild, channel, author, user, reason):
        """Core warn logic. Returns (embed, public_embed)."""
        now = datetime.now(timezone.utc)
        count = await db.add_warning(
            guild.id,
            user.id,
            reason,
            str(author.id),
            str(author),
            now.isoformat(),
        )

        cfg = await db.get_warn_config(guild.id)

        dm_sent = False
        if cfg["dm_user"]:
            try:
                dm_e = discord.Embed(
                    title=f"\u26a0\ufe0f Warning #{count} \u2014 {guild.name}",
                    description=f"**Reason:** {reason}",
                    color=h.YELLOW,
                )
                dm_e.set_footer(text=f"Issued by {author.display_name}")
                dm_e.timestamp = now
                await user.send(embed=dm_e)
                dm_sent = True
            except discord.Forbidden:
                pass

        log.info(
            f"Warned {user} ({user.id}) in {guild} ({guild.id}) "
            f"by {author} \u2014 warning #{count}: {reason}"
        )

        lines = [
            f"**{user.display_name}** has been warned. That's warning **#{count}**.",
            f"\U0001f4dd {reason}",
            f"\U0001f4e8 DM {'sent' if dm_sent else 'failed (closed DMs)'}.",
        ]

        action_taken = None

        if cfg["ban_at"] and count >= cfg["ban_at"]:
            try:
                await guild.ban(
                    user,
                    reason=f"NanoBot auto-ban: reached {count} warnings",
                    delete_message_days=0,
                )
                action_taken = f"\U0001f528 Auto-banned (reached {count} warnings \u2014 threshold: {cfg['ban_at']})"
                log.warning(
                    f"Auto-banned {user} ({user.id}) in {guild} \u2014 {count} warnings"
                )
            except discord.Forbidden:
                action_taken = "\u26a0\ufe0f Auto-ban threshold reached but I lack Ban Members permission."

        elif cfg["kick_at"] and count >= cfg["kick_at"]:
            try:
                await guild.kick(
                    user, reason=f"NanoBot auto-kick: reached {count} warnings"
                )
                action_taken = f"\U0001f462 Auto-kicked (reached {count} warnings \u2014 threshold: {cfg['kick_at']})"
                log.warning(
                    f"Auto-kicked {user} ({user.id}) in {guild} \u2014 {count} warnings"
                )
            except discord.Forbidden:
                action_taken = "\u26a0\ufe0f Auto-kick threshold reached but I lack Kick Members permission."

        if action_taken:
            lines.append(action_taken)

        reply_embed = h.warn("\n".join(lines), f"\u26a0\ufe0f Warning #{count}")

        public_embed = discord.Embed(
            description=(
                f"\u26a0\ufe0f **{author.display_name}** warned **{user.display_name}** (`{user.id}`)\n"
                f"\U0001f4dd {reason}\n"
                f"Total warnings: **{count}**"
                + (f"\n{action_taken}" if action_taken else "")
            ),
            color=h.YELLOW,
        )
        public_embed.set_footer(text="NanoBot")
        public_embed.timestamp = now

        return reply_embed, public_embed

    async def _do_list(self, guild, user):
        """Core list logic. Returns embed."""
        warns = await db.get_warnings(guild.id, user.id)

        if not warns:
            return h.info(
                f"**{user.display_name}** has no warnings on this server.",
                "\u26a0\ufe0f Warnings",
            )

        cfg = await db.get_warn_config(guild.id)
        e = h.embed(
            title=f"\u26a0\ufe0f Warnings \u2014 {user.display_name}", color=h.YELLOW
        )
        e.set_thumbnail(url=user.display_avatar.url)

        shown = warns[-5:]
        lines = []
        for w in shown:
            date = w["at"][:10]
            reason = w["reason"][:120] + ("\u2026" if len(w["reason"]) > 120 else "")
            lines.append(
                f"**#{w['id']}** \u00b7 {date} \u00b7 {w['by_name']}\n{reason}"
            )
        e.description = "\n\n".join(lines)

        count_label = (
            f"Showing last 5 of {len(warns)}" if len(warns) > 5 else str(len(warns))
        )
        footer_parts = [f"{count_label} warning(s)"]
        if cfg["kick_at"]:
            footer_parts.append(f"\U0001f462 Auto-kick at {cfg['kick_at']}")
        if cfg["ban_at"]:
            footer_parts.append(f"\U0001f528 Auto-ban at {cfg['ban_at']}")
        footer_parts.append("NanoBot")
        e.set_footer(text="  \u00b7  ".join(footer_parts))
        return e

    async def _do_clear(self, guild, user, author):
        """Core clear logic. Returns embed."""
        count = await db.clear_warnings(guild.id, user.id)
        if count:
            log.info(
                f"Cleared {count} warning(s) for {user} ({user.id}) in {guild} by {author}"
            )
            return h.ok(
                f"Cleared **{count}** warning(s) for **{user.display_name}**.",
                "\u2705 Warnings Cleared",
            )
        return h.info(
            f"**{user.display_name}** has no warnings to clear.",
            "\u26a0\ufe0f Warnings",
        )

    async def _do_config(self, guild, kick_at, ban_at, dm_user):
        """Core config logic. Returns embed."""
        if kick_at is None and ban_at is None and dm_user is None:
            cfg = await db.get_warn_config(guild.id)
            e = h.embed(title="\u2699\ufe0f Warning Config", color=h.BLUE)
            e.add_field(
                name="\U0001f462 Auto-Kick",
                value=(
                    str(cfg["kick_at"]) + " warnings" if cfg["kick_at"] else "Disabled"
                ),
                inline=True,
            )
            e.add_field(
                name="\U0001f528 Auto-Ban",
                value=str(cfg["ban_at"]) + " warnings" if cfg["ban_at"] else "Disabled",
                inline=True,
            )
            e.add_field(
                name="\U0001f4e8 DM Users",
                value="Yes" if cfg["dm_user"] else "No",
                inline=True,
            )
            e.set_footer(
                text="Use /warn config kick_at:3 ban_at:5 to configure  \u00b7  NanoBot"
            )
            return e

        cfg = await db.get_warn_config(guild.id)
        new_kick = kick_at if kick_at is not None else cfg["kick_at"]
        new_ban = ban_at if ban_at is not None else cfg["ban_at"]
        new_dm = dm_user if dm_user is not None else cfg["dm_user"]

        if new_kick and new_ban and new_kick >= new_ban:
            return h.err("Auto-kick threshold must be lower than auto-ban threshold.")

        await db.set_warn_config(guild.id, new_kick, new_ban, new_dm)
        log.info(
            f"warnconfig updated in {guild}: kick_at={new_kick} ban_at={new_ban} dm={new_dm}"
        )

        lines = [
            (
                f"\U0001f462 Auto-kick at **{new_kick}** warnings"
                if new_kick
                else "\U0001f462 Auto-kick **disabled**"
            ),
            (
                f"\U0001f528 Auto-ban at **{new_ban}** warnings"
                if new_ban
                else "\U0001f528 Auto-ban **disabled**"
            ),
            f"\U0001f4e8 DM on warn: **{'Yes' if new_dm else 'No'}**",
        ]
        return h.ok("\n".join(lines), "\u2699\ufe0f Warning Config Updated")

    # ══════════════════════════════════════════════════════════════════════════
    #  SLASH: /warn group (app_commands.Group -- 1 top-level slot)
    # ══════════════════════════════════════════════════════════════════════════

    warn_slash = app_commands.Group(
        name="warn",
        description="Warning system commands.",
        default_permissions=discord.Permissions(manage_messages=True),
        guild_only=True,
    )

    @warn_slash.command(name="issue", description="Issue a warning to a user.")
    @app_commands.describe(user="User to warn", reason="Reason for the warning")
    async def slash_warn_issue(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str = "No reason provided.",
    ):
        if user == interaction.user:
            return await interaction.response.send_message(
                embed=h.err("You can't warn yourself."), ephemeral=True
            )
        if user.bot:
            return await interaction.response.send_message(
                embed=h.err("You can't warn bots."), ephemeral=True
            )
        reply_e, public_e = await self._do_warn(
            interaction.guild, interaction.channel, interaction.user, user, reason
        )
        await interaction.response.send_message(embed=reply_e, ephemeral=True)
        try:
            await interaction.channel.send(embed=public_e)
        except discord.HTTPException:
            pass

    @warn_slash.command(name="list", description="View all warnings for a user.")
    @app_commands.describe(user="User to look up")
    async def slash_warn_list(
        self, interaction: discord.Interaction, user: discord.Member
    ):
        e = await self._do_list(interaction.guild, user)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @warn_slash.command(
        name="clear", description="Clear all warnings for a user. Admin only."
    )
    @app_commands.describe(user="User whose warnings to clear")
    @app_commands.default_permissions(administrator=True)
    async def slash_warn_clear(
        self, interaction: discord.Interaction, user: discord.Member
    ):
        e = await self._do_clear(interaction.guild, user, interaction.user)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @warn_slash.command(
        name="config", description="Configure auto-actions for warnings."
    )
    @app_commands.describe(
        kick_at="Auto-kick after this many warnings (0 = disabled)",
        ban_at="Auto-ban after this many warnings (0 = disabled)",
        dm_user="DM users when they are warned",
    )
    async def slash_warn_config(
        self,
        interaction: discord.Interaction,
        kick_at: Optional[int] = None,
        ban_at: Optional[int] = None,
        dm_user: Optional[bool] = None,
    ):
        e = await self._do_config(interaction.guild, kick_at, ban_at, dm_user)
        await interaction.response.send_message(embed=e, ephemeral=True)

    # ══════════════════════════════════════════════════════════════════════════
    #  PREFIX: !warn, !warnings, !clearwarnings, !warnconfig
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command(
        name="warn",
        extras={
            "category": "\U0001f6e1\ufe0f Moderation",
            "short": "Warn a user",
            "usage": "warn <user> [reason]",
            "desc": "Issue a warning to a user. Configurable auto-kick/ban thresholds apply.",
            "args": [("user", "User to warn"), ("reason", "Reason (optional)")],
            "perms": "Manage Messages",
            "example": "!warn @Troublemaker Spamming in general",
        },
    )
    @has_mod_perms()
    async def pfx_warn(
        self,
        ctx: commands.Context,
        user: discord.Member,
        *,
        reason: str = "No reason provided.",
    ):
        if user == ctx.author:
            return await ctx.reply(
                embed=h.err("You can't warn yourself."), ephemeral=True
            )
        if user.bot:
            return await ctx.reply(embed=h.err("You can't warn bots."), ephemeral=True)
        reply_e, public_e = await self._do_warn(
            ctx.guild, ctx.channel, ctx.author, user, reason
        )
        await ctx.reply(embed=reply_e, ephemeral=True)
        try:
            await ctx.channel.send(embed=public_e)
        except discord.HTTPException:
            pass

    @commands.command(
        name="warnings",
        extras={
            "category": "\U0001f6e1\ufe0f Moderation",
            "short": "View warnings for a user",
            "usage": "warnings <user>",
            "desc": "View all warnings for a user on this server.",
            "args": [("user", "User to look up")],
            "perms": "Manage Messages",
            "example": "!warnings @Troublemaker",
        },
    )
    @has_mod_perms()
    async def pfx_warnings(self, ctx: commands.Context, user: discord.Member):
        e = await self._do_list(ctx.guild, user)
        await ctx.reply(embed=e, ephemeral=True)

    @commands.command(
        name="clearwarnings",
        extras={
            "category": "\U0001f6e1\ufe0f Moderation",
            "short": "Clear all warnings for a user",
            "usage": "clearwarnings <user>",
            "desc": "Wipe all warnings for a user. Admin only.",
            "args": [("user", "User whose warnings to clear")],
            "perms": "Administrator",
            "example": "!clearwarnings @Reformed",
        },
    )
    @has_admin_perms()
    async def pfx_clearwarnings(self, ctx: commands.Context, user: discord.Member):
        e = await self._do_clear(ctx.guild, user, ctx.author)
        await ctx.reply(embed=e, ephemeral=True)

    @commands.command(
        name="warnconfig",
        extras={
            "category": "\U0001f6e1\ufe0f Moderation",
            "short": "Configure warning auto-actions",
            "usage": "warnconfig [kick_at] [ban_at] [dm_user]",
            "desc": "Configure auto-kick/ban thresholds and DM behavior.",
            "args": [
                ("kick_at", "Auto-kick after N warnings (0=off)"),
                ("ban_at", "Auto-ban after N warnings (0=off)"),
                ("dm_user", "DM users on warn (true/false)"),
            ],
            "perms": "Administrator",
            "example": "!warnconfig 3 5 true\n!warnconfig",
        },
    )
    @has_admin_perms()
    async def pfx_warnconfig(
        self,
        ctx: commands.Context,
        kick_at: Optional[int] = None,
        ban_at: Optional[int] = None,
        dm_user: Optional[bool] = None,
    ):
        e = await self._do_config(ctx.guild, kick_at, ban_at, dm_user)
        await ctx.reply(embed=e, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Warnings(bot))
