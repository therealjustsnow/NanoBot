"""
cogs/warnings.py
Per-server warning system with configurable auto-actions.

Commands:
  warn          — warn a user with a reason
  warnings      — view all warnings for a user
  clearwarnings — wipe all warnings for a user (admin)
  warnconfig    — configure auto-kick/ban thresholds (admin)
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

    # ══════════════════════════════════════════════════════════════════════════
    #  /warn
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="warn",
        description="Issue a warning to a user. Configurable auto-actions apply.",
    )
    @app_commands.describe(
        user   = "User to warn",
        reason = "Reason for the warning",
    )
    @has_mod_perms()
    async def warn(
        self,
        ctx:    commands.Context,
        user:   discord.Member,
        *,
        reason: str = "No reason provided.",
    ):
        if user == ctx.author:
            return await ctx.reply(embed=h.err("You can't warn yourself."), ephemeral=True)
        if user.bot:
            return await ctx.reply(embed=h.err("You can't warn bots."), ephemeral=True)

        now   = datetime.now(timezone.utc)
        count = await db.add_warning(
            ctx.guild.id, user.id, reason,
            str(ctx.author.id), str(ctx.author),
            now.isoformat(),
        )

        cfg = await db.get_warn_config(ctx.guild.id)

        # DM the warned user if configured
        dm_sent = False
        if cfg["dm_user"]:
            try:
                dm_e = discord.Embed(
                    title       = f"⚠️ Warning #{count} — {ctx.guild.name}",
                    description = f"**Reason:** {reason}",
                    color       = h.YELLOW,
                )
                dm_e.set_footer(text=f"Issued by {ctx.author.display_name}")
                dm_e.timestamp = now
                await user.send(embed=dm_e)
                dm_sent = True
            except discord.Forbidden:
                pass

        log.info(
            f"Warned {user} ({user.id}) in {ctx.guild} ({ctx.guild.id}) "
            f"by {ctx.author} — warning #{count}: {reason}"
        )

        lines = [
            f"**{user.display_name}** has been warned. That's warning **#{count}**.",
            f"📝 {reason}",
            f"📨 DM {'sent' if dm_sent else 'failed (closed DMs)'}.",
        ]

        # ── Auto-actions ───────────────────────────────────────────────────────
        action_taken = None

        if cfg["ban_at"] and count >= cfg["ban_at"]:
            try:
                await ctx.guild.ban(
                    user,
                    reason=f"NanoBot auto-ban: reached {count} warnings",
                    delete_message_days=0,
                )
                action_taken = f"🔨 Auto-banned (reached {count} warnings — threshold: {cfg['ban_at']})"
                log.warning(f"Auto-banned {user} ({user.id}) in {ctx.guild} — {count} warnings")
            except discord.Forbidden:
                action_taken = "⚠️ Auto-ban threshold reached but I lack Ban Members permission."

        elif cfg["kick_at"] and count >= cfg["kick_at"]:
            try:
                await ctx.guild.kick(user, reason=f"NanoBot auto-kick: reached {count} warnings")
                action_taken = f"👢 Auto-kicked (reached {count} warnings — threshold: {cfg['kick_at']})"
                log.warning(f"Auto-kicked {user} ({user.id}) in {ctx.guild} — {count} warnings")
            except discord.Forbidden:
                action_taken = "⚠️ Auto-kick threshold reached but I lack Kick Members permission."

        if action_taken:
            lines.append(action_taken)

        await ctx.reply(embed=h.warn("\n".join(lines), f"⚠️ Warning #{count}"), ephemeral=True)

        # Public action log
        e = discord.Embed(
            description=(
                f"⚠️ **{ctx.author.display_name}** warned **{user.display_name}** (`{user.id}`)\n"
                f"📝 {reason}\n"
                f"Total warnings: **{count}**"
                + (f"\n{action_taken}" if action_taken else "")
            ),
            color=h.YELLOW,
        )
        e.set_footer(text="NanoBot")
        e.timestamp = now
        try:
            await ctx.channel.send(embed=e)
        except discord.HTTPException:
            pass

    # ══════════════════════════════════════════════════════════════════════════
    #  /warnings
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="warnings",
        description="View all warnings for a user.",
    )
    @app_commands.describe(user="User to look up")
    @has_mod_perms()
    async def warnings(self, ctx: commands.Context, user: discord.Member):
        warns = await db.get_warnings(ctx.guild.id, user.id)

        if not warns:
            return await ctx.reply(
                embed=h.info(f"**{user.display_name}** has no warnings on this server.", "⚠️ Warnings"),
                ephemeral=True,
            )

        cfg = await db.get_warn_config(ctx.guild.id)

        e = h.embed(title=f"⚠️ Warnings — {user.display_name}", color=h.YELLOW)
        e.set_thumbnail(url=user.display_avatar.url)

        # Show up to last 8 warnings
        shown = warns[-8:]
        for w in shown:
            date = w["at"][:10]
            e.add_field(
                name  = f"#{w['id']}  ·  {w['by_name']}  ·  {date}",
                value = w["reason"][:300],
                inline= False,
            )

        lines = [f"**{len(warns)}** total warning(s)"]
        if cfg["kick_at"]:
            lines.append(f"👢 Auto-kick at {cfg['kick_at']}")
        if cfg["ban_at"]:
            lines.append(f"🔨 Auto-ban at {cfg['ban_at']}")

        e.set_footer(
            text=f"{'Showing last 8 of ' + str(len(warns)) if len(warns) > 8 else str(len(warns))} warning(s)  ·  "
                 + "  ·  ".join(lines[1:]) if len(lines) > 1 else
                 f"{len(warns)} warning(s)  ·  NanoBot"
        )
        await ctx.reply(embed=e, ephemeral=True)

    # ══════════════════════════════════════════════════════════════════════════
    #  /clearwarnings
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="clearwarnings",
        description="Clear all warnings for a user. Admin only.",
    )
    @app_commands.describe(user="User whose warnings to clear")
    @has_admin_perms()
    async def clearwarnings(self, ctx: commands.Context, user: discord.Member):
        count = await db.clear_warnings(ctx.guild.id, user.id)
        if count:
            log.info(f"Cleared {count} warning(s) for {user} ({user.id}) in {ctx.guild} by {ctx.author}")
            await ctx.reply(
                embed=h.ok(
                    f"Cleared **{count}** warning(s) for **{user.display_name}**.",
                    "✅ Warnings Cleared",
                ),
                ephemeral=True,
            )
        else:
            await ctx.reply(
                embed=h.info(f"**{user.display_name}** has no warnings to clear.", "⚠️ Warnings"),
                ephemeral=True,
            )

    # ══════════════════════════════════════════════════════════════════════════
    #  /warnconfig
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="warnconfig",
        description="Configure auto-actions for warnings (admin only).",
    )
    @app_commands.describe(
        kick_at = "Auto-kick after this many warnings (0 = disabled)",
        ban_at  = "Auto-ban after this many warnings (0 = disabled)",
        dm_user = "DM users when they are warned",
    )
    @has_admin_perms()
    async def warnconfig(
        self,
        ctx:     commands.Context,
        kick_at: Optional[int]  = None,
        ban_at:  Optional[int]  = None,
        dm_user: Optional[bool] = None,
    ):
        # If no args, show current config
        if kick_at is None and ban_at is None and dm_user is None:
            cfg = await db.get_warn_config(ctx.guild.id)
            e = h.embed(title="⚙️ Warning Config", color=h.BLUE)
            e.add_field(name="👢 Auto-Kick", value=str(cfg["kick_at"]) + " warnings" if cfg["kick_at"] else "Disabled", inline=True)
            e.add_field(name="🔨 Auto-Ban",  value=str(cfg["ban_at"])  + " warnings" if cfg["ban_at"]  else "Disabled", inline=True)
            e.add_field(name="📨 DM Users",  value="Yes" if cfg["dm_user"] else "No", inline=True)
            e.set_footer(text="Use /warnconfig kick_at:3 ban_at:5 to configure  ·  NanoBot")
            return await ctx.reply(embed=e, ephemeral=True)

        # Merge with existing config
        cfg = await db.get_warn_config(ctx.guild.id)
        new_kick = kick_at if kick_at is not None else cfg["kick_at"]
        new_ban  = ban_at  if ban_at  is not None else cfg["ban_at"]
        new_dm   = dm_user if dm_user is not None else cfg["dm_user"]

        if new_kick and new_ban and new_kick >= new_ban:
            return await ctx.reply(
                embed=h.err("Auto-kick threshold must be lower than auto-ban threshold."),
                ephemeral=True,
            )

        await db.set_warn_config(ctx.guild.id, new_kick, new_ban, new_dm)
        log.info(f"warnconfig updated in {ctx.guild}: kick_at={new_kick} ban_at={new_ban} dm={new_dm}")

        lines = [
            f"👢 Auto-kick at **{new_kick}** warnings" if new_kick else "👢 Auto-kick **disabled**",
            f"🔨 Auto-ban at **{new_ban}** warnings"  if new_ban  else "🔨 Auto-ban **disabled**",
            f"📨 DM on warn: **{'Yes' if new_dm else 'No'}**",
        ]
        await ctx.reply(
            embed=h.ok("\n".join(lines), "⚙️ Warning Config Updated"),
            ephemeral=True,
        )


# ── Registration ───────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(Warnings(bot))
