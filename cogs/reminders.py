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
  /reminders cancel <number>           → cancel by list number

Prefix shorthands:
  !remindme do this 8h
  !remindme do this in 8 hours
  !remind @user do that 1h
  !reminders
  !reminders cancel 2

──────────────────────────────────────────────────────
Storage  (SQLite via utils/db.py)
──────────────────────────────────────────────────────
Reminders table keyed by internal ID (hidden from users).
Users see sequential numbers (#1, #2, #3) based on their
sorted list at the time of viewing.
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

_MAX = 25  # max active reminders per user
_MIN_SECS = 60  # 1 minute
_MAX_SECS = 365 * 86400  # 1 year


# ── Helpers ────────────────────────────────────────────────────────────────────


def _new_id() -> str:
    """6-character internal ID for DB/task tracking. Never shown to users."""
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(6))


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _build_numbered_list(own: dict, sent: dict) -> list[tuple[int, str, dict, str]]:
    """Build a combined numbered list of reminders.

    Returns list of (number, rid, info, section) tuples sorted by due date.
    Own reminders come first, then sent reminders, each sorted by due date.
    """
    entries: list[tuple[int, str, dict, str]] = []
    n = 1

    for rid, info in sorted(own.items(), key=lambda x: x[1]["due"]):
        entries.append((n, rid, info, "own"))
        n += 1

    for rid, info in sorted(sent.items(), key=lambda x: x[1]["due"]):
        entries.append((n, rid, info, "sent"))
        n += 1

    return entries


