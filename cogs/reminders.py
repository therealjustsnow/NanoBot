"""
cogs/reminders.py
Reminder system — set a reminder for yourself or another user.

Reminders survive bot restarts. Overdue reminders fire immediately on restore.
Max 25 active reminders per user. Min 1 minute, max 1 year.

──────────────────────────────────────────────────────
Commands
──────────────────────────────────────────────────────
  /remindme  <text> <time> [dm]
  /remind    @user  <text> <time> [dm]
  /reminders                           → list your active reminders
  /reminders cancel <id>               → cancel one

Prefix shorthands:
  !remindme do this 8h
  !remindme do this in 8 hours
  !remind @user do that 1h
  !reminders
  !reminders cancel abc123

──────────────────────────────────────────────────────
Storage  (data/reminders.json)
──────────────────────────────────────────────────────
Flat dict keyed by reminder ID:
  {
    "abc123": {
      "id":         "abc123",
      "target_id":  "123456789",
      "set_by_id":  "123456789",
      "guild_id":   "987654321",
      "channel_id": "111222333",
      "message":    "do this",
      "due":        1234567890.0,
      "dm":         true
    }
  }
"""

import asyncio
import logging
import secrets
import string
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import db
from utils import helpers as h

log = logging.getLogger("NanoBot.reminders")

_MAX        = 25          # max active reminders per user
_MIN_SECS   = 60          # 1 minute
_MAX_SECS   = 365 * 86400 # 1 year


# ── Helpers ────────────────────────────────────────────────────────────────────

def _new_id() -> str:
    """6-character alphanumeric ID — short enough to type on mobile."""
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(6))


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()



