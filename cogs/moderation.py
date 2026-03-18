"""
cogs/moderation.py — v2.1.1
Core moderation commands — designed for speed on mobile.

Commands:
  cban / cleanban  — ban + purge history + optional timed unban + DM
  ban              — permanent ban + DM
  massban          — ban multiple IDs at once
  unban            — unban by user ID
  tempban          — timed ban with auto-unban (simpler than cban)
  kick             — kick + DM
  slow             — toggle / set slowmode (optional timed auto-disable)
  lock             — toggle channel lock for @everyone
  hide             — hide a channel from @everyone
  unhide           — restore @everyone visibility
  purge            — bulk-delete with filters (bots, user, contains, starts/ends with)
  snailpurge       — slow unrestricted delete with confirmation
  clean            — delete recent bot messages
  echo             — make the bot send a message
  nuke             — clone channel + delete original (wipes all messages)
  moveall          — move all members between voice channels
  freeze           — Discord timeout (temp mute)
  unfreeze         — remove a timeout early
  addrole          — give a role to a user
  removerole       — take a role from a user
  channelinfo      — info card for a channel
  note             — add a mod note (invisible to the user)
  notes            — view mod notes for a user
  clearnotes       — wipe all notes for a user
  last             — show who last sent a message here
"""

import asyncio
import logging
import random
import string
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import db
from utils import helpers as h
from utils.checks import (
    has_ban_perms,
    has_kick_perms,
    has_mod_perms,
    has_channel_perms,
    has_timeout_perms,
    has_role_perms,
    has_move_perms,
    has_admin_perms,
)

log = logging.getLogger("NanoBot.moderation")


# ── Helpers ────────────────────────────────────────────────────────────────────


async def resolve_target(bot, channel_id, explicit):
    return explicit if explicit else bot.last_senders.get(channel_id)


async def try_dm(member, content):
    try:
        await member.send(content)
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


def can_target(actor, target):
    if actor == actor.guild.owner:
        return True
    return actor.top_role > target.top_role


def can_bot_target(bot_member, target):
    """Check whether the bot's role is high enough to act on target.

    This is separate from can_target (which checks the human moderator).
    Discord will 403 if the bot's top role is not strictly above the target's
    top role, even when the bot has the relevant permission node.
    """
    return bot_member.top_role > target.top_role


async def action_log(ctx, emoji, action, *, target=None, detail=""):
    desc = f"{emoji} **{ctx.author.display_name}** used **{action}**"
    if target:
        desc += f" on **{target.display_name}** (`{target.id}`)"
    if detail:
        desc += f"\n{detail}"
    e = discord.Embed(description=desc, color=h.GREY)
    e.timestamp = discord.utils.utcnow()
    e.set_footer(text="NanoBot")
    try:
        await ctx.channel.send(embed=e)
    except discord.HTTPException:
        pass


# ── Nuke confirmation view ────────────────────────────────────────────────────
class NukeConfirm(discord.ui.View):
    """Ephemeral confirm/cancel buttons for /nuke. Times out after 30 s."""

    def __init__(self, author: discord.Member):
        super().__init__(timeout=30)
        self.author = author
        self.outcome: bool | None = None
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.author:
            await interaction.response.send_message(
                "That's not your nuke to confirm.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="💥 Nuke it", style=discord.ButtonStyle.danger)
    async def confirm_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.outcome = True
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(description="💥 Nuking…", color=0xED4245), view=None
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.outcome = False
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(description="✅ Nuke cancelled.", color=0x57F287),
            view=None,
        )