# ══════════════════════════════════════════════════════════════════════════════
class Reminders(commands.Cog):
    """Reminder commands — set it and forget it."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._tasks: dict[str, asyncio.Task] = {}  # reminder_id → Task

    # ── Restore on restart ─────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_restore_schedules(self):
        data = await db.get_all_reminders()
        now = _now()
        fired = 0

        for rid, info in data.items():
            remaining = info["due"] - now

            if remaining <= 0:
                asyncio.create_task(self._fire(info, delay=0))
                log.info(f"Overdue reminder {rid} — firing immediately")
                fired += 1
            else:
                self._tasks[rid] = asyncio.create_task(
                    self._fire(info, delay=remaining)
                )
                log.debug(f"Restored reminder {rid} — fires in {remaining:.0f}s")

        log.info(
            f"Reminders: restored {len(self._tasks)} active, fired {fired} overdue"
        )

    # ── Background fire ────────────────────────────────────────────────────────
    async def _fire(self, info: dict, *, delay: float):
        """Sleep then deliver the reminder."""
        if delay > 0:
            await asyncio.sleep(delay)

        rid = info["id"]
        target_id = int(info["target_id"])
        set_by_id = int(info["set_by_id"])
        channel_id = int(info["channel_id"])
        guild_id = int(info["guild_id"])
        message = info["message"]
        use_dm = info.get("dm", True)
        due = info["due"]

        set_at_ts = due - info.get("duration", 0)

        e = discord.Embed(
            title="⏰ Reminder",
            description=message,
            color=h.BLUE,
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
                    log.debug(
                        f"Reminder {rid}: DMs closed for {target_id}, falling back to channel"
                    )

        # ── Fall back to channel ping ──────────────────────────────────────────
        if not delivered:
            channel = self.bot.get_channel(channel_id)
            if channel:
                try:
                    await channel.send(f"<@{target_id}>", embed=e)
                    delivered = True
                    log.debug(f"Reminder {rid} delivered via channel {channel_id}")
                except (discord.Forbidden, discord.HTTPException) as exc:
                    log.warning(
                        f"Reminder {rid}: could not deliver to channel {channel_id}: {exc}"
                    )

        if not delivered:
            log.warning(
                f"Reminder {rid} for {target_id} could not be delivered anywhere"
            )

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
        ctx: commands.Context,
        target: discord.Member,
        message: str,
        secs: int,
        use_dm: bool,
    ):
        """Validate and schedule a reminder. Used by all command variants."""
        if secs < _MIN_SECS:
            return await ctx.reply(
                embed=h.err(
                    f"Minimum reminder time is **1 minute**. Got {h.fmt_duration(secs)}."
                ),
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

            subject = (
                "You have" if target == ctx.author else f"{target.display_name} has"
            )
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

        # Generate a collision-free internal ID
        rid = _new_id()
        while await db.reminder_id_exists(rid):
            rid = _new_id()

        due = _now() + secs

        info = {
            "id": rid,
            "target_id": str(target.id),
            "set_by_id": str(ctx.author.id),
            "guild_id": str(ctx.guild.id),
            "channel_id": str(ctx.channel.id),
            "message": message.strip(),
            "due": due,
            "duration": secs,
            "dm": use_dm,
        }

        await self._schedule(info)

        due_dt = datetime.fromtimestamp(due, tz=timezone.utc)
        is_self = target == ctx.author

        lines = [
            f"📝 **{message.strip()[:200]}**",
            f"⏰ {discord.utils.format_dt(due_dt, style='R')} "
            f"({discord.utils.format_dt(due_dt, style='f')})",
            (
                f"📬 Delivery: {'DM' if use_dm else 'channel ping'} "
                f"(falls back to channel if DMs are closed)"
                if use_dm
                else f"📬 Delivery: channel ping"
            ),
        ]
        if not is_self:
            lines.insert(0, f"👤 Reminding **{target.display_name}**")

        lines.append(f"View or cancel: `{ctx.prefix or '/'}reminders`")

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
        extras={
            "category": "⏰ Reminders",
            "short": "Set a reminder for yourself",
            "usage": "remindme <message with duration>",
            "desc": "Remind yourself about something. Put the duration at the end of your message. Delivered by DM, falls back to channel ping.",
            "args": [
                (
                    "message",
                    "What to remind you about — put the duration at the end (e.g. stand up 1h)",
                ),
            ],
            "perms": "None",
            "example": "!remindme stand up in 1 hour",
        },
    )
    @app_commands.describe(
        message="What to remind you about — include the time at the end: 'do this 8h' or 'do this in 8 hours'",
        time="Duration if not in the message (e.g. 8h, 30m, 2 hours) — ignored if message already has one",
        dm="DM you the reminder (default: yes, falls back to channel if DMs closed)",
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def remindme(
        self,
        ctx: commands.Context,
        *,
        message: str,
        time: Optional[str] = None,
        dm: Optional[bool] = True,
    ):
        # Duration can be embedded in message or in the time arg
        cleaned, secs = h.parse_duration_from_end(message)
        if secs is None and time:
            secs = h.parse_duration(time)
            cleaned = message
        if secs is None:
            return await ctx.reply(
                embed=h.err(
                    "Couldn't find a duration. Include it in your message or use the `time` argument.\n"
                    "Examples: `!remindme call mum 30m` · `!remindme stand up in 1 hour`"
                ),
                ephemeral=True,
            )

        await self._create(
            ctx, ctx.author, cleaned or message, secs, dm if dm is not None else True
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  /remind @user
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_command(
        name="remind",
        description="Set a reminder for another user.",
        extras={
            "category": "⏰ Reminders",
            "short": "Set a reminder for another user",
            "usage": "remind <@user> <message with duration>",
            "desc": "Remind someone else. Posts a channel ping by default.",
            "args": [
                ("user", "Who to remind"),
                ("message", "What to remind them about — duration at the end"),
            ],
            "perms": "None",
            "example": "!remind @user check that PR 2h",
        },
    )
    @app_commands.describe(
        user="Who to remind",
        message="What to remind them about — include the time at the end",
        time="Duration if not in the message (e.g. 1h, 30m)",
        dm="DM them the reminder (default: no — posts a channel ping instead)",
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def remind(
        self,
        ctx: commands.Context,
        user: discord.Member,
        *,
        message: str,
        time: Optional[str] = None,
        dm: Optional[bool] = False,
    ):
        if user.bot:
            return await ctx.reply(
                embed=h.err("You can't set reminders for bots."), ephemeral=True
            )

        cleaned, secs = h.parse_duration_from_end(message)
        if secs is None and time:
            secs = h.parse_duration(time)
            cleaned = message
        if secs is None:
            return await ctx.reply(
                embed=h.err(
                    "Couldn't find a duration. Include it in your message or use the `time` argument.\n"
                    "Examples: `!remind @user call me back 30m` · `!remind @user check this in 2 hours`"
                ),
                ephemeral=True,
            )

        await self._create(
            ctx, user, cleaned or message, secs, dm if dm is not None else False
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  /reminders  (list + cancel)
    # ══════════════════════════════════════════════════════════════════════════
    @commands.hybrid_group(
        name="reminders",
        aliases=["reminder"],
        description="List your active reminders, or cancel one.",
        invoke_without_command=True,
        extras={
            "category": "⏰ Reminders",
            "short": "List or cancel your active reminders",
            "usage": "reminders [cancel <number>]",
            "desc": "No args: lists all your active reminders. cancel <number>: cancels that reminder by its list number.",
            "args": [
                ("number", "Reminder number shown in the list"),
            ],
            "perms": "None",
            "example": "!reminders cancel 2",
        },
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def reminders(self, ctx: commands.Context):
        """Default: list your reminders."""
        await self._list(ctx)

    @reminders.command(name="list", description="List your active reminders.")
    async def reminders_list(self, ctx: commands.Context):
        await self._list(ctx)

    @reminders.command(
        name="cancel", description="Cancel an active reminder by its list number."
    )
    @app_commands.describe(number="The number shown next to the reminder in your list")
    async def reminders_cancel(self, ctx: commands.Context, number: int):
        await self._cancel(ctx, number)

    # ── List helper ────────────────────────────────────────────────────────────
    async def _list(self, ctx: commands.Context):
        own_rems = await db.get_user_reminders(ctx.author.id)
        sent_rems = await db.get_sent_reminders(ctx.author.id)

        try:
            from cogs.votes import get_reminder_limit

            user_max = await get_reminder_limit(ctx.author.id)
        except Exception:
            user_max = _MAX

        if not own_rems and not sent_rems:
            return await ctx.reply(
                embed=h.info(
                    "You have no active reminders.\nUse `remindme` to set one.",
                    "⏰ Reminders",
                ),
                ephemeral=True,
            )

        entries = _build_numbered_list(own_rems, sent_rems)

        e = h.embed(
            title=f"⏰ Your Reminders ({len(own_rems)}/{user_max})",
            color=h.BLUE,
        )

        # ── Own reminders section ─────────────────────────────────────────────
        own_entries = [x for x in entries if x[3] == "own"]
        if own_entries:
            lines = []
            for num, rid, info, section in own_entries:
                due_dt = datetime.fromtimestamp(info["due"], tz=timezone.utc)
                msg_preview = info["message"][:60] + (
                    "..." if len(info["message"]) > 60 else ""
                )
                delivery = "DM" if info.get("dm") else "channel"

                set_note = ""
                if info.get("set_by_id") and info["set_by_id"] != info["target_id"]:
                    setter = self.bot.get_user(int(info["set_by_id"]))
                    set_note = f" (from {setter.display_name if setter else '?'})"

                lines.append(
                    f"**#{num}** {msg_preview}{set_note}\n"
                    f"  {discord.utils.format_dt(due_dt, style='R')} · {delivery}"
                )

            e.add_field(
                name="Your Reminders",
                value="\n".join(lines),
                inline=False,
            )

        # ── Sent reminders section ────────────────────────────────────────────
        sent_entries = [x for x in entries if x[3] == "sent"]
        if sent_entries:
            lines = []
            for num, rid, info, section in sent_entries:
                due_dt = datetime.fromtimestamp(info["due"], tz=timezone.utc)
                msg_preview = info["message"][:60] + (
                    "..." if len(info["message"]) > 60 else ""
                )
                delivery = "DM" if info.get("dm") else "channel"

                target = self.bot.get_user(int(info["target_id"]))
                target_name = target.display_name if target else "?"

                lines.append(
                    f"**#{num}** {msg_preview} (to {target_name})\n"
                    f"  {discord.utils.format_dt(due_dt, style='R')} · {delivery}"
                )

            e.add_field(
                name="Sent Reminders",
                value="\n".join(lines),
                inline=False,
            )

        cancel_prefix = ctx.prefix or "/"
        e.set_footer(text=f"Cancel: {cancel_prefix}reminders cancel <number>")
        await ctx.reply(embed=e, ephemeral=True)

    # ── Cancel helper ──────────────────────────────────────────────────────────
    async def _cancel(self, ctx: commands.Context, number: int):
        own_rems = await db.get_user_reminders(ctx.author.id)
        sent_rems = await db.get_sent_reminders(ctx.author.id)
        entries = _build_numbered_list(own_rems, sent_rems)

        if not entries:
            return await ctx.reply(
                embed=h.err("You have no active reminders to cancel."),
                ephemeral=True,
            )

        # Find the entry matching the requested number
        match = None
        for num, rid, info, section in entries:
            if num == number:
                match = (rid, info)
                break

        if not match:
            total = len(entries)
            return await ctx.reply(
                embed=h.err(
                    f"No reminder **#{number}**. You have **{total}** active "
                    f"({'1' if total == 1 else f'1-{total}'}).\n"
                    f"Use `{ctx.prefix or '/'}reminders` to see the list."
                ),
                ephemeral=True,
            )

        rid, info = match

        # Cancel the running task
        task = self._tasks.pop(rid, None)
        if task:
            task.cancel()

        await db.remove_reminder(rid)

        due_dt = datetime.fromtimestamp(info["due"], tz=timezone.utc)
        await ctx.reply(
            embed=h.ok(
                f"Cancelled reminder **#{number}**.\n"
                f"📝 _{info['message'][:150]}_\n"
                f"Was due {discord.utils.format_dt(due_dt, style='R')}.",
                "⏰ Reminder Cancelled",
            ),
            ephemeral=True,
        )
        log.info(f"Reminder {rid} (#{number}) cancelled by {ctx.author}")


# ── Registration ───────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(Reminders(bot))