# ══════════════════════════════════════════════════════════════════════════════
class Reminders(commands.Cog):
    """Reminder commands — set it and forget it."""

    def __init__(self, bot: commands.Bot):
        self.bot   = bot
        self._tasks: dict[str, asyncio.Task] = {}  # reminder_id → Task

    # ── Restore on restart ─────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_restore_schedules(self):
        data  = await db.get_all_reminders()
        now   = _now()
        fired = 0

        for rid, info in data.items():
            remaining = info["due"] - now

            if remaining <= 0:
                asyncio.create_task(self._fire(info, delay=0))
                log.info(f"Overdue reminder {rid} — firing immediately")
                fired += 1
            else:
                self._tasks[rid] = asyncio.create_task(self._fire(info, delay=remaining))
                log.debug(f"Restored reminder {rid} — fires in {remaining:.0f}s")

        log.info(f"Reminders: restored {len(self._tasks)} active, fired {fired} overdue")

    # ── Background fire ────────────────────────────────────────────────────────
    async def _fire(self, info: dict, *, delay: float):
        """Sleep then deliver the reminder."""
        if delay > 0:
            await asyncio.sleep(delay)

        rid        = info["id"]
        target_id  = int(info["target_id"])
        set_by_id  = int(info["set_by_id"])
        channel_id = int(info["channel_id"])
        guild_id   = int(info["guild_id"])
        message    = info["message"]
        use_dm     = info.get("dm", True)
        due        = info["due"]

        set_at_ts = due - info.get("duration", 0)

        e = discord.Embed(
            title       = "⏰ Reminder",
            description = message,
            color       = h.BLUE,
        )
        e.set_footer(text="NanoBot Reminders")
        e.timestamp = datetime.now(timezone.utc)

        if set_by_id != target_id:
            setter = self.bot.get_user(set_by_id)
            setter_name = setter.display_name if setter else f"user {set_by_id}"
            e.add_field(name="Set by", value=setter_name, inline=True)

        delivered = False

        # ── Try DM ────────────────────────────────────────────────────────────
        if use_dm:
            user = self.bot.get_user(target_id) or await self.bot.fetch_user(target_id)
            if user:
                try:
                    await user.send(embed=e)
                    delivered = True
                    log.debug(f"Reminder {rid} delivered via DM to {target_id}")
                except discord.Forbidden:
                    log.debug(f"Reminder {rid}: DMs closed for {target_id}, falling back to channel")

        # ── Fall back to channel ping ──────────────────────────────────────────
        if not delivered:
            channel = self.bot.get_channel(channel_id)
            if channel:
                try:
                    await channel.send(f"<@{target_id}>", embed=e)
                    delivered = True
                    log.debug(f"Reminder {rid} delivered via channel {channel_id}")
                except (discord.Forbidden, discord.HTTPException) as exc:
                    log.warning(f"Reminder {rid}: could not deliver to channel {channel_id}: {exc}")

        if not delivered:
            log.warning(f"Reminder {rid} for {target_id} could not be delivered anywhere")

        # ── Clean up storage and task dict ────────────────────────────────────
        self._tasks.pop(rid, None)
        await db.remove_reminder(rid)

    # ── Scheduling ─────────────────────────────────────────────────────────────
    async def _schedule(self, info: dict):
        """Persist a reminder and create its asyncio task."""
        await db.set_reminder(info)
        self._tasks[info["id"]] = asyncio.create_task(
            self._fire(info, delay=info["due"] - _now())
        )

    # ── Core create logic ──────────────────────────────────────────────────────
    async def _create(
        self,
        ctx:      commands.Context,
        target:   discord.Member,
        message:  str,
        secs:     int,
        use_dm:   bool,
    ):
        """Validate and schedule a reminder. Used by all command variants."""
        if secs < _MIN_SECS:
            return await ctx.reply(
                embed=h.err(f"Minimum reminder time is **1 minute**. Got {h.fmt_duration(secs)}."),
                ephemeral=True,
            )
        if secs > _MAX_SECS:
            return await ctx.reply(
                embed=h.err("Maximum reminder time is **1 year**."),
                ephemeral=True,
            )
        if not message.strip():
            return await ctx.reply(
                embed=h.err("Reminder message can't be empty."),
                ephemeral=True,
            )
        if len(message) > 500:
            return await ctx.reply(
                embed=h.err("Reminder message must be 500 characters or fewer."),
                ephemeral=True,
            )

        # Check the target user's active reminder count (voters get a higher cap)
        try:
            from cogs.votes import get_reminder_limit
            user_max = await get_reminder_limit(target.id)
        except Exception:
            user_max = _MAX

        if await db.count_user_reminders(target.id) >= user_max:
            from cogs.votes import VOTER_REMINDER_MAX, DEFAULT_REMINDER_MAX
            subject = "You have" if target == ctx.author else f"{target.display_name} has"
            if user_max == DEFAULT_REMINDER_MAX:
                tip = f" Vote for NanoBot (`/vote`) to unlock **{VOTER_REMINDER_MAX}** slots."
            else:
                tip = ""
            return await ctx.reply(
                embed=h.err(
                    f"{subject} too many active reminders (**{user_max}** max). "
                    f"Cancel some first.{tip}"
                ),
                ephemeral=True,
            )

        # Generate a collision-free unique ID
        rid = _new_id()
        while await db.reminder_id_exists(rid):
            rid = _new_id()

        due = _now() + secs

        info = {
            "id":         rid,
            "target_id":  str(target.id),
            "set_by_id":  str(ctx.author.id),
            "guild_id":   str(ctx.guild.id),
            "channel_id": str(ctx.channel.id),
            "message":    message.strip(),
            "due":        due,
            "duration":   secs,
            "dm":         use_dm,
        }

        await self._schedule(info)

        due_dt = datetime.fromtimestamp(due, tz=timezone.utc)
        is_self = target == ctx.author

        lines = [
            f"📝 **{message.strip()[:200]}**",
            f"⏰ {discord.utils.format_dt(due_dt, style='R')} "
            f"({discord.utils.format_dt(due_dt, style='f')})",
            f"📬 Delivery: {'DM' if use_dm else 'channel ping'} "
            f"(falls back to channel if DMs are closed)" if use_dm else
            f"📬 Delivery: channel ping",
            f"🆔 ID: `{rid}`  _(use to cancel)_",
        ]
        if not is_self:
            lines.insert(0, f"👤 Reminding **{target.display_name}**")

        await ctx.reply(
            embed=h.ok("\n".join(lines), "⏰ Reminder Set"),
            ephemeral=True,
        )
        log.info(
            f"Reminder {rid} set by {ctx.author} for {target} "
            f"in {h.fmt_duration(secs)} via {'DM' if use_dm else 'channel'}"
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  /remindme
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="remindme",
        aliases=["rm"],
        description="Set a reminder for yourself. Duration can be part of the message.",
    )
    @app_commands.describe(
        message = "What to remind you about — include the time at the end: 'do this 8h' or 'do this in 8 hours'",
        time    = "Duration if not in the message (e.g. 8h, 30m, 2 hours) — ignored if message already has one",
        dm      = "DM you the reminder (default: yes, falls back to channel if DMs closed)",
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def remindme(
        self,
        ctx:     commands.Context,
        *,
        message: str,
        time:    Optional[str] = None,
        dm:      Optional[bool] = True,
    ):
        # Duration can be embedded in message or in the time arg
        cleaned, secs = h.parse_duration_from_end(message)
        if secs is None and time:
            secs    = h.parse_duration(time)
            cleaned = message
        if secs is None:
            return await ctx.reply(
                embed=h.err(
                    "Couldn't find a duration. Include it in your message or use the `time` argument.\n"
                    "Examples: `!remindme call mum 30m` · `!remindme stand up in 1 hour`"
                ),
                ephemeral=True,
            )

        await self._create(ctx, ctx.author, cleaned or message, secs, dm if dm is not None else True)

    # ══════════════════════════════════════════════════════════════════════════
    #  /remind @user
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="remind",
        description="Set a reminder for another user.",
    )
    @app_commands.describe(
        user    = "Who to remind",
        message = "What to remind them about — include the time at the end",
        time    = "Duration if not in the message (e.g. 1h, 30m)",
        dm      = "DM them the reminder (default: no — posts a channel ping instead)",
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def remind(
        self,
        ctx:     commands.Context,
        user:    discord.Member,
        *,
        message: str,
        time:    Optional[str] = None,
        dm:      Optional[bool] = False,
    ):
        if user.bot:
            return await ctx.reply(embed=h.err("You can't set reminders for bots."), ephemeral=True)

        cleaned, secs = h.parse_duration_from_end(message)
        if secs is None and time:
            secs    = h.parse_duration(time)
            cleaned = message
        if secs is None:
            return await ctx.reply(
                embed=h.err(
                    "Couldn't find a duration. Include it in your message or use the `time` argument.\n"
                    "Examples: `!remind @user call me back 30m` · `!remind @user check this in 2 hours`"
                ),
                ephemeral=True,
            )

        await self._create(ctx, user, cleaned or message, secs, dm if dm is not None else False)

    # ══════════════════════════════════════════════════════════════════════════
    #  /reminders  (list + cancel)
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_group(
        name="reminders",
        aliases=["reminder"],
        description="List your active reminders, or cancel one.",
        invoke_without_command=True,
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def reminders(self, ctx: commands.Context):
        """Default: list your reminders."""
        await self._list(ctx)

    @reminders.command(name="list", description="List your active reminders.")
    async def reminders_list(self, ctx: commands.Context):
        await self._list(ctx)

    @reminders.command(name="cancel", description="Cancel an active reminder by its ID.")
    @app_commands.describe(reminder_id="The 6-character ID shown when the reminder was set")
    async def reminders_cancel(self, ctx: commands.Context, reminder_id: str):
        await self._cancel(ctx, reminder_id.strip().lower())

    # ── List helper ────────────────────────────────────────────────────────────
    async def _list(self, ctx: commands.Context):
        user_rems = await db.get_user_reminders(ctx.author.id)

        if not user_rems:
            return await ctx.reply(
                embed=h.info("You have no active reminders.\nUse `remindme` to set one.", "⏰ Reminders"),
                ephemeral=True,
            )

        e = h.embed(
            title  = f"⏰ Your Reminders ({len(user_rems)}/{_MAX})",
            color  = h.BLUE,
        )

        now = _now()
        for rid, info in sorted(user_rems.items(), key=lambda x: x[1]["due"]):
            due_dt    = datetime.fromtimestamp(info["due"], tz=timezone.utc)
            remaining = max(0, info["due"] - now)
            msg_preview = info["message"][:80] + ("…" if len(info["message"]) > 80 else "")
            delivery  = "DM" if info.get("dm") else "channel ping"

            # Show who set it if it wasn't self-set
            set_note = ""
            if info.get("set_by_id") and info["set_by_id"] != info["target_id"]:
                setter = self.bot.get_user(int(info["set_by_id"]))
                set_note = f"\nSet by: {setter.display_name if setter else '?'}"

            e.add_field(
                name  = f"`{rid}` — {discord.utils.format_dt(due_dt, style='R')}",
                value = (
                    f"{msg_preview}\n"
                    f"📬 {delivery} · ⏱️ {h.fmt_duration(int(remaining))}{set_note}\n"
                    f"_Cancel: `{ctx.prefix or '/'}reminders cancel {rid}`_"
                ),
                inline=False,
            )

        e.set_footer(text=f"NanoBot Reminders · {len(user_rems)}/{_MAX} active")
        await ctx.reply(embed=e, ephemeral=True)

    # ── Cancel helper ──────────────────────────────────────────────────────────
    async def _cancel(self, ctx: commands.Context, rid: str):
        # Look up in user's own reminders first, then globally (to check ownership)
        user_rems = await db.get_user_reminders(ctx.author.id)
        if rid in user_rems:
            info = user_rems[rid]
        else:
            all_rems = await db.get_all_reminders()
            if rid not in all_rems:
                return await ctx.reply(
                    embed=h.err(f"No reminder with ID `{rid}` found.\nUse `reminders` to see your active ones."),
                    ephemeral=True,
                )
            info = all_rems[rid]

        # Only the target or the person who set it can cancel
        allowed = {info.get("target_id"), info.get("set_by_id")}
        if str(ctx.author.id) not in allowed:
            return await ctx.reply(
                embed=h.err("You can only cancel your own reminders."),
                ephemeral=True,
            )

        # Cancel the running task
        task = self._tasks.pop(rid, None)
        if task:
            task.cancel()

        await db.remove_reminder(rid)

        due_dt = datetime.fromtimestamp(info["due"], tz=timezone.utc)
        await ctx.reply(
            embed=h.ok(
                f"Cancelled reminder `{rid}`.\n"
                f"📝 _{info['message'][:150]}_\n"
                f"Was due {discord.utils.format_dt(due_dt, style='R')}.",
                "⏰ Reminder Cancelled",
            ),
            ephemeral=True,
        )
        log.info(f"Reminder {rid} cancelled by {ctx.author}")


# ── Registration ───────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(Reminders(bot))
