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

# Suggestions only — h.duration_picker echoes anything parse_duration accepts,
# and the duration can still be written at the end of the message instead.
_REMIND_IN_CHOICES = h.duration_picker(
    [
        ("In 5 minutes", "5m"),
        ("In 15 minutes", "15m"),
        ("In 30 minutes", "30m"),
        ("In 1 hour", "1h"),
        ("In 3 hours", "3h"),
        ("In 8 hours", "8h"),
        ("Tomorrow", "1d"),
        ("In 3 days", "3d"),
        ("In a week", "7d"),
    ]
)

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

    async def cog_load(self):
        # restore_schedules is dispatched once, from the first on_ready — so a
        # hot-reload after the bot is up would otherwise leave persisted rows
        # un-armed. Re-run the restore here when the gateway is already ready
        # (initial startup still waits for the dispatch). Safe to double up:
        # _spawn/spawn_tracked cancels whatever it replaces.
        if self.bot.is_ready():
            self.bot.loop.create_task(self.on_restore_schedules())

    def cog_unload(self):
        """Cancel all pending fire tasks so a cog reload doesn't leak them
        (and doesn't double-deliver when the new instance restores from DB)."""
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()

    # ── Restore on restart ─────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_restore_schedules(self):
        data = await db.get_all_reminders()
        overdue = 0

        for rid, info in data.items():
            if info["due"] - _now() <= 0:
                overdue += 1
            self._spawn(info)

        log.info(
            f"Reminders: restored {len(self._tasks)} ({overdue} overdue, firing now)"
        )

    def _spawn(self, info: dict) -> None:
        """Create and track the fire task for a reminder, keyed by its id.

        Cancels any task already armed for that id, so re-running the restore
        listener (on_ready fires again after a reconnect) can't leave two
        timers racing to deliver the same reminder.
        """
        h.spawn_tracked(self._tasks, info["id"], self._fire(info))

    # ── Background fire ────────────────────────────────────────────────────────
    async def _fire(self, info: dict):
        """Sleep until due (in bounded chunks) then deliver, then clean up.

        A *cancelled* timer deliberately leaves the row alone. Cancellation
        means the timer is being replaced or the cog is unloading — the
        reminder itself hasn't happened, and the next restore has to be able to
        find it. (Deleting it here meant a cog reload silently wiped every
        pending reminder; `/reminders cancel` removes the row itself.)
        """
        rid = info["id"]
        try:
            await self._sleep_until(info["due"])
            await self._deliver(info)
        except asyncio.CancelledError:
            # Only drop the dict entry if it's still ours — a replacement task
            # may already have claimed the slot.
            if self._tasks.get(rid) is asyncio.current_task():
                self._tasks.pop(rid, None)
            raise
        except Exception:
            log.exception(f"Reminder {rid}: delivery failed")
        self._tasks.pop(rid, None)
        try:
            await db.remove_reminder(rid)
        except Exception:
            log.exception(f"Reminder {rid}: failed to remove from DB")

    @staticmethod
    async def _sleep_until(due: float) -> None:
        """Sleep until the wall-clock `due` timestamp in chunks no longer than an
        hour, re-checking each time. A single multi-month sleep is fragile and
        drifts; chunked sleeps re-anchor to the real clock."""
        chunk = 3600
        while True:
            remaining = due - _now()
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, chunk))

    async def _deliver(self, info: dict):
        rid = info["id"]
        target_id = int(info["target_id"])
        set_by_id = int(info["set_by_id"])
        channel_id = int(info["channel_id"])
        message = info["message"]
        use_dm = info.get("dm", True)

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
            user = self.bot.get_user(target_id)
            if user is None:
                try:
                    user = await self.bot.fetch_user(target_id)
                except discord.HTTPException:
                    user = None
            if user:
                try:
                    await user.send(embed=e)
                    delivered = True
                    log.debug(f"Reminder {rid} delivered via DM to {target_id}")
                except discord.Forbidden:
                    log.debug(
                        f"Reminder {rid}: DMs closed for {target_id}, falling back to channel"
                    )
                except discord.HTTPException as exc:
                    log.warning(f"Reminder {rid}: DM send failed: {exc}")

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

    # ── Scheduling ─────────────────────────────────────────────────────────────
    async def _schedule(self, info: dict):
        """Persist a reminder and create its asyncio task."""
        await db.set_reminder(info)
        self._spawn(info)

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
                else "📬 Delivery: channel ping"
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
            f"Reminder {rid} set by {h.user_log(ctx.author)} for {h.user_log(target)} "
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
            "example": "{prefix}remindme stand up in 1 hour",
        },
    )
    @app_commands.describe(
        message="What to remind you about — include the time at the end: 'do this 8h' or 'do this in 8 hours'",
        time="Duration if not in the message (e.g. 8h, 30m, 2 hours) — ignored if message already has one",
        dm="DM you the reminder (default: yes, falls back to channel if DMs closed)",
    )
    @app_commands.autocomplete(time=_REMIND_IN_CHOICES)
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
            "example": "{prefix}remind @user check that PR 2h",
        },
    )
    @app_commands.describe(
        user="Who to remind",
        message="What to remind them about — include the time at the end",
        time="Duration if not in the message (e.g. 1h, 30m)",
        dm="DM them the reminder (default: no — posts a channel ping instead)",
    )
    @app_commands.autocomplete(time=_REMIND_IN_CHOICES)
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
            "example": "{prefix}reminders cancel 2",
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
        log.info(f"Reminder {rid} (#{number}) cancelled by {h.user_log(ctx.author)}")


# ── Registration ───────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(Reminders(bot))
