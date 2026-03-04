"""
cogs/moderation.py
Core moderation commands — designed for speed on mobile.

Every action produces two messages:
  • Ephemeral reply to the mod  — full details (only they see it)
  • Public channel embed        — brief action log visible to the server

Commands:
  cban / cleanban  — ban + purge history + optional timed unban + DM
  ban              — permanent ban + DM
  unban            — unban by user ID
  kick             — kick + DM
  slow             — toggle / set slowmode (optional timed auto-disable)
  lock             — toggle channel lock for @everyone
  purge            — delete last X messages (optionally by user)
  freeze           — Discord timeout (temp mute)
  unfreeze         — remove a timeout early
  whois            — mobile-friendly user info card
  note             — add a mod note (JSON)
  notes            — view mod notes for a user
  clearnotes       — wipe all notes for a user
  last             — show who last sent a message here
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import helpers as h
from utils import storage

log = logging.getLogger("NanoBot.moderation")


# ── Helpers ────────────────────────────────────────────────────────────────────

async def resolve_target(
    bot: commands.Bot,
    channel_id: int,
    explicit: Optional[discord.Member],
) -> discord.Member | None:
    return explicit if explicit else bot.last_senders.get(channel_id)


async def try_dm(member: discord.Member, content: str) -> bool:
    """DM a user. Returns True if delivered."""
    try:
        await member.send(content)
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


def can_target(actor: discord.Member, target: discord.Member) -> bool:
    if actor == actor.guild.owner:
        return True
    return actor.top_role > target.top_role


async def action_log(
    ctx: commands.Context,
    emoji: str,
    action: str,
    *,
    target: discord.Member | None = None,
    detail: str = "",
):
    """
    Send a short PUBLIC embed to the channel logging what just happened.
    Separate from the ephemeral confirmation sent to the mod.

    Format:  🔨  ModName  used  ban  on  TargetName (ID)
    """
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


# ══════════════════════════════════════════════════════════════════════════════
class Moderation(commands.Cog):
    """Mobile-first moderation commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._slow_tasks:  dict[int, asyncio.Task]  = {}
        self._unban_tasks: dict[str, asyncio.Task]  = {}

    # ── Restore scheduled tasks after restart ──────────────────────────────────
    @commands.Cog.listener()
    async def on_restore_schedules(self):
        await asyncio.gather(
            self._restore_unban_schedules(),
            self._restore_slow_schedules(),
        )

    async def _restore_unban_schedules(self):
        data    = storage.read("unban_schedules.json")
        now     = datetime.now(timezone.utc).timestamp()
        cleaned = {}
        for key, info in data.items():
            remaining = info["until"] - now
            guild_id  = int(info["guild_id"])
            user_id   = int(info["user_id"])
            if remaining > 0:
                task = asyncio.create_task(self._auto_unban(guild_id, user_id, remaining))
                self._unban_tasks[key] = task
                cleaned[key] = info
            else:
                guild = self.bot.get_guild(guild_id)
                if guild:
                    try:
                        await guild.unban(discord.Object(id=user_id),
                                          reason="NanoBot: Timed unban (overdue from restart)")
                        log.info(f"Overdue unban: {user_id} in {guild_id}")
                    except discord.NotFound:
                        pass
        storage.write("unban_schedules.json", cleaned)

    async def _restore_slow_schedules(self):
        data    = storage.read("slow_schedules.json")
        now     = datetime.now(timezone.utc).timestamp()
        cleaned = {}
        for cid_str, info in data.items():
            remaining  = info["until"] - now
            channel_id = int(cid_str)
            if remaining > 0:
                task = asyncio.create_task(self._auto_unslow(channel_id, remaining))
                self._slow_tasks[channel_id] = task
                cleaned[cid_str] = info
            else:
                ch = self.bot.get_channel(channel_id)
                if ch:
                    try:
                        await ch.edit(slowmode_delay=0)
                    except discord.Forbidden:
                        pass
        storage.write("slow_schedules.json", cleaned)

    # ── Background task runners ────────────────────────────────────────────────
    async def _auto_unban(self, guild_id: int, user_id: int, delay: float):
        await asyncio.sleep(delay)
        guild = self.bot.get_guild(guild_id)
        if guild:
            try:
                await guild.unban(discord.Object(id=user_id),
                                  reason="NanoBot: Timed unban complete")
                log.info(f"Timed unban: {user_id} in {guild_id}")
            except discord.NotFound:
                pass
        key = f"{guild_id}:{user_id}"
        self._unban_tasks.pop(key, None)
        data = storage.read("unban_schedules.json")
        data.pop(key, None)
        storage.write("unban_schedules.json", data)

    async def _auto_unslow(self, channel_id: int, delay: float):
        await asyncio.sleep(delay)
        ch = self.bot.get_channel(channel_id)
        if ch:
            try:
                await ch.edit(slowmode_delay=0, reason="NanoBot: Timed slowmode expired")
                log.info(f"Timed slowmode removed: #{ch}")
            except discord.Forbidden:
                pass
        self._slow_tasks.pop(channel_id, None)
        data = storage.read("slow_schedules.json")
        data.pop(str(channel_id), None)
        storage.write("slow_schedules.json", data)

    def _schedule_unban(self, guild_id: int, user_id: int, delay: float):
        key = f"{guild_id}:{user_id}"
        if key in self._unban_tasks:
            self._unban_tasks[key].cancel()
        data = storage.read("unban_schedules.json")
        data[key] = {
            "until":    datetime.now(timezone.utc).timestamp() + delay,
            "guild_id": guild_id,
            "user_id":  user_id,
        }
        storage.write("unban_schedules.json", data)
        self._unban_tasks[key] = asyncio.create_task(
            self._auto_unban(guild_id, user_id, delay)
        )

    def _schedule_unslow(self, channel_id: int, guild_id: int, delay: float):
        if channel_id in self._slow_tasks:
            self._slow_tasks[channel_id].cancel()
        data = storage.read("slow_schedules.json")
        data[str(channel_id)] = {
            "until":    datetime.now(timezone.utc).timestamp() + delay,
            "guild_id": guild_id,
        }
        storage.write("slow_schedules.json", data)
        self._slow_tasks[channel_id] = asyncio.create_task(
            self._auto_unslow(channel_id, delay)
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  cban / cleanban
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="cban", aliases=["cleanban"],
        description="Ban + delete message history. Optional timed unban & DM.",
    )
    @app_commands.describe(
        user    = "Who to ban (blank = last message sender)",
        days    = "Days of history to delete (1–7, default 7)",
        wait    = "Auto-unban after e.g. 1h 30m 7d (omit for permanent)",
        message = "DM sent to the user (omit for default)",
    )
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def cban(
        self,
        ctx:     commands.Context,
        user:    Optional[discord.Member] = None,
        days:    Optional[int]            = 7,
        wait:    Optional[str]            = None,
        *,
        message: Optional[str]            = None,
    ):
        target = await resolve_target(self.bot, ctx.channel.id, user)
        if not target:
            return await ctx.reply(
                embed=h.err("No user specified and no recent sender tracked. Mention someone!"),
                ephemeral=True,
            )
        if target == ctx.author:
            return await ctx.reply(embed=h.err("You can't ban yourself."), ephemeral=True)
        if not can_target(ctx.author, target):
            return await ctx.reply(
                embed=h.err(f"You can't ban **{target.display_name}** — they outrank you."),
                ephemeral=True,
            )

        days      = max(1, min(7, days or 7))
        wait_secs = h.parse_duration(wait)
        is_timed  = wait_secs is not None

        dm_text = message or (
            f"You've been temporarily banned from **{ctx.guild.name}**.\n"
            f"You can rejoin after **{h.fmt_duration(wait_secs)}**."
            if is_timed else
            f"You've been banned from **{ctx.guild.name}**."
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
                embed=h.err("I don't have permission to ban that user. Check my role position."),
                ephemeral=True,
            )

        if is_timed:
            self._schedule_unban(ctx.guild.id, target.id, wait_secs)

        # ── Ephemeral confirmation to mod ──────────────────────────────────────
        detail_lines = [
            f"🗂️ Deleted **{days} day(s)** of message history.",
            f"📨 DM {'sent' if dm_sent else 'failed (closed DMs)'}.",
        ]
        if is_timed:
            detail_lines.append(f"⏱️ Auto-unban in **{h.fmt_duration(wait_secs)}**.")

        title = f"🔨 {'Timed ' if is_timed else ''}Clean Ban"
        await ctx.reply(
            embed=h.ok(
                f"**{target.display_name}** (`{target.id}`) has been banned.\n"
                + "\n".join(detail_lines),
                title,
            ),
            ephemeral=True,
        )

        # ── Public action log ──────────────────────────────────────────────────
        log_detail = f"🗂️ {days}d history deleted"
        if is_timed:
            log_detail += f" · ⏱️ unban in {h.fmt_duration(wait_secs)}"
        await action_log(ctx, "🔨", "cban", target=target, detail=log_detail)

    # ══════════════════════════════════════════════════════════════════════════
    #  ban
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="ban",
        description="Permanently ban a user with an optional DM.",
    )
    @app_commands.describe(
        user    = "Who to ban (blank = last message sender)",
        message = "DM sent to the user (omit for default)",
    )
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def ban(
        self,
        ctx:     commands.Context,
        user:    Optional[discord.Member] = None,
        *,
        message: Optional[str]            = None,
    ):
        target = await resolve_target(self.bot, ctx.channel.id, user)
        if not target:
            return await ctx.reply(
                embed=h.err("No user specified and no recent sender tracked."),
                ephemeral=True,
            )
        if target == ctx.author:
            return await ctx.reply(embed=h.err("You can't ban yourself."), ephemeral=True)
        if not can_target(ctx.author, target):
            return await ctx.reply(
                embed=h.err(f"You can't ban **{target.display_name}** — they outrank you."),
                ephemeral=True,
            )

        dm_text = message or f"You've been banned from **{ctx.guild.name}**."
        dm_sent = await try_dm(target, dm_text)

        try:
            await ctx.guild.ban(
                target,
                reason=f"ban by {ctx.author} ({ctx.author.id})",
                delete_message_days=0,
            )
        except discord.Forbidden:
            return await ctx.reply(
                embed=h.err("I don't have permission to ban that user. Check my role position."),
                ephemeral=True,
            )

        await ctx.reply(
            embed=h.ok(
                f"**{target.display_name}** (`{target.id}`) permanently banned.\n"
                f"📨 DM {'sent' if dm_sent else 'failed (closed DMs)'}.",
                "🔨 Banned",
            ),
            ephemeral=True,
        )
        await action_log(ctx, "🔨", "ban", target=target)

    # ══════════════════════════════════════════════════════════════════════════
    #  unban
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="unban",
        description="Unban a user by their User ID.",
    )
    @app_commands.describe(
        user_id = "Discord User ID (enable Dev Mode → right-click → Copy ID)",
        reason  = "Optional reason",
    )
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def unban(
        self,
        ctx:     commands.Context,
        user_id: str,
        *,
        reason:  Optional[str] = None,
    ):
        try:
            uid = int(user_id.strip())
        except ValueError:
            return await ctx.reply(
                embed=h.err("That doesn't look like a valid User ID (numbers only)."),
                ephemeral=True,
            )

        try:
            await ctx.guild.unban(
                discord.Object(id=uid),
                reason=reason or f"unban by {ctx.author} ({ctx.author.id})",
            )
        except discord.NotFound:
            return await ctx.reply(embed=h.err(f"User `{uid}` is not currently banned."), ephemeral=True)
        except discord.Forbidden:
            return await ctx.reply(embed=h.err("I don't have permission to unban."), ephemeral=True)

        # Cancel pending timed unban if any
        key = f"{ctx.guild.id}:{uid}"
        if key in self._unban_tasks:
            self._unban_tasks[key].cancel()
            self._unban_tasks.pop(key, None)
            d = storage.read("unban_schedules.json")
            d.pop(key, None)
            storage.write("unban_schedules.json", d)

        await ctx.reply(embed=h.ok(f"User `{uid}` has been unbanned.", "✅ Unbanned"), ephemeral=True)
        await action_log(ctx, "✅", "unban", detail=f"User ID: `{uid}`")

    # ══════════════════════════════════════════════════════════════════════════
    #  kick
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="kick",
        description="Kick a user with an optional DM. Defaults to last message sender.",
    )
    @app_commands.describe(
        user    = "Who to kick (blank = last message sender)",
        message = "DM sent to the user (omit for default)",
    )
    @commands.has_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def kick(
        self,
        ctx:     commands.Context,
        user:    Optional[discord.Member] = None,
        *,
        message: Optional[str]            = None,
    ):
        target = await resolve_target(self.bot, ctx.channel.id, user)
        if not target:
            return await ctx.reply(
                embed=h.err("No user specified and no recent sender tracked."),
                ephemeral=True,
            )
        if target == ctx.author:
            return await ctx.reply(embed=h.err("You can't kick yourself."), ephemeral=True)
        if not can_target(ctx.author, target):
            return await ctx.reply(
                embed=h.err(f"You can't kick **{target.display_name}** — they outrank you."),
                ephemeral=True,
            )

        dm_text = message or f"You've been kicked from **{ctx.guild.name}** but can rejoin at any time."
        dm_sent = await try_dm(target, dm_text)

        try:
            await ctx.guild.kick(target, reason=f"kick by {ctx.author} ({ctx.author.id})")
        except discord.Forbidden:
            return await ctx.reply(
                embed=h.err("I don't have permission to kick that user."),
                ephemeral=True,
            )

        await ctx.reply(
            embed=h.ok(
                f"**{target.display_name}** (`{target.id}`) has been kicked.\n"
                f"📨 DM {'sent' if dm_sent else 'failed (closed DMs)'}.",
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
        description="Toggle slowmode. No args = toggle on/off. Add delay and optional auto-disable timer.",
    )
    @app_commands.describe(
        delay  = "Slowmode delay e.g. 30s 2m 5m (max 5 min). Omit to toggle.",
        length = "Auto-disable after e.g. 10m 1h 3d (max 7 days). Omit for indefinite.",
    )
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def slow(
        self,
        ctx:    commands.Context,
        delay:  Optional[str] = None,
        length: Optional[str] = None,
    ):
        channel = ctx.channel
        current = channel.slowmode_delay

        if delay is None:
            if current > 0:
                await channel.edit(slowmode_delay=0, reason=f"slow toggle off by {ctx.author}")
                if channel.id in self._slow_tasks:
                    self._slow_tasks[channel.id].cancel()
                    self._slow_tasks.pop(channel.id, None)
                await ctx.reply(
                    embed=h.ok(f"Slowmode disabled in {channel.mention}.", "🐢 Slowmode Off"),
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
                embed=h.err("Discord's maximum slowmode is **5 minutes** (300s)."),
                ephemeral=True,
            )

        length_secs = None
        if length:
            length_secs = h.parse_duration(length)
            if not length_secs or length_secs < 60:
                return await ctx.reply(
                    embed=h.err("Invalid length. Use `10m`, `1h`, `3d`, etc. (min 1 minute)."),
                    ephemeral=True,
                )
            if length_secs > 7 * 86400:
                return await ctx.reply(
                    embed=h.err("Maximum timed slowmode duration is 7 days."),
                    ephemeral=True,
                )

        await channel.edit(slowmode_delay=delay_secs, reason=f"slow by {ctx.author}")

        desc = f"Slowmode set to **{h.fmt_duration(delay_secs)}** in {channel.mention}."
        if length_secs:
            desc += f"\n⏱️ Auto-disables in **{h.fmt_duration(length_secs)}**."
            self._schedule_unslow(channel.id, ctx.guild.id, length_secs)
        else:
            desc += "\n_Use `/slow` with no args to toggle off._"

        await ctx.reply(embed=h.ok(desc, "🐢 Slowmode On"), ephemeral=True)
        log_detail = h.fmt_duration(delay_secs)
        if length_secs:
            log_detail += f" · auto-off in {h.fmt_duration(length_secs)}"
        await action_log(ctx, "🐢", "slow", detail=f"{log_detail} in {channel.mention}")

    # ══════════════════════════════════════════════════════════════════════════
    #  lock
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="lock",
        description="Toggle @everyone channel lock. Run again to unlock.",
    )
    @app_commands.describe(
        channel = "Channel to lock (default: current channel)",
        reason  = "Optional reason (shown in audit log)",
    )
    @commands.has_permissions(manage_channels=True)
    @commands.bot_has_permissions(manage_channels=True)
    async def lock(
        self,
        ctx:     commands.Context,
        channel: Optional[discord.TextChannel] = None,
        *,
        reason:  Optional[str] = None,
    ):
        target   = channel or ctx.channel
        everyone = ctx.guild.default_role
        ow       = target.overwrites_for(everyone)
        is_locked = ow.send_messages is False

        if is_locked:
            ow.send_messages = None
            await target.set_permissions(everyone, overwrite=ow, reason=f"unlock by {ctx.author}")
            await ctx.reply(embed=h.ok(f"{target.mention} is now **unlocked**. 🔓", "🔓 Unlocked"), ephemeral=True)
            await action_log(ctx, "🔓", "unlock", detail=f"in {target.mention}")
        else:
            ow.send_messages = False
            await target.set_permissions(everyone, overwrite=ow, reason=reason or f"lock by {ctx.author}")
            desc = f"{target.mention} is now **locked** for @everyone."
            if reason:
                desc += f"\n📝 {reason}"
            await ctx.reply(embed=h.ok(desc, "🔒 Locked"), ephemeral=True)
            log_detail = f"in {target.mention}" + (f" · {reason}" if reason else "")
            await action_log(ctx, "🔒", "lock", detail=log_detail)

    # ══════════════════════════════════════════════════════════════════════════
    #  purge
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="purge",
        description="Bulk delete the last X messages (1–100). Optionally filter by user.",
    )
    @app_commands.describe(
        amount = "Number of messages to delete (1–100)",
        user   = "Only delete messages from this user (optional)",
    )
    @commands.has_permissions(manage_messages=True)
    @commands.bot_has_permissions(manage_messages=True)
    async def purge(
        self,
        ctx:    commands.Context,
        amount: int,
        user:   Optional[discord.Member] = None,
    ):
        if not 1 <= amount <= 100:
            return await ctx.reply(embed=h.err("Amount must be between **1** and **100**."), ephemeral=True)

        await ctx.defer(ephemeral=True)

        check   = (lambda m: m.author == user) if user else None
        deleted = await ctx.channel.purge(limit=amount + 1, check=check, bulk=True)
        count   = max(0, len(deleted) - 1)

        desc = f"Deleted **{count}** message{'s' if count != 1 else ''}."
        if user:
            desc += f"\n👤 Filtered to: {user.mention}"

        await ctx.send(embed=h.ok(desc, "🗑️ Purged"), ephemeral=True)
        log_detail = f"{count} message{'s' if count != 1 else ''}"
        if user:
            log_detail += f" from {user.display_name}"
        await action_log(ctx, "🗑️", "purge", detail=log_detail)

    # ══════════════════════════════════════════════════════════════════════════
    #  freeze
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="freeze",
        description="Timeout a user (default 10m). They can't speak, react, or join VCs.",
    )
    @app_commands.describe(
        user     = "Who to freeze (blank = last message sender)",
        duration = "How long e.g. 5m 1h 1d (max 28 days, default 10m)",
        reason   = "Optional reason",
    )
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def freeze(
        self,
        ctx:      commands.Context,
        user:     Optional[discord.Member] = None,
        duration: Optional[str]            = "10m",
        *,
        reason:   Optional[str]            = None,
    ):
        target = await resolve_target(self.bot, ctx.channel.id, user)
        if not target:
            return await ctx.reply(
                embed=h.err("No user specified and no recent sender tracked."),
                ephemeral=True,
            )
        if target == ctx.author:
            return await ctx.reply(embed=h.err("You can't freeze yourself."), ephemeral=True)
        if not can_target(ctx.author, target):
            return await ctx.reply(
                embed=h.err(f"You can't timeout **{target.display_name}** — they outrank you."),
                ephemeral=True,
            )

        secs = h.parse_duration(duration or "10m")
        if not secs or secs < 1:
            return await ctx.reply(embed=h.err("Invalid duration. Try `5m`, `1h`, `1d`."), ephemeral=True)
        if secs > 28 * 86400:
            return await ctx.reply(embed=h.err("Maximum timeout is **28 days**."), ephemeral=True)

        until = discord.utils.utcnow() + timedelta(seconds=secs)
        try:
            await target.timeout(until, reason=reason or f"freeze by {ctx.author}")
        except discord.Forbidden:
            return await ctx.reply(
                embed=h.err("I can't timeout that user. Check my role position."),
                ephemeral=True,
            )

        desc = f"**{target.display_name}** is frozen for **{h.fmt_duration(secs)}**."
        if reason:
            desc += f"\n📝 {reason}"
        await ctx.reply(embed=h.ok(desc, "🧊 Frozen"), ephemeral=True)
        await action_log(
            ctx, "🧊", "freeze", target=target,
            detail=h.fmt_duration(secs) + (f" · {reason}" if reason else ""),
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  unfreeze
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="unfreeze",
        description="Remove a timeout from a user early.",
    )
    @app_commands.describe(user="User to unfreeze")
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def unfreeze(self, ctx: commands.Context, user: discord.Member):
        if not user.timed_out_until:
            return await ctx.reply(
                embed=h.warn(f"**{user.display_name}** is not currently frozen."),
                ephemeral=True,
            )
        try:
            await user.timeout(None, reason=f"unfreeze by {ctx.author}")
        except discord.Forbidden:
            return await ctx.reply(embed=h.err("I can't remove that user's timeout."), ephemeral=True)

        await ctx.reply(embed=h.ok(f"**{user.display_name}** has been unfrozen.", "🌡️ Unfrozen"), ephemeral=True)
        await action_log(ctx, "🌡️", "unfreeze", target=user)

    # ══════════════════════════════════════════════════════════════════════════
    #  whois
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="whois",
        description="Quick user info card — designed for mobile readability.",
    )
    @app_commands.describe(user="User to inspect (leave blank for yourself)")
    async def whois(self, ctx: commands.Context, user: Optional[discord.Member] = None):
        target = user or ctx.author
        now    = discord.utils.utcnow()

        created = discord.utils.format_dt(target.created_at, style="R")
        joined  = discord.utils.format_dt(target.joined_at, style="R") if target.joined_at else "Unknown"

        roles     = [r for r in reversed(target.roles) if r != ctx.guild.default_role]
        roles_str = " ".join(r.mention for r in roles[:6])
        if len(roles) > 6:
            roles_str += f"\n_+{len(roles) - 6} more_"
        if not roles:
            roles_str = "_None_"

        color = target.color.value if target.color != discord.Color.default() else h.GREY
        e = discord.Embed(title=f"👁️ {target.display_name}", color=color)
        e.set_thumbnail(url=target.display_avatar.url)

        e.add_field(name="🆔 User ID",      value=f"`{target.id}`",              inline=True)
        e.add_field(name="🤖 Bot",           value="Yes" if target.bot else "No", inline=True)
        e.add_field(name="\u200b",           value="\u200b",                      inline=True)
        e.add_field(name="📅 Joined Server", value=joined,                        inline=True)
        e.add_field(name="📅 Account Age",   value=created,                       inline=True)
        e.add_field(name="\u200b",           value="\u200b",                      inline=True)
        e.add_field(name=f"🎭 Roles ({len(roles)})", value=roles_str,             inline=False)

        status_parts = []
        if target.timed_out_until and target.timed_out_until > now:
            status_parts.append(f"🧊 Frozen until {discord.utils.format_dt(target.timed_out_until, style='R')}")
        if target.premium_since:
            status_parts.append(f"💎 Boosting since {discord.utils.format_dt(target.premium_since, style='R')}")
        if status_parts:
            e.add_field(name="📌 Status", value="\n".join(status_parts), inline=False)

        flags  = target.public_flags
        badges = []
        if flags.staff:                  badges.append("🛡️ Discord Staff")
        if flags.partner:                badges.append("🤝 Partner")
        if flags.hypesquad:              badges.append("🏠 HypeSquad")
        if flags.bug_hunter:             badges.append("🐛 Bug Hunter")
        if flags.early_supporter:        badges.append("🏷️ Early Supporter")
        if flags.verified_bot_developer: badges.append("🔧 Bot Dev")
        if flags.active_developer:       badges.append("💻 Active Dev")
        if badges:
            e.add_field(name="🏅 Badges", value=" · ".join(badges), inline=False)

        notes = storage.read("notes.json").get(str(ctx.guild.id), {}).get(str(target.id), [])
        if notes:
            e.add_field(name="📜 Mod Notes", value=f"{len(notes)} note(s) on file.", inline=False)

        e.set_footer(text=f"NanoBot · {target.name}")
        e.timestamp = now
        await ctx.reply(embed=e, ephemeral=True)

    # ══════════════════════════════════════════════════════════════════════════
    #  note / notes / clearnotes
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="note",
        description="Add an internal mod note about a user (invisible to them).",
    )
    @app_commands.describe(user="User to attach note to", content="Note content")
    @commands.has_permissions(manage_messages=True)
    async def note(self, ctx: commands.Context, user: discord.Member, *, content: str):
        if len(content) > 1000:
            return await ctx.reply(embed=h.err("Note must be 1000 characters or fewer."), ephemeral=True)

        data = storage.read("notes.json")
        gid  = str(ctx.guild.id)
        uid  = str(user.id)
        data.setdefault(gid, {}).setdefault(uid, []).append({
            "note":    content,
            "by_id":   str(ctx.author.id),
            "by_name": str(ctx.author),
            "at":      datetime.now(timezone.utc).isoformat(),
        })
        storage.write("notes.json", data)

        count = len(data[gid][uid])
        await ctx.reply(
            embed=h.ok(f"Note #{count} added for **{user.display_name}**.\n> {content[:300]}", "📜 Note Saved"),
            ephemeral=True,
        )

    @commands.hybrid_command(name="notes", description="View mod notes for a user.")
    @app_commands.describe(user="User whose notes to view")
    @commands.has_permissions(manage_messages=True)
    async def notes(self, ctx: commands.Context, user: discord.Member):
        data       = storage.read("notes.json")
        user_notes = data.get(str(ctx.guild.id), {}).get(str(user.id), [])

        if not user_notes:
            return await ctx.reply(
                embed=h.info(f"No notes on file for **{user.display_name}**.", "📜 Notes"),
                ephemeral=True,
            )

        e = h.embed(title=f"📜 Notes — {user.display_name}", color=h.BLUE)
        for i, n in enumerate(user_notes[-8:], start=max(1, len(user_notes) - 7)):
            date = n.get("at", "")[:10]
            e.add_field(
                name  = f"#{i}  ·  {n.get('by_name', '?')}  ·  {date}",
                value = n["note"][:300],
                inline= False,
            )
        total = len(user_notes)
        shown = min(8, total)
        e.set_footer(text=f"Showing {shown}/{total} notes  ·  NanoBot")
        e.timestamp = datetime.now(timezone.utc)
        await ctx.reply(embed=e, ephemeral=True)

    @commands.hybrid_command(name="clearnotes", description="Delete all mod notes for a user. Admin only.")
    @app_commands.describe(user="User whose notes to clear")
    @commands.has_permissions(administrator=True)
    async def clearnotes(self, ctx: commands.Context, user: discord.Member):
        data = storage.read("notes.json")
        gid  = str(ctx.guild.id)
        uid  = str(user.id)
        if gid in data and uid in data[gid]:
            count = len(data[gid][uid])
            del data[gid][uid]
            storage.write("notes.json", data)
            await ctx.reply(
                embed=h.ok(f"Cleared **{count}** note(s) for **{user.display_name}**.", "📜 Notes Cleared"),
                ephemeral=True,
            )
        else:
            await ctx.reply(
                embed=h.info(f"No notes on file for **{user.display_name}**.", "📜 Notes"),
                ephemeral=True,
            )

    # ══════════════════════════════════════════════════════════════════════════
    #  last
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="last",
        description="Show who last sent a message here — this is who no-arg commands will target.",
    )
    async def last(self, ctx: commands.Context):
        target = self.bot.last_senders.get(ctx.channel.id)
        if not target:
            return await ctx.reply(
                embed=h.warn("No recent sender tracked in this channel yet."),
                ephemeral=True,
            )
        e = h.embed(
            title       = "📦 Last Message Sender",
            description = f"**{target.display_name}**\n{target.mention}\n`{target.id}`",
            color       = h.BLUE,
        )
        e.set_thumbnail(url=target.display_avatar.url)
        e.set_footer(text="This is who /kick, /freeze, etc. will target with no user specified  ·  NanoBot")
        await ctx.reply(embed=e, ephemeral=True)


# ── Registration ───────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
