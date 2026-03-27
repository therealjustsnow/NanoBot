"""
cogs/warnings.py
Per-server warning system with configurable auto-actions.

Slash commands live under the /warn group (1 top-level slot).
Prefix commands remain flat: !warn, !warnings, !clearwarnings, !warnconfig.

Subcommands:
  /warn issue   (prefix: !warn)           -- warn a user with a reason
  /warn list    (prefix: !warnings)       -- view all warnings for a user
  /warn clear   (prefix: !clearwarnings)  -- wipe all warnings for a user (admin)
  /warn config  (prefix: !warnconfig)     -- configure auto-kick/ban thresholds (admin)
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
    #  /warn group -- 1 top-level slash slot
    # ══════════════════════════════════════════════════════════════════════════

    warn_group = commands.HybridGroup(
        name="warn",
        description="Warning system commands.",
        fallback="help",
    )

    # ── /warn issue ───────────────────────────────────────────────────────────

    @warn_group.command(
        name="issue",
        description="Issue a warning to a user. Configurable auto-actions apply.",
    )
    @app_commands.describe(user="User to warn", reason="Reason for the warning")
    @has_mod_perms()
    async def warn_issue(
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

        now = datetime.now(timezone.utc)
        count = await db.add_warning(
            ctx.guild.id,
            user.id,
            reason,
            str(ctx.author.id),
            str(ctx.author),
            now.isoformat(),
        )

        cfg = await db.get_warn_config(ctx.guild.id)

        dm_sent = False
        if cfg["dm_user"]:
            try:
                dm_e = discord.Embed(
                    title=f"\u26a0\ufe0f Warning #{count} \u2014 {ctx.guild.name}",
                    description=f"**Reason:** {reason}",
                    color=h.YELLOW,
                )
                dm_e.set_footer(text=f"Issued by {ctx.author.display_name}")
                dm_e.timestamp = now
                await user.send(embed=dm_e)
                dm_sent = True
            except discord.Forbidden:
                pass

        log.info(
            f"Warned {user} ({user.id}) in {ctx.guild} ({ctx.guild.id}) "
            f"by {ctx.author} \u2014 warning #{count}: {reason}"
        )

        lines = [
            f"**{user.display_name}** has been warned. That's warning **#{count}**.",
            f"\U0001f4dd {reason}",
            f"\U0001f4e8 DM {'sent' if dm_sent else 'failed (closed DMs)'}.",
        ]

        action_taken = None

        if cfg["ban_at"] and count >= cfg["ban_at"]:
            try:
                await ctx.guild.ban(
                    user,
                    reason=f"NanoBot auto-ban: reached {count} warnings",
                    delete_message_days=0,
                )
                action_taken = f"\U0001f528 Auto-banned (reached {count} warnings \u2014 threshold: {cfg['ban_at']})"
                log.warning(
                    f"Auto-banned {user} ({user.id}) in {ctx.guild} \u2014 {count} warnings"
                )
            except discord.Forbidden:
                action_taken = "\u26a0\ufe0f Auto-ban threshold reached but I lack Ban Members permission."

        elif cfg["kick_at"] and count >= cfg["kick_at"]:
            try:
                await ctx.guild.kick(
                    user, reason=f"NanoBot auto-kick: reached {count} warnings"
                )
                action_taken = f"\U0001f462 Auto-kicked (reached {count} warnings \u2014 threshold: {cfg['kick_at']})"
                log.warning(
                    f"Auto-kicked {user} ({user.id}) in {ctx.guild} \u2014 {count} warnings"
                )
            except discord.Forbidden:
                action_taken = "\u26a0\ufe0f Auto-kick threshold reached but I lack Kick Members permission."

        if action_taken:
            lines.append(action_taken)

        await ctx.reply(
            embed=h.warn("\n".join(lines), f"\u26a0\ufe0f Warning #{count}"),
            ephemeral=True,
        )

        e = discord.Embed(
            description=(
                f"\u26a0\ufe0f **{ctx.author.display_name}** warned **{user.display_name}** (`{user.id}`)\n"
                f"\U0001f4dd {reason}\n"
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

    # ── /warn list ────────────────────────────────────────────────────────────

    @warn_group.command(
        name="list",
        description="View all warnings for a user.",
    )
    @app_commands.describe(user="User to look up")
    @has_mod_perms()
    async def warn_list(self, ctx: commands.Context, user: discord.Member):
        warns = await db.get_warnings(ctx.guild.id, user.id)

        if not warns:
            return await ctx.reply(
                embed=h.info(
                    f"**{user.display_name}** has no warnings on this server.",
                    "\u26a0\ufe0f Warnings",
                ),
                ephemeral=True,
            )

        cfg = await db.get_warn_config(ctx.guild.id)

        e = h.embed(
            title=f"\u26a0\ufe0f Warnings \u2014 {user.display_name}", color=h.YELLOW
        )
        e.set_thumbnail(url=user.display_avatar.url)

        shown = warns[-8:]
        for w in shown:
            date = w["at"][:10]
            e.add_field(
                name=f"#{w['id']}  \u00b7  {w['by_name']}  \u00b7  {date}",
                value=w["reason"][:300],
                inline=False,
            )

        lines = [f"**{len(warns)}** total warning(s)"]
        if cfg["kick_at"]:
            lines.append(f"\U0001f462 Auto-kick at {cfg['kick_at']}")
        if cfg["ban_at"]:
            lines.append(f"\U0001f528 Auto-ban at {cfg['ban_at']}")

        e.set_footer(
            text=(
                f"{'Showing last 8 of ' + str(len(warns)) if len(warns) > 8 else str(len(warns))} warning(s)  \u00b7  "
                + "  \u00b7  ".join(lines[1:])
                if len(lines) > 1
                else f"{len(warns)} warning(s)  \u00b7  NanoBot"
            )
        )
        await ctx.reply(embed=e, ephemeral=True)

    # ── /warn clear ───────────────────────────────────────────────────────────

    @warn_group.command(
        name="clear",
        description="Clear all warnings for a user. Admin only.",
    )
    @app_commands.describe(user="User whose warnings to clear")
    @has_admin_perms()
    async def warn_clear(self, ctx: commands.Context, user: discord.Member):
        count = await db.clear_warnings(ctx.guild.id, user.id)
        if count:
            log.info(
                f"Cleared {count} warning(s) for {user} ({user.id}) in {ctx.guild} by {ctx.author}"
            )
            await ctx.reply(
                embed=h.ok(
                    f"Cleared **{count}** warning(s) for **{user.display_name}**.",
                    "\u2705 Warnings Cleared",
                ),
                ephemeral=True,
            )
        else:
            await ctx.reply(
                embed=h.info(
                    f"**{user.display_name}** has no warnings to clear.",
                    "\u26a0\ufe0f Warnings",
                ),
                ephemeral=True,
            )

    # ── /warn config ──────────────────────────────────────────────────────────

    @warn_group.command(
        name="config",
        description="Configure auto-actions for warnings (admin only).",
    )
    @app_commands.describe(
        kick_at="Auto-kick after this many warnings (0 = disabled)",
        ban_at="Auto-ban after this many warnings (0 = disabled)",
        dm_user="DM users when they are warned",
    )
    @has_admin_perms()
    async def warn_config(
        self,
        ctx: commands.Context,
        kick_at: Optional[int] = None,
        ban_at: Optional[int] = None,
        dm_user: Optional[bool] = None,
    ):
        if kick_at is None and ban_at is None and dm_user is None:
            cfg = await db.get_warn_config(ctx.guild.id)
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
            return await ctx.reply(embed=e, ephemeral=True)

        cfg = await db.get_warn_config(ctx.guild.id)
        new_kick = kick_at if kick_at is not None else cfg["kick_at"]
        new_ban = ban_at if ban_at is not None else cfg["ban_at"]
        new_dm = dm_user if dm_user is not None else cfg["dm_user"]

        if new_kick and new_ban and new_kick >= new_ban:
            return await ctx.reply(
                embed=h.err(
                    "Auto-kick threshold must be lower than auto-ban threshold."
                ),
                ephemeral=True,
            )

        await db.set_warn_config(ctx.guild.id, new_kick, new_ban, new_dm)
        log.info(
            f"warnconfig updated in {ctx.guild}: kick_at={new_kick} ban_at={new_ban} dm={new_dm}"
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
        await ctx.reply(
            embed=h.ok("\n".join(lines), "\u2699\ufe0f Warning Config Updated"),
            ephemeral=True,
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  PREFIX-ONLY ALIASES
    #  !warn, !warnings, !clearwarnings, !warnconfig
    # ══════════════════════════════════════════════════════════════════════════

    @commands.command(
        name="warn",
        extras={
            "category": "\U0001f6e1\ufe0f Moderation",
            "short": "Warn a user",
            "usage": "warn <user> [reason]",
            "desc": "Issue a warning to a user. Configurable auto-kick/ban thresholds apply.",
            "args": [
                ("user", "User to warn"),
                ("reason", "Reason for the warning (optional)"),
            ],
            "perms": "Manage Messages",
            "example": "!warn @Troublemaker Spamming in general",
        },
    )
    @has_mod_perms()
    async def pfx_warn(
        self, ctx, user: discord.Member, *, reason: str = "No reason provided."
    ):
        await self.warn_issue(ctx, user=user, reason=reason)

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
    async def pfx_warnings(self, ctx, user: discord.Member):
        await self.warn_list(ctx, user=user)

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
    async def pfx_clearwarnings(self, ctx, user: discord.Member):
        await self.warn_clear(ctx, user=user)

    @commands.command(
        name="warnconfig",
        extras={
            "category": "\U0001f6e1\ufe0f Moderation",
            "short": "Configure warning auto-actions",
            "usage": "warnconfig [kick_at] [ban_at] [dm_user]",
            "desc": "Configure auto-kick/ban thresholds and DM behavior for warnings.",
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
        ctx,
        kick_at: Optional[int] = None,
        ban_at: Optional[int] = None,
        dm_user: Optional[bool] = None,
    ):
        await self.warn_config(ctx, kick_at=kick_at, ban_at=ban_at, dm_user=dm_user)


# ── Registration ──────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(Warnings(bot))