# ══════════════════════════════════════════════════════════════════════════════
class Moderation(commands.Cog):
    """Mobile-first moderation commands."""

    def __init__(self, bot):
        self.bot = bot
        self._slow_tasks = {}
        self._unban_tasks = {}

    @commands.Cog.listener()
    async def on_restore_schedules(self):
        await asyncio.gather(
            self._restore_unban_schedules(), self._restore_slow_schedules()
        )

    async def _restore_unban_schedules(self):
        data = await db.get_all_unbans()
        now = datetime.now(timezone.utc).timestamp()
        for key, info in data.items():
            remaining = info["until"] - now
            guild_id = int(info["guild_id"])
            user_id = int(info["user_id"])
            if remaining > 0:
                self._unban_tasks[key] = asyncio.create_task(
                    self._auto_unban(guild_id, user_id, remaining)
                )
            else:
                guild = self.bot.get_guild(guild_id)
                if guild:
                    try:
                        await guild.unban(
                            discord.Object(id=user_id),
                            reason="NanoBot: Timed unban (overdue)",
                        )
                        log.info(f"Overdue unban: {user_id} in {guild_id}")
                    except discord.NotFound:
                        pass
                await db.remove_unban(key)

    async def _restore_slow_schedules(self):
        data = await db.get_all_slows()
        now = datetime.now(timezone.utc).timestamp()
        for cid_str, info in data.items():
            remaining = info["until"] - now
            channel_id = int(cid_str)
            if remaining > 0:
                self._slow_tasks[channel_id] = asyncio.create_task(
                    self._auto_unslow(channel_id, remaining)
                )
            else:
                ch = self.bot.get_channel(channel_id)
                if ch:
                    try:
                        await ch.edit(slowmode_delay=0)
                    except discord.Forbidden:
                        pass
                await db.remove_slow(channel_id)

    async def _auto_unban(self, guild_id, user_id, delay):
        await asyncio.sleep(delay)
        guild = self.bot.get_guild(guild_id)
        if guild:
            try:
                await guild.unban(
                    discord.Object(id=user_id), reason="NanoBot: Timed unban complete"
                )
                log.info(f"Timed unban: {user_id} in {guild_id}")
            except discord.NotFound:
                pass
        key = f"{guild_id}:{user_id}"
        self._unban_tasks.pop(key, None)
        await db.remove_unban(key)

    async def _auto_unslow(self, channel_id, delay):
        await asyncio.sleep(delay)
        ch = self.bot.get_channel(channel_id)
        if ch:
            try:
                await ch.edit(
                    slowmode_delay=0, reason="NanoBot: Timed slowmode expired"
                )
                log.info(f"Timed slowmode removed: #{ch}")
            except discord.Forbidden:
                pass
        self._slow_tasks.pop(channel_id, None)
        await db.remove_slow(channel_id)

    async def _schedule_unban(self, guild_id, user_id, delay):
        key = f"{guild_id}:{user_id}"
        if key in self._unban_tasks:
            self._unban_tasks[key].cancel()
        await db.set_unban(
            key, guild_id, user_id, datetime.now(timezone.utc).timestamp() + delay
        )
        self._unban_tasks[key] = asyncio.create_task(
            self._auto_unban(guild_id, user_id, delay)
        )

    async def _schedule_unslow(self, channel_id, guild_id, delay):
        if channel_id in self._slow_tasks:
            self._slow_tasks[channel_id].cancel()
        await db.set_slow(
            channel_id, guild_id, datetime.now(timezone.utc).timestamp() + delay
        )
        self._slow_tasks[channel_id] = asyncio.create_task(
            self._auto_unslow(channel_id, delay)
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  cban
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="cban",
        aliases=["cleanban"],
        description="Ban + delete message history. Optional timed unban & DM.",
        extras={
            "category": "🔨 Banning",
            "short": "Ban + wipe message history + optional timed unban",
            "usage": "cban [user] [days] [wait] [message]",
            "desc": "The mobile mod's best friend. Always deletes message history (1–7 days), optionally DMs the user, and optionally auto-unbans after a set time. Defaults to the last message sender if no user is given.",
            "args": [
                ("user", "Who to ban (blank = last sender)"),
                ("days", "Days of message history to delete (1–7, default 7)"),
                ("wait", "Auto-unban after e.g. 30m, 1h, 7d (omit for permanent)"),
                ("message", "DM to send the user (omit for default)"),
            ],
            "perms": "Ban Members",
            "example": "!cban @user 7 24h See you tomorrow.",
        },
    )
    @app_commands.describe(
        user="Who to ban (blank=last sender)",
        days="Days of history to delete (1–7)",
        wait="Auto-unban after e.g. 1h 7d",
        message="DM to send the user",
    )
    @has_ban_perms()
    async def cban(
        self,
        ctx,
        user: Optional[discord.Member] = None,
        days: Optional[int] = 7,
        wait: Optional[str] = None,
        *,
        message: Optional[str] = None,
    ):
        target = await resolve_target(self.bot, ctx.channel.id, user)
        if not target:
            return await ctx.reply(
                embed=h.err("No user specified and no recent sender tracked."),
                ephemeral=True,
            )
        if target == ctx.author:
            return await ctx.reply(
                embed=h.err("You can't ban yourself."), ephemeral=True
            )
        if not can_target(ctx.author, target):
            return await ctx.reply(
                embed=h.err(f"**{target.display_name}** outranks you."), ephemeral=True
            )
        if not can_bot_target(ctx.guild.me, target):
            return await ctx.reply(
                embed=h.err(
                    f"I can't ban **{target.display_name}** — their role is at or above mine.\n"
                    "Go to **Server Settings → Roles** and drag my role above theirs."
                ),
                ephemeral=True,
            )

        days = max(1, min(7, days or 7))
        wait_secs = h.parse_duration(wait)
        is_timed = wait_secs is not None

        dm_text = message or (
            f"You've been temporarily banned from **{ctx.guild.name}**. Rejoin after **{h.fmt_duration(wait_secs)}**."
            if is_timed
            else f"You've been banned from **{ctx.guild.name}**."
        )
        dm_sent = await try_dm(target, dm_text)

        try:
            await ctx.guild.ban(
                target,
                reason=f"cban by {ctx.author} ({ctx.author.id})"
                + (f" · timed: {h.fmt_duration(wait_secs)}" if is_timed else ""),
                delete_message_days=days,
            )
        except discord.Forbidden:
            return await ctx.reply(
                embed=h.err(
                    "I don't have permission to ban — check my role is above the target's "
                    "and that I have the **Ban Members** permission."
                ),
                ephemeral=True,
            )

        log.info(
            f"cban: {target} ({target.id}) by {ctx.author} in #{ctx.channel} / {ctx.guild} | days={days} timed={is_timed}"
        )
        if is_timed:
            await self._schedule_unban(ctx.guild.id, target.id, wait_secs)

        lines = [
            f"🗂️ Deleted **{days}d** of history.",
            f"📨 DM {'sent' if dm_sent else 'failed'}.",
        ]
        if is_timed:
            lines.append(f"⏱️ Auto-unban in **{h.fmt_duration(wait_secs)}**.")
        await ctx.reply(
            embed=h.ok(
                f"**{target.display_name}** (`{target.id}`) banned.\n"
                + "\n".join(lines),
                f"🔨 {'Timed ' if is_timed else ''}Clean Ban",
            ),
            ephemeral=True,
        )
        await action_log(
            ctx,
            "🔨",
            "cban",
            target=target,
            detail=f"🗂️ {days}d deleted"
            + (f" · ⏱️ unban in {h.fmt_duration(wait_secs)}" if is_timed else ""),
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  ban
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="ban",
        description="Permanently ban a user with an optional DM.",
        extras={
            "category": "🔨 Banning",
            "short": "Permanently ban a user with optional DM",
            "usage": "ban [user] [message]",
            "desc": "Permanent ban with no message history deletion. Targets last sender if no user is specified.",
            "args": [
                ("user", "Who to ban (blank = last sender)"),
                ("message", "DM to send (omit for default)"),
            ],
            "perms": "Ban Members",
            "example": "!ban @user You have been permanently banned.",
        },
    )
    @app_commands.describe(
        user="Who to ban (blank=last sender)", message="DM to send the user"
    )
    @has_ban_perms()
    async def ban(
        self,
        ctx,
        user: Optional[discord.Member] = None,
        *,
        message: Optional[str] = None,
    ):
        target = await resolve_target(self.bot, ctx.channel.id, user)
        if not target:
            return await ctx.reply(
                embed=h.err("No user specified and no recent sender tracked."),
                ephemeral=True,
            )
        if target == ctx.author:
            return await ctx.reply(
                embed=h.err("You can't ban yourself."), ephemeral=True
            )
        if not can_target(ctx.author, target):
            return await ctx.reply(
                embed=h.err(f"**{target.display_name}** outranks you."), ephemeral=True
            )
        if not can_bot_target(ctx.guild.me, target):
            return await ctx.reply(
                embed=h.err(
                    f"I can't ban **{target.display_name}** — their role is at or above mine.\n"
                    "Go to **Server Settings → Roles** and drag my role above theirs."
                ),
                ephemeral=True,
            )

        dm_sent = await try_dm(
            target, message or f"You've been banned from **{ctx.guild.name}**."
        )
        try:
            await ctx.guild.ban(
                target,
                reason=f"ban by {ctx.author} ({ctx.author.id})",
                delete_message_days=0,
            )
        except discord.Forbidden:
            return await ctx.reply(
                embed=h.err(
                    "I don't have permission to ban — check my role is above the target's "
                    "and that I have the **Ban Members** permission."
                ),
                ephemeral=True,
            )

        log.info(f"ban: {target} ({target.id}) by {ctx.author} in {ctx.guild}")
        await ctx.reply(
            embed=h.ok(
                f"**{target.display_name}** (`{target.id}`) permanently banned.\n📨 DM {'sent' if dm_sent else 'failed'}.",
                "🔨 Banned",
            ),
            ephemeral=True,
        )
        await action_log(ctx, "🔨", "ban", target=target)

    # ══════════════════════════════════════════════════════════════════════════
    #  massban
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="massban",
        description="Ban multiple users by ID. Paste IDs separated by spaces.",
        extras={
            "category": "🔨 Banning",
            "short": "Ban multiple users by ID at once",
            "usage": "massban <id1 id2 ...> [reason]",
            "desc": "Paste a space-separated list of user IDs. Maximum 50 per command. Useful after a raid.",
            "args": [
                ("user_ids", "Space-separated list of user IDs to ban"),
                ("reason", "Reason applied to all bans"),
            ],
            "perms": "Ban Members",
            "example": "!massban 111 222 333 Raid cleanup",
        },
    )
    @app_commands.describe(
        user_ids="Space-separated list of user IDs", reason="Reason applied to all bans"
    )
    @has_ban_perms()
    async def massban(self, ctx, *, user_ids: str, reason: Optional[str] = None):
        await ctx.defer(ephemeral=True)
        raw_ids = user_ids.split()
        ids, invalid = [], []
        for raw in raw_ids:
            raw = raw.strip("<@!>")
            try:
                ids.append(int(raw))
            except ValueError:
                invalid.append(raw)

        if not ids:
            return await ctx.reply(
                embed=h.err("No valid user IDs found."), ephemeral=True
            )
        if len(ids) > 50:
            return await ctx.reply(
                embed=h.err("Maximum 50 users per massban."), ephemeral=True
            )

        rsn = reason or f"massban by {ctx.author} ({ctx.author.id})"
        ok_ids, fail_ids = [], []
        for uid in ids:
            try:
                await ctx.guild.ban(
                    discord.Object(id=uid), reason=rsn, delete_message_days=0
                )
                ok_ids.append(uid)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                fail_ids.append(uid)

        log.warning(
            f"massban: {len(ok_ids)} banned, {len(fail_ids)} failed by {ctx.author} in {ctx.guild}"
        )
        lines = [f"✅ Banned **{len(ok_ids)}** user(s)."]
        if fail_ids:
            lines.append(f"❌ Failed: {len(fail_ids)}")
        if invalid:
            lines.append(f"⚠️ Skipped (not IDs): {', '.join(invalid[:5])}")
        await ctx.send(embed=h.ok("\n".join(lines), "🔨 Mass Ban"), ephemeral=True)
        await action_log(ctx, "🔨", "massban", detail=f"{len(ok_ids)} user(s) banned")

    # ══════════════════════════════════════════════════════════════════════════
    #  unban
    # ══════════════════════════════════════════════════════════════════════════,
    extras = (
        {
            "category": "🔨 Banning",
            "short": "Unban a user by their ID",
            "usage": "unban <user_id> [reason]",
            "desc": "Unbans by User ID. Enable Developer Mode → right-click any user → Copy ID.",
            "args": [
                ("user_id", "The user's Discord ID"),
                ("reason", "Optional reason (shown in audit log)"),
            ],
            "perms": "Ban Members",
            "example": "!unban 123456789012345678",
        },
    )

    @commands.hybrid_command(name="unban", description="Unban a user by their User ID.")
    @app_commands.describe(user_id="Discord User ID", reason="Optional reason")
    @has_ban_perms()
    async def unban(self, ctx, user_id: str, *, reason: Optional[str] = None):
        try:
            uid = int(user_id.strip())
        except ValueError:
            return await ctx.reply(
                embed=h.err("That doesn't look like a valid User ID."), ephemeral=True
            )

        try:
            await ctx.guild.unban(
                discord.Object(id=uid),
                reason=reason or f"unban by {ctx.author} ({ctx.author.id})",
            )
        except discord.NotFound:
            return await ctx.reply(
                embed=h.err(f"User `{uid}` is not currently banned."), ephemeral=True
            )
        except discord.Forbidden:
            return await ctx.reply(
                embed=h.err("I don't have permission to unban."), ephemeral=True
            )

        key = f"{ctx.guild.id}:{uid}"
        if key in self._unban_tasks:
            self._unban_tasks[key].cancel()
            self._unban_tasks.pop(key, None)
            await db.remove_unban(key)

        log.info(f"unban: {uid} by {ctx.author} in {ctx.guild}")
        await ctx.reply(
            embed=h.ok(f"User `{uid}` has been unbanned.", "✅ Unbanned"),
            ephemeral=True,
        )
        await action_log(ctx, "✅", "unban", detail=f"User ID: `{uid}`")

    # ══════════════════════════════════════════════════════════════════════════
    #  kick
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="kick",
        description="Kick a user. Defaults to last message sender.",
        extras={
            "category": "👢 Kicking & Timeouts",
            "short": "Kick a user — they can rejoin",
            "usage": "kick [user] [message]",
            "desc": "Kicks with an optional DM. Targets last sender if no user specified.",
            "args": [
                ("user", "Who to kick (blank = last sender)"),
                ("message", "DM to send (omit for default)"),
            ],
            "perms": "Kick Members",
            "example": "!kick @user Please review the rules.",
        },
    )
    @app_commands.describe(
        user="Who to kick (blank=last sender)", message="DM to send the user"
    )
    @has_kick_perms()
    async def kick(
        self,
        ctx,
        user: Optional[discord.Member] = None,
        *,
        message: Optional[str] = None,
    ):
        target = await resolve_target(self.bot, ctx.channel.id, user)
        if not target:
            return await ctx.reply(
                embed=h.err("No user specified and no recent sender tracked."),
                ephemeral=True,
            )
        if target == ctx.author:
            return await ctx.reply(
                embed=h.err("You can't kick yourself."), ephemeral=True
            )
        if not can_target(ctx.author, target):
            return await ctx.reply(
                embed=h.err(f"**{target.display_name}** outranks you."), ephemeral=True
            )
        if not can_bot_target(ctx.guild.me, target):
            return await ctx.reply(
                embed=h.err(
                    f"I can't kick **{target.display_name}** — their role is at or above mine.\n"
                    "Go to **Server Settings → Roles** and drag my role above theirs."
                ),
                ephemeral=True,
            )

        dm_sent = await try_dm(
            target,
            message
            or f"You've been kicked from **{ctx.guild.name}** but can rejoin at any time.",
        )
        try:
            await ctx.guild.kick(
                target, reason=f"kick by {ctx.author} ({ctx.author.id})"
            )
        except discord.Forbidden:
            return await ctx.reply(
                embed=h.err(
                    "I don't have permission to kick — check my role is above the target's "
                    "and that I have the **Kick Members** permission."
                ),
                ephemeral=True,
            )

        log.info(f"kick: {target} ({target.id}) by {ctx.author} in {ctx.guild}")
        await ctx.reply(
            embed=h.ok(
                f"**{target.display_name}** (`{target.id}`) kicked.\n📨 DM {'sent' if dm_sent else 'failed'}.",
                "👢 Kicked",
            ),
            ephemeral=True,
        )
        await action_log(ctx, "👢", "kick", target=target)

    # ══════════════════════════════════════════════════════════════════════════
    #  slow
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="slow",
        description="Toggle slowmode. No args = toggle. Add delay and optional timer.",
        extras={
            "category": "📢 Channel Controls",
            "short": "Toggle or set slowmode with optional auto-disable",
            "usage": "slow [delay] [length]",
            "desc": "No args = toggle. With delay = set slowmode. With length = auto-disable after that time (survives restarts).",
            "args": [
                ("delay", "Slowmode delay: 30s, 2m, 5m (max 5 min). Omit to toggle."),
                ("length", "Auto-disable after: 10m, 1h, 3d (max 7 days)."),
            ],
            "perms": "Manage Channels",
            "example": "!slow 2m 1h",
        },
    )
    @app_commands.describe(
        delay="Slowmode delay e.g. 30s 2m 5m (max 5 min). Omit to toggle.",
        length="Auto-disable after e.g. 10m 1h 3d. Omit for indefinite.",
    )
    @has_channel_perms()
    async def slow(
        self, ctx, delay: Optional[str] = None, length: Optional[str] = None
    ):
        channel = ctx.channel
        current = channel.slowmode_delay

        if delay is None:
            if current > 0:
                await channel.edit(
                    slowmode_delay=0, reason=f"slow toggle off by {ctx.author}"
                )
                if channel.id in self._slow_tasks:
                    self._slow_tasks[channel.id].cancel()
                    self._slow_tasks.pop(channel.id, None)
                await ctx.reply(
                    embed=h.ok(
                        f"Slowmode disabled in {channel.mention}.", "🐢 Slowmode Off"
                    ),
                    ephemeral=True,
                )
                await action_log(ctx, "🐢", "slow off", detail=f"in {channel.mention}")
                return
            else:
                delay = "60s"

        delay_secs = h.parse_duration(delay)
        if not delay_secs or delay_secs < 1:
            return await ctx.reply(
                embed=h.err("Invalid delay. Use `30s`, `2m`, `5m`, etc."),
                ephemeral=True,
            )
        if delay_secs > 300:
            return await ctx.reply(
                embed=h.err("Discord max slowmode is **5 minutes** (300s)."),
                ephemeral=True,
            )

        length_secs = None
        if length:
            length_secs = h.parse_duration(length)
            if not length_secs or length_secs < 60:
                return await ctx.reply(
                    embed=h.err(
                        "Invalid length. Use `10m`, `1h`, etc. (min 1 minute)."
                    ),
                    ephemeral=True,
                )
            if length_secs > 7 * 86400:
                return await ctx.reply(
                    embed=h.err("Max timed slowmode is 7 days."), ephemeral=True
                )

        await channel.edit(slowmode_delay=delay_secs, reason=f"slow by {ctx.author}")
        desc = f"Slowmode set to **{h.fmt_duration(delay_secs)}** in {channel.mention}."
        if length_secs:
            desc += f"\n⏱️ Auto-disables in **{h.fmt_duration(length_secs)}**."
            await self._schedule_unslow(channel.id, ctx.guild.id, length_secs)
        else:
            desc += "\n_Use `/slow` with no args to toggle off._"
        await ctx.reply(embed=h.ok(desc, "🐢 Slowmode On"), ephemeral=True)
        log_detail = h.fmt_duration(delay_secs) + (
            f" · auto-off in {h.fmt_duration(length_secs)}" if length_secs else ""
        )
        await action_log(ctx, "🐢", "slow", detail=f"{log_detail} in {channel.mention}")

    # ══════════════════════════════════════════════════════════════════════════
    #  lock
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="lock",
        description="Toggle @everyone channel lock. Run again to unlock.",
        extras={
            "category": "📢 Channel Controls",
            "short": "Toggle @everyone channel lock",
            "usage": "lock [channel] [reason]",
            "desc": "Prevents @everyone from sending messages. Run again to unlock.",
            "args": [
                ("channel", "Channel to lock (default: current)"),
                ("reason", "Optional reason in audit log"),
            ],
            "perms": "Manage Channels",
            "example": "!lock #general Temporary lock during raid.",
        },
    )
    @app_commands.describe(
        channel="Channel to lock (default: current)", reason="Optional reason"
    )
    @has_channel_perms()
    async def lock(
        self,
        ctx,
        channel: Optional[discord.TextChannel] = None,
        *,
        reason: Optional[str] = None,
    ):
        target = channel or ctx.channel
        everyone = ctx.guild.default_role
        ow = target.overwrites_for(everyone)
        is_locked = ow.send_messages is False

        if is_locked:
            ow.send_messages = None
            await target.set_permissions(
                everyone, overwrite=ow, reason=f"unlock by {ctx.author}"
            )
            await ctx.reply(
                embed=h.ok(f"{target.mention} is now **unlocked**. 🔓", "🔓 Unlocked"),
                ephemeral=True,
            )
            await action_log(ctx, "🔓", "unlock", detail=f"in {target.mention}")
        else:
            ow.send_messages = False
            await target.set_permissions(
                everyone, overwrite=ow, reason=reason or f"lock by {ctx.author}"
            )
            desc = f"{target.mention} is now **locked** for @everyone." + (
                f"\n📝 {reason}" if reason else ""
            )
            await ctx.reply(embed=h.ok(desc, "🔒 Locked"), ephemeral=True)
            await action_log(
                ctx,
                "🔒",
                "lock",
                detail=f"in {target.mention}" + (f" · {reason}" if reason else ""),
            )

    # ══════════════════════════════════════════════════════════════════════════
    #  purge
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="purge",
        description="Bulk delete messages with optional filters.",
        extras={
            "category": "📢 Channel Controls",
            "short": "Bulk delete with optional filters (1–100)",
            "usage": "purge <amount> [bots] [user] [contains] [starts_with] [ends_with]",
            "desc": "Deletes up to 100 messages. Combine filters: bots only, by user, text matching.",
            "args": [
                ("amount", "Number of messages to scan (1–100, required)"),
                ("bots", "Only delete bot messages"),
                ("user", "Only delete from this user (mention, ID, or nickname)"),
                ("contains", "Only messages containing this text"),
                ("starts_with", "Only messages starting with this text"),
                ("ends_with", "Only messages ending with this text"),
            ],
            "perms": "Manage Messages",
            "example": "!purge 50 user:@spammer",
        },
    )
    @app_commands.describe(
        amount="Number of messages to scan (1–100)",
        bots="Only delete bot messages",
        user="Only messages from this user (mention, ID, or nickname)",
        contains="Only messages containing this text",
        starts_with="Only messages starting with this text",
        ends_with="Only messages ending with this text",
    )
    @has_mod_perms()
    async def purge(
        self,
        ctx,
        amount: int,
        bots: Optional[bool] = None,
        user: Optional[str] = None,
        contains: Optional[str] = None,
        starts_with: Optional[str] = None,
        ends_with: Optional[str] = None,
    ):
        if not 1 <= amount <= 100:
            return await ctx.reply(
                embed=h.err("Amount must be between **1** and **100**."), ephemeral=True
            )
        await ctx.defer(ephemeral=True)

        # Resolve user filter
        target_member = None
        if user:
            clean = user.strip("<@!>")
            if clean.isdigit():
                target_member = ctx.guild.get_member(int(clean))
            if not target_member:
                low = user.lower()
                target_member = discord.utils.find(
                    lambda m: m.display_name.lower() == low or m.name.lower() == low,
                    ctx.guild.members,
                )
            if not target_member:
                return await ctx.send(
                    embed=h.err(f"Couldn't find a member matching `{user}`."),
                    ephemeral=True,
                )

        checks = []
        if bots:
            checks.append(lambda m: m.author.bot)
        if target_member:
            checks.append(lambda m, t=target_member: m.author == t)
        if contains:
            low = contains.lower()
            checks.append(lambda m, s=low: s in m.content.lower())
        if starts_with:
            low = starts_with.lower()
            checks.append(lambda m, s=low: m.content.lower().startswith(s))
        if ends_with:
            low = ends_with.lower()
            checks.append(lambda m, s=low: m.content.lower().endswith(s))

        def combined(m):
            return all(c(m) for c in checks) if checks else True

        deleted = await ctx.channel.purge(limit=amount + 1, check=combined, bulk=True)
        count = max(0, len(deleted) - (1 if not ctx.interaction else 0))

        parts = [f"Deleted **{count}** message{'s' if count != 1 else ''}."]
        if bots:
            parts.append("🤖 Bots only")
        if target_member:
            parts.append(f"👤 {target_member.display_name} only")
        if contains:
            parts.append(f"🔍 Contains: `{contains}`")
        if starts_with:
            parts.append(f"▶️ Starts with: `{starts_with}`")
        if ends_with:
            parts.append(f"◀️ Ends with: `{ends_with}`")

        log.info(
            f"purge: {count} messages by {ctx.author} in #{ctx.channel} / {ctx.guild}"
        )
        await ctx.send(embed=h.ok("  ·  ".join(parts), "🗑️ Purged"), ephemeral=True)
        await action_log(
            ctx,
            "🗑️",
            "purge",
            detail=f"{count} messages"
            + (f" from {target_member.display_name}" if target_member else ""),
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  snailpurge
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="snailpurge",
        description="Slow delete of older messages (no 14-day limit). Requires confirmation.",
        extras={
            "category": "📢 Channel Controls",
            "short": "Slow delete up to 500 messages — no 14-day limit",
            "usage": "snailpurge <amount>",
            "desc": "Deletes messages one-by-one (~80/min) so it works on messages older than 14 days. Requires a confirmation code.",
            "args": [
                ("amount", "Number of messages to delete (1–500)"),
            ],
            "perms": "Manage Messages",
            "example": "!snailpurge 200",
        },
    )
    @app_commands.describe(amount="Number of messages to delete (1–500)")
    @has_mod_perms()
    async def snailpurge(self, ctx, amount: int):
        if not 1 <= amount <= 500:
            return await ctx.reply(
                embed=h.err("Amount must be between **1** and **500**."), ephemeral=True
            )

        code = "".join(random.choices(string.digits, k=4))
        warn_e = h.warn(
            f"⚠️ This will **slowly** delete the last **{amount}** messages in {ctx.channel.mention}.\n"
            f"Bypasses Discord's 14-day limit but is much slower (~80 msg/min).\n\n"
            f"**To confirm, type:** `{code}`\n"
            f"_(30 seconds. Type anything else or wait to cancel.)_",
            "🐌 Snail Purge — Confirm",
        )
        await ctx.reply(embed=warn_e, ephemeral=True)

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            reply = await self.bot.wait_for("message", check=check, timeout=30)
        except asyncio.TimeoutError:
            return await ctx.send(
                embed=h.info("Snail purge cancelled (timed out).", "🐌 Cancelled"),
                ephemeral=True,
            )

        if reply.content.strip() != code:
            try:
                await reply.delete()
            except discord.HTTPException:
                pass
            return await ctx.send(
                embed=h.info("Snail purge cancelled (wrong code).", "🐌 Cancelled"),
                ephemeral=True,
            )

        try:
            await reply.delete()
        except discord.HTTPException:
            pass

        await ctx.send(
            embed=h.ok(
                f"Snail purge started — deleting up to **{amount}** messages...",
                "🐌 In Progress",
            ),
            ephemeral=True,
        )

        deleted = 0
        async for message in ctx.channel.history(limit=amount):
            try:
                await message.delete()
                deleted += 1
                await asyncio.sleep(0.75)
            except discord.Forbidden:
                break
            except discord.HTTPException:
                await asyncio.sleep(2)

        log.warning(
            f"snailpurge: {deleted}/{amount} by {ctx.author} in #{ctx.channel} / {ctx.guild}"
        )
        await ctx.send(
            embed=h.ok(
                f"Done. Deleted **{deleted}** message(s).", "🐌 Snail Purge Complete"
            ),
            ephemeral=True,
        )
        await action_log(
            ctx, "🐌", "snailpurge", detail=f"{deleted} messages (slow delete)"
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  clean
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="clean",
        description="Delete recent NanoBot messages from this channel.",
        extras={
            "category": "📢 Channel Controls",
            "short": "Delete NanoBot's own recent messages",
            "usage": "clean [amount]",
            "desc": "Removes NanoBot's own messages from the channel.",
            "args": [
                ("amount", "Messages to scan (1–100, default 50)"),
            ],
            "perms": "Manage Messages",
            "example": "!clean 20",
        },
    )
    @app_commands.describe(amount="How many messages to scan (1–100, default 50)")
    @has_mod_perms()
    async def clean(self, ctx, amount: int = 50):
        if not 1 <= amount <= 100:
            return await ctx.reply(
                embed=h.err("Amount must be between 1 and 100."), ephemeral=True
            )
        await ctx.defer(ephemeral=True)
        deleted = await ctx.channel.purge(
            limit=amount, check=lambda m: m.author == ctx.guild.me, bulk=True
        )
        log.info(
            f"clean: {len(deleted)} bot messages removed by {ctx.author} in #{ctx.channel} / {ctx.guild}"
        )
        await ctx.send(
            embed=h.ok(f"Removed **{len(deleted)}** bot message(s).", "🧹 Cleaned"),
            ephemeral=True,
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  freeze
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="freeze",
        description="Timeout a user (default 10m). They can't speak, react, or join VCs.",
        extras={
            "category": "👢 Kicking & Timeouts",
            "short": "Timeout a user (default 10m)",
            "usage": "freeze [user] [duration] [reason]",
            "desc": "Discord Timeout — they can't speak, react, or join VCs. Max 28 days.",
            "args": [
                ("user", "Who to freeze (blank = last sender)"),
                ("duration", "5m, 1h, 1d (default 10m, max 28 days)"),
                ("reason", "Optional reason"),
            ],
            "perms": "Moderate Members",
            "example": "!freeze @user 30m Please cool down.",
        },
    )
    @app_commands.describe(
        user="Who to freeze (blank=last sender)",
        duration="e.g. 5m 1h 1d (max 28d, default 10m)",
        reason="Optional reason",
    )
    @has_timeout_perms()
    async def freeze(
        self,
        ctx,
        user: Optional[discord.Member] = None,
        duration: Optional[str] = "10m",
        *,
        reason: Optional[str] = None,
    ):
        target = await resolve_target(self.bot, ctx.channel.id, user)
        if not target:
            return await ctx.reply(
                embed=h.err("No user specified and no recent sender tracked."),
                ephemeral=True,
            )
        if target == ctx.author:
            return await ctx.reply(
                embed=h.err("You can't freeze yourself."), ephemeral=True
            )
        if not can_target(ctx.author, target):
            return await ctx.reply(
                embed=h.err(f"**{target.display_name}** outranks you."), ephemeral=True
            )
        if not can_bot_target(ctx.guild.me, target):
            return await ctx.reply(
                embed=h.err(
                    f"I can't freeze **{target.display_name}** — their role is at or above mine.\n"
                    "Go to **Server Settings → Roles** and drag my role above theirs."
                ),
                ephemeral=True,
            )

        secs = h.parse_duration(duration or "10m")
        if not secs or secs < 1:
            return await ctx.reply(
                embed=h.err("Invalid duration. Try `5m`, `1h`, `1d`."), ephemeral=True
            )
        if secs > 28 * 86400:
            return await ctx.reply(
                embed=h.err("Maximum timeout is **28 days**."), ephemeral=True
            )

        try:
            await target.timeout(
                discord.utils.utcnow() + timedelta(seconds=secs),
                reason=reason or f"freeze by {ctx.author}",
            )
        except discord.Forbidden:
            return await ctx.reply(
                embed=h.err(
                    "I can't timeout that user — check my role is above theirs "
                    "and that I have the **Timeout Members** permission."
                ),
                ephemeral=True,
            )

        log.info(
            f"freeze: {target} ({target.id}) for {h.fmt_duration(secs)} by {ctx.author} in {ctx.guild}"
        )
        desc = (
            f"**{target.display_name}** is frozen for **{h.fmt_duration(secs)}**."
            + (f"\n📝 {reason}" if reason else "")
        )
        await ctx.reply(embed=h.ok(desc, "🧊 Frozen"), ephemeral=True)
        await action_log(
            ctx,
            "🧊",
            "freeze",
            target=target,
            detail=h.fmt_duration(secs) + (f" · {reason}" if reason else ""),
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  unfreeze
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="unfreeze",
        description="Remove a timeout from a user early.",
        extras={
            "category": "👢 Kicking & Timeouts",
            "short": "Remove a timeout early",
            "usage": "unfreeze <user>",
            "desc": "Removes an active Discord Timeout from a user before it expires.",
            "args": [
                ("user", "User to unfreeze (required)"),
            ],
            "perms": "Moderate Members",
            "example": "!unfreeze @user",
        },
    )
    @app_commands.describe(user="User to unfreeze")
    @has_timeout_perms()
    async def unfreeze(self, ctx, user: discord.Member):
        if not user.timed_out_until:
            return await ctx.reply(
                embed=h.warn(f"**{user.display_name}** is not frozen."), ephemeral=True
            )
        try:
            await user.timeout(None, reason=f"unfreeze by {ctx.author}")
        except discord.Forbidden:
            return await ctx.reply(
                embed=h.err("I can't remove that timeout."), ephemeral=True
            )
        log.info(f"unfreeze: {user} ({user.id}) by {ctx.author} in {ctx.guild}")
        await ctx.reply(
            embed=h.ok(f"**{user.display_name}** has been unfrozen.", "🌡️ Unfrozen"),
            ephemeral=True,
        )
        await action_log(ctx, "🌡️", "unfreeze", target=user)

    # ══════════════════════════════════════════════════════════════════════════
    #  addrole / removerole
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="addrole",
        aliases=["ar", "giverole"],
        description="Give a role to a user.",
        extras={
            "category": "🎭 Roles",
            "short": "Give a role to a user",
            "usage": "addrole <user> <role>",
            "desc": "Assigns a role to a user. The role must be below NanoBot's top role.",
            "args": [
                ("user", "User to give the role to"),
                ("role", "Role to assign (mention or name)"),
            ],
            "perms": "Manage Roles",
            "example": "!addrole @user Verified",
        },
    )
    @app_commands.describe(user="User to give the role to", role="Role to assign")
    @has_role_perms()
    async def addrole(self, ctx, user: discord.Member, role: discord.Role):
        if role in user.roles:
            return await ctx.reply(
                embed=h.info(f"**{user.display_name}** already has **{role.name}**."),
                ephemeral=True,
            )
        if role >= ctx.guild.me.top_role:
            return await ctx.reply(
                embed=h.err(f"I can't assign **{role.name}** — it outranks me."),
                ephemeral=True,
            )
        try:
            await user.add_roles(role, reason=f"addrole by {ctx.author}")
        except discord.Forbidden:
            return await ctx.reply(
                embed=h.err("I don't have permission to assign that role."),
                ephemeral=True,
            )
        log.info(f"addrole: {role} → {user} ({user.id}) by {ctx.author} in {ctx.guild}")
        await ctx.reply(
            embed=h.ok(
                f"Gave **{role.name}** to **{user.display_name}**.", "🎭 Role Added"
            ),
            ephemeral=True,
        )
        await action_log(ctx, "🎭", "addrole", target=user, detail=f"role: {role.name}")

    @commands.hybrid_command(
        name="removerole",
        aliases=["rr", "takerole"],
        description="Remove a role from a user.",
        extras={
            "category": "🎭 Roles",
            "short": "Remove a role from a user",
            "usage": "removerole <user> <role>",
            "desc": "Removes a role from a user. The role must be below NanoBot's top role.",
            "args": [
                ("user", "User to remove the role from"),
                ("role", "Role to remove (mention or name)"),
            ],
            "perms": "Manage Roles",
            "example": "!removerole @user Muted",
        },
    )
    @app_commands.describe(user="User to remove the role from", role="Role to remove")
    @has_role_perms()
    async def removerole(self, ctx, user: discord.Member, role: discord.Role):
        if role not in user.roles:
            return await ctx.reply(
                embed=h.info(f"**{user.display_name}** doesn't have **{role.name}**."),
                ephemeral=True,
            )
        if role >= ctx.guild.me.top_role:
            return await ctx.reply(
                embed=h.err(f"I can't remove **{role.name}** — it outranks me."),
                ephemeral=True,
            )
        try:
            await user.remove_roles(role, reason=f"removerole by {ctx.author}")
        except discord.Forbidden:
            return await ctx.reply(
                embed=h.err("I don't have permission to remove that role."),
                ephemeral=True,
            )
        log.info(
            f"removerole: {role} ← {user} ({user.id}) by {ctx.author} in {ctx.guild}"
        )
        await ctx.reply(
            embed=h.ok(
                f"Removed **{role.name}** from **{user.display_name}**.",
                "🎭 Role Removed",
            ),
            ephemeral=True,
        )
        await action_log(
            ctx, "🎭", "removerole", target=user, detail=f"role: {role.name}"
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  channelinfo
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="channelinfo",
        aliases=["ci", "channel"],
        description="Info card for a channel.",
        extras={
            "category": "🔎 Info & Notes",
            "short": "Info card for a channel",
            "usage": "channelinfo [channel]",
            "desc": "Shows channel type, ID, category, creation date, position, NSFW status, slowmode, and topic.",
            "args": [
                ("channel", "Channel to inspect (default: current channel)"),
            ],
            "perms": "None",
            "example": "!channelinfo #general",
        },
    )
    @app_commands.describe(channel="Channel to inspect (default: current channel)")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def channelinfo(self, ctx, channel: Optional[discord.TextChannel] = None):
        ch = channel or ctx.channel
        type_icons = {
            discord.ChannelType.text: "📝",
            discord.ChannelType.voice: "🔊",
            discord.ChannelType.stage_voice: "🎙️",
            discord.ChannelType.forum: "📋",
            discord.ChannelType.news: "📰",
        }
        icon = type_icons.get(ch.type, "📢")
        e = h.embed(title=f"{icon} #{ch.name}", color=h.BLUE)
        e.add_field(name="🆔 ID", value=f"`{ch.id}`", inline=True)
        e.add_field(
            name="📂 Category",
            value=ch.category.name if ch.category else "_None_",
            inline=True,
        )
        e.add_field(
            name="📅 Created",
            value=discord.utils.format_dt(ch.created_at, style="R"),
            inline=True,
        )
        e.add_field(name="📌 Position", value=str(ch.position), inline=True)
        e.add_field(name="🔞 NSFW", value="Yes" if ch.is_nsfw() else "No", inline=True)
        if hasattr(ch, "slowmode_delay") and ch.slowmode_delay:
            e.add_field(
                name="🐢 Slowmode", value=h.fmt_duration(ch.slowmode_delay), inline=True
            )
        if ch.topic:
            e.add_field(name="📜 Topic", value=ch.topic[:500], inline=False)
        e.set_footer(text="NanoBot")
        e.timestamp = discord.utils.utcnow()
        await ctx.reply(embed=e, ephemeral=True)

    # ══════════════════════════════════════════════════════════════════════════
    #  note / notes / clearnotes
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="note",
        description="Add an internal mod note about a user (invisible to them).",
        extras={
            "category": "🔎 Info & Notes",
            "short": "Add a private mod note (invisible to the user)",
            "usage": "note <user> <content>",
            "desc": "Saves an internal note about a user. The user never sees these.",
            "args": [
                ("user", "User to attach the note to"),
                ("content", "Note content (max 1000 chars)"),
            ],
            "perms": "Manage Messages",
            "example": "!note @user Warned about spam in #general.",
        },
    )
    @app_commands.describe(user="User to attach note to", content="Note content")
    @has_mod_perms()
    async def note(self, ctx, user: discord.Member, *, content: str):
        if len(content) > 1000:
            return await ctx.reply(
                embed=h.err("Note must be 1000 characters or fewer."), ephemeral=True
            )
        count = await db.add_note(
            ctx.guild.id,
            user.id,
            content,
            str(ctx.author.id),
            str(ctx.author),
            datetime.now(timezone.utc).isoformat(),
        )
        log.info(
            f"note: #{count} added for {user} ({user.id}) by {ctx.author} in {ctx.guild}"
        )
        await ctx.reply(
            embed=h.ok(
                f"Note #{count} added for **{user.display_name}**.\n> {content[:300]}",
                "📜 Note Saved",
            ),
            ephemeral=True,
        ),

    extras = (
        {
            "category": "🔎 Info & Notes",
            "short": "View mod notes for a user",
            "usage": "notes <user>",
            "desc": "Shows up to 8 of the most recent mod notes. Only visible to you (ephemeral).",
            "args": [
                ("user", "User to look up"),
            ],
            "perms": "Manage Messages",
            "example": "!notes @user",
        },
    )

    @commands.hybrid_command(name="notes", description="View mod notes for a user.")
    @app_commands.describe(user="User whose notes to view")
    @has_mod_perms()
    async def notes(self, ctx, user: discord.Member):
        user_notes = await db.get_notes(ctx.guild.id, user.id)
        if not user_notes:
            return await ctx.reply(
                embed=h.info(
                    f"No notes on file for **{user.display_name}**.", "📜 Notes"
                ),
                ephemeral=True,
            )
        e = h.embed(title=f"📜 Notes — {user.display_name}", color=h.BLUE)
        for i, n in enumerate(user_notes[-8:], start=max(1, len(user_notes) - 7)):
            e.add_field(
                name=f"#{i}  ·  {n.get('by_name','?')}  ·  {n.get('at','')[:10]}",
                value=n["note"][:300],
                inline=False,
            )
        e.set_footer(
            text=f"Showing {min(8,len(user_notes))}/{len(user_notes)} notes  ·  NanoBot"
        )
        e.timestamp = datetime.now(timezone.utc)
        await ctx.reply(embed=e, ephemeral=True)

    @commands.hybrid_command(
        name="clearnotes",
        description="Delete all mod notes for a user. Admin only.",
        extras={
            "category": "🔎 Info & Notes",
            "short": "Delete all mod notes for a user (admin only)",
            "usage": "clearnotes <user>",
            "desc": "Permanently wipes all mod notes for a user.",
            "args": [
                ("user", "User whose notes to clear"),
            ],
            "perms": "Administrator",
            "example": "!clearnotes @user",
        },
    )
    @app_commands.describe(user="User whose notes to clear")
    @has_admin_perms()
    async def clearnotes(self, ctx, user: discord.Member):
        count = await db.clear_notes(ctx.guild.id, user.id)
        if count:
            log.info(
                f"clearnotes: {count} notes cleared for {user} by {ctx.author} in {ctx.guild}"
            )
            await ctx.reply(
                embed=h.ok(
                    f"Cleared **{count}** note(s) for **{user.display_name}**.",
                    "📜 Notes Cleared",
                ),
                ephemeral=True,
            )
        else:
            await ctx.reply(
                embed=h.info(
                    f"No notes on file for **{user.display_name}**.", "📜 Notes"
                ),
                ephemeral=True,
            )

    # ══════════════════════════════════════════════════════════════════════════
    #  last
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="last",
        description="Show who last sent a message here.",
        extras={
            "category": "🔎 Info & Notes",
            "short": "Show who last sent a message here",
            "usage": "last",
            "desc": "Displays who last sent a message in this channel — the default target for kick, ban, freeze, etc.",
            "args": [],
            "perms": "None",
            "example": "!last",
        },
    )
    async def last(self, ctx):
        target = self.bot.last_senders.get(ctx.channel.id)
        if not target:
            return await ctx.reply(
                embed=h.warn("No recent sender tracked in this channel yet."),
                ephemeral=True,
            )
        e = h.embed(
            title="📦 Last Message Sender",
            description=f"**{target.display_name}**\n{target.mention}\n`{target.id}`",
            color=h.BLUE,
        )
        e.set_thumbnail(url=target.display_avatar.url)
        e.set_footer(
            text="This is who /kick, /freeze, etc. will target with no user specified  ·  NanoBot"
        )
        await ctx.reply(embed=e, ephemeral=True)

    # ══════════════════════════════════════════════════════════════════════════
    #  tempban
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="tempban",
        description="Ban a user for a set duration. Auto-unbans when it expires.",
        extras={
            "category": "🔨 Banning",
            "short": "Timed ban — no message deletion, just a duration",
            "usage": "tempban [user] [duration] [reason]",
            "desc": "Simple timed ban with no history deletion. Auto-unban survives restarts. Defaults to last sender if no user given. vs cban: cban always deletes message history — use it when content needs to be wiped. tempban leaves messages intact.",
            "args": [
                ("user", "Who to ban (blank = last sender)"),
                ("duration", "How long: 1h, 12h, 7d (default 24h, min 1 minute)"),
                ("reason", "Optional reason"),
            ],
            "perms": "Ban Members",
            "example": "!tempban @user 3d Repeated rule violations",
        },
    )
    @app_commands.describe(
        user="Who to ban (blank = last sender)",
        duration="How long — e.g. 1h, 12h, 7d (min 1 minute)",
        reason="Optional reason",
    )
    @has_ban_perms()
    async def tempban(
        self,
        ctx,
        user: Optional[discord.Member] = None,
        duration: str = "24h",
        *,
        reason: Optional[str] = None,
    ):
        target = await resolve_target(self.bot, ctx.channel.id, user)
        if not target:
            return await ctx.reply(
                embed=h.err("No user specified and no recent sender tracked."),
                ephemeral=True,
            )
        if target == ctx.author:
            return await ctx.reply(
                embed=h.err("You can't ban yourself."), ephemeral=True
            )
        if not can_target(ctx.author, target):
            return await ctx.reply(
                embed=h.err(f"**{target.display_name}** outranks you."), ephemeral=True
            )
        if not can_bot_target(ctx.guild.me, target):
            return await ctx.reply(
                embed=h.err(
                    f"I can't ban **{target.display_name}** — their role is at or above mine.\n"
                    "Go to **Server Settings → Roles** and drag my role above theirs."
                ),
                ephemeral=True,
            )

        wait_secs = h.parse_duration(duration)
        if not wait_secs or wait_secs < 60:
            return await ctx.reply(
                embed=h.err(
                    "Invalid duration. Use e.g. `1h`, `12h`, `7d` (minimum 1 minute)."
                ),
                ephemeral=True,
            )

        dm_text = f"You've been temporarily banned from **{ctx.guild.name}** for **{h.fmt_duration(wait_secs)}**."
        if reason:
            dm_text += f"\nReason: {reason}"
        dm_sent = await try_dm(target, dm_text)

        try:
            await ctx.guild.ban(
                target,
                reason=(
                    f"tempban by {ctx.author} ({ctx.author.id}) — {h.fmt_duration(wait_secs)}"
                    + (f": {reason}" if reason else "")
                ),
                delete_message_days=0,
            )
        except discord.Forbidden:
            return await ctx.reply(
                embed=h.err(
                    "I don't have permission to ban — check my role is above the target's "
                    "and that I have the **Ban Members** permission."
                ),
                ephemeral=True,
            )

        await self._schedule_unban(ctx.guild.id, target.id, wait_secs)
        log.info(
            f"tempban: {target} ({target.id}) for {h.fmt_duration(wait_secs)} by {ctx.author} in {ctx.guild}"
        )

        await ctx.reply(
            embed=h.ok(
                f"**{target.display_name}** (`{target.id}`) banned for **{h.fmt_duration(wait_secs)}**.\n"
                f"📨 DM {'sent' if dm_sent else 'failed (closed DMs)'}.\n"
                f"⏱️ Auto-unban scheduled.",
                "⏱️ Temp Ban",
            ),
            ephemeral=True,
        )
        await action_log(
            ctx,
            "⏱️",
            "tempban",
            target=target,
            detail=f"Duration: {h.fmt_duration(wait_secs)}"
            + (f" · {reason}" if reason else ""),
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  nuke
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="nuke",
        description="Clone this channel and delete the original — permanently wipes all messages.",
        extras={
            "category": "📢 Channel Controls",
            "short": "Wipe a channel — clones it then deletes the original",
            "usage": "nuke [reason]",
            "desc": "Recreates the channel with identical settings and permissions, deleting all message history. Requires button confirmation. Cannot be undone.",
            "args": [
                ("reason", "Optional reason (shown in audit log)"),
            ],
            "perms": "Manage Channels",
            "example": "!nuke raid cleanup",
        },
    )
    @app_commands.describe(reason="Optional reason (shown in audit log)")
    @has_channel_perms()
    async def nuke(self, ctx, *, reason: Optional[str] = None):
        view = NukeConfirm(ctx.author)
        msg = await ctx.reply(
            embed=h.warn(
                f"⚠️ **This will permanently delete ALL messages** in {ctx.channel.mention}.\n"
                f"The channel will be recreated with identical settings.\n\n"
                f"_Only {ctx.author.mention} can confirm. Expires in 30 seconds._",
                "💥 Nuke — Confirm",
            ),
            ephemeral=True,
            view=view,
        )
        view.message = msg
        await view.wait()

        if not view.outcome:
            return  # cancelled or timed out

        channel = ctx.channel
        pos = channel.position
        rsn = f"nuke by {ctx.author} ({ctx.author.id})" + (
            f": {reason}" if reason else ""
        )

        try:
            new_ch = await channel.clone(reason=rsn)
            await new_ch.edit(position=pos)
            await channel.delete(reason=rsn)
        except discord.Forbidden:
            return  # can't send anything — channel was already gone or perms changed

        log.warning(
            f"nuke: #{channel.name} ({channel.id}) by {ctx.author} in {ctx.guild}"
        )

        e = h.ok(
            f"Channel nuked by **{ctx.author.display_name}**."
            + (f"\n📝 {reason}" if reason else ""),
            "💥 Nuked",
        )
        e.set_footer(text="NanoBot · All previous messages have been deleted.")
        await new_ch.send(embed=e)

    # ══════════════════════════════════════════════════════════════════════════
    #  hide / unhide
    # ══════════════════════════════════════════════════════════════════════════,
    extras = (
        {
            "category": "📢 Channel Controls",
            "short": "Hide a channel from @everyone",
            "usage": "hide [channel]",
            "desc": "Sets view_channel=False for @everyone on the target channel.",
            "args": [
                ("channel", "Channel to hide (default: current channel)"),
            ],
            "perms": "Manage Channels",
            "example": "!hide #staff-only",
        },
    )

    @commands.hybrid_command(name="hide", description="Hide a channel from @everyone.")
    @app_commands.describe(channel="Channel to hide (default: current)")
    @has_channel_perms()
    async def hide(self, ctx, channel: Optional[discord.TextChannel] = None):
        target = channel or ctx.channel
        everyone = ctx.guild.default_role
        ow = target.overwrites_for(everyone)

        if ow.view_channel is False:
            return await ctx.reply(
                embed=h.info(
                    f"{target.mention} is already hidden from @everyone.",
                    "👁️ Already Hidden",
                ),
                ephemeral=True,
            )

        ow.view_channel = False
        await target.set_permissions(
            everyone, overwrite=ow, reason=f"hide by {ctx.author}"
        )
        log.info(f"hide: #{target.name} by {ctx.author} in {ctx.guild}")
        await ctx.reply(
            embed=h.ok(
                f"{target.mention} is now **hidden** from @everyone. 🙈", "👁️ Hidden"
            ),
            ephemeral=True,
        )
        await action_log(ctx, "🙈", "hide", detail=f"in #{target.name}")

    @commands.hybrid_command(
        name="unhide",
        description="Restore @everyone visibility on a hidden channel.",
        extras={
            "category": "📢 Channel Controls",
            "short": "Restore @everyone visibility on a hidden channel",
            "usage": "unhide [channel]",
            "desc": "Resets the view_channel override for @everyone.",
            "args": [
                ("channel", "Channel to unhide (default: current channel)"),
            ],
            "perms": "Manage Channels",
            "example": "!unhide #announcements",
        },
    )
    @app_commands.describe(channel="Channel to unhide (default: current)")
    @has_channel_perms()
    async def unhide(self, ctx, channel: Optional[discord.TextChannel] = None):
        target = channel or ctx.channel
        everyone = ctx.guild.default_role
        ow = target.overwrites_for(everyone)

        if ow.view_channel is not False:
            return await ctx.reply(
                embed=h.info(
                    f"{target.mention} isn't hidden from @everyone.", "👁️ Not Hidden"
                ),
                ephemeral=True,
            )

        ow.view_channel = None
        await target.set_permissions(
            everyone, overwrite=ow, reason=f"unhide by {ctx.author}"
        )
        log.info(f"unhide: #{target.name} by {ctx.author} in {ctx.guild}")
        await ctx.reply(
            embed=h.ok(
                f"{target.mention} is now **visible** to @everyone. 👁️", "👁️ Unhidden"
            ),
            ephemeral=True,
        )
        await action_log(ctx, "👁️", "unhide", detail=f"in #{target.name}")

    # ══════════════════════════════════════════════════════════════════════════
    #  echo
    # ══════════════════════════════════════════════════════════════════════════,
    extras = (
        {
            "category": "📢 Channel Controls",
            "short": "Send a message as NanoBot",
            "usage": "echo [channel] <message>",
            "desc": "Posts a message in the current or specified channel. Prefix mode deletes your trigger message for a cleaner look.",
            "args": [
                ("channel", "Where to send it (default: current channel)"),
                ("message", "The text to send"),
            ],
            "perms": "Manage Messages",
            "example": "!echo #announcements Server maintenance in 10 minutes!",
        },
    )

    @commands.hybrid_command(name="echo", description="Send a message as NanoBot.")
    @app_commands.describe(
        message="The message to send",
        channel="Where to send it (default: current channel)",
    )
    @has_mod_perms()
    async def echo(
        self, ctx, channel: Optional[discord.TextChannel] = None, *, message: str
    ):
        target = channel or ctx.channel
        try:
            await target.send(message)
        except discord.Forbidden:
            return await ctx.reply(
                embed=h.err(f"I can't send messages in {target.mention}."),
                ephemeral=True,
            )

        log.info(f"echo: by {ctx.author} in #{target} / {ctx.guild}: {message[:100]}")

        if target != ctx.channel:
            await ctx.reply(
                embed=h.ok(f"Message sent in {target.mention}.", "📢 Sent"),
                ephemeral=True,
            )
        elif ctx.interaction:
            # Slash: acknowledge silently since the message is already visible
            await ctx.reply(embed=h.ok("Message sent.", "📢 Sent"), ephemeral=True)
        else:
            # Prefix: delete the trigger message so only the echo is visible
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass

    # ══════════════════════════════════════════════════════════════════════════
    #  moveall
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="moveall",
        description="Move all members from one voice channel to another.",
        extras={
            "category": "📢 Channel Controls",
            "short": "Move all VC members from one channel to another",
            "usage": "moveall <to_channel> [from_channel]",
            "desc": "Moves every member from the source VC to the destination. If no source is given, uses your current voice channel.",
            "args": [
                ("to_channel", "Destination voice channel"),
                ("from_channel", "Source voice channel (blank = your current VC)"),
            ],
            "perms": "Move Members",
            "example": "!moveall #General",
        },
    )
    @app_commands.describe(
        to_channel="Destination voice channel",
        from_channel="Source voice channel (default: your current VC)",
    )
    @has_move_perms()
    async def moveall(
        self,
        ctx,
        to_channel: discord.VoiceChannel,
        from_channel: Optional[discord.VoiceChannel] = None,
    ):
        source = from_channel
        if not source:
            if ctx.author.voice and ctx.author.voice.channel:
                source = ctx.author.voice.channel
            else:
                return await ctx.reply(
                    embed=h.err(
                        "Specify a source channel or join a voice channel first."
                    ),
                    ephemeral=True,
                )

        if source == to_channel:
            return await ctx.reply(
                embed=h.err("Source and destination are the same channel."),
                ephemeral=True,
            )

        members = list(source.members)
        if not members:
            return await ctx.reply(
                embed=h.info(
                    f"{source.mention} has no members to move.", "🔊 Empty Channel"
                ),
                ephemeral=True,
            )

        await ctx.defer(ephemeral=True)
        moved, failed = 0, 0
        for member in members:
            try:
                await member.move_to(to_channel, reason=f"moveall by {ctx.author}")
                moved += 1
            except discord.HTTPException:
                failed += 1

        log.info(
            f"moveall: {moved} moved from #{source} → #{to_channel} by {ctx.author} in {ctx.guild}"
        )
        parts = [
            f"Moved **{moved}** member(s) from {source.mention} → {to_channel.mention}."
        ]
        if failed:
            parts.append(f"⚠️ Failed to move {failed}.")
        await ctx.send(embed=h.ok(" ".join(parts), "🔊 Members Moved"), ephemeral=True)
        await action_log(
            ctx,
            "🔊",
            "moveall",
            detail=f"{moved} from #{source.name} → #{to_channel.name}",
        )


async def setup(bot):
    await bot.add_cog(Moderation(bot))
