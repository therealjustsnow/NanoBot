"""
cogs/recurring.py
Recurring reminder system — set it once, get reminded forever.
Like Google Calendar repeat events, but in Discord.

Reminders survive bot restarts. On restore, overdue reminders fire once
immediately (no catch-up spam) then reschedule. Missed cycles are skipped
cleanly — the next fire is always in the future.

──────────────────────────────────────────────────────
Commands
──────────────────────────────────────────────────────
  /every  <interval> <message> [label] [dm]   → create a recurring reminder
  /recurring                                  → list your recurring reminders
  /recurring pause  <id>                      → stop firing until resumed
  /recurring resume <id>                      → re-enable, schedules for now + interval
  /recurring cancel <id>                      → permanently delete

Prefix shorthands:
  !every 2w Payday!
  !every daily Stand up meeting
  !recurring
  !recurring pause abc123
  !recurring resume abc123
  !recurring cancel abc123

──────────────────────────────────────────────────────
Limits
──────────────────────────────────────────────────────
  Max 10 recurring reminders per user.
  Min interval: 1 hour · Max interval: 1 year.
  For sub-hour reminders use /remindme instead.

──────────────────────────────────────────────────────
Storage  (recurring_reminders table in nanobot.db)
──────────────────────────────────────────────────────
  id          TEXT PRIMARY KEY    — 6-char alphanumeric
  target_id   TEXT NOT NULL
  set_by_id   TEXT NOT NULL
  guild_id    TEXT NOT NULL
  channel_id  TEXT NOT NULL
  message     TEXT NOT NULL
  interval    REAL NOT NULL       — seconds between fires
  next_due    REAL NOT NULL       — unix timestamp of next delivery
  dm          INTEGER NOT NULL DEFAULT 1
  paused      INTEGER NOT NULL DEFAULT 0
  fire_count  INTEGER NOT NULL DEFAULT 0
  label       TEXT                — optional short display name, e.g. "Payday"
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

log = logging.getLogger("NanoBot.recurring")

_MAX = 10  # max recurring reminders per user
_MIN_SECS = 3_600  # 1 hour minimum interval
_MAX_SECS = 365 * 86_400  # 1 year maximum interval


# ── Helpers ────────────────────────────────────────────────────────────────────


def _new_id() -> str:
    """6-character alphanumeric ID — short enough to type on mobile."""
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(6))


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


# ── Autocomplete ───────────────────────────────────────────────────────────────

_INTERVAL_SUGGESTIONS = [
    ("Hourly", "1h"),
    ("Every 6 hours", "6h"),
    ("Every 12 hours", "12h"),
    ("Daily", "daily"),
    ("Every 2 days", "2d"),
    ("Every 3 days", "3d"),
    ("Weekly", "weekly"),
    ("Every 2 weeks", "2w"),
    ("Monthly (30 days)", "monthly"),
]


async def _interval_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    cl = current.lower()
    return [
        app_commands.Choice(name=label, value=value)
        for label, value in _INTERVAL_SUGGESTIONS
        if cl in label.lower() or cl in value.lower()
    ][:25]


# ══════════════════════════════════════════════════════════════════════════════
class Recurring(commands.Cog):
    """Recurring reminder commands — set it once, get reminded forever."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._tasks: dict[str, asyncio.Task] = {}  # recurring_id → Task

    def cog_unload(self):
        """Cancel all in-flight tasks when the cog is unloaded or reloaded."""
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()

    # ── Restore on restart ─────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_restore_schedules(self):
        data = await db.get_all_recurring()
        now = _now()
        restored = 0
        overdue = 0

        for rid, info in data.items():
            if info["paused"]:
                log.debug(f"Recurring {rid} is paused — skipping restore")
                continue

            remaining = info["next_due"] - now

            if remaining <= 0:
                # Fire immediately; _fire will advance next_due past now
                asyncio.create_task(self._fire(info, delay=0))
                log.info(f"Overdue recurring {rid} — firing immediately")
                overdue += 1
            else:
                self._tasks[rid] = asyncio.create_task(
                    self._fire(info, delay=remaining)
                )
                log.debug(f"Restored recurring {rid} — fires in {remaining:.0f}s")
                restored += 1

        log.info(f"Recurring: restored {restored} active, fired {overdue} overdue")

    # ── Background fire ────────────────────────────────────────────────────────

    async def _fire(self, info: dict, *, delay: float):
        """Sleep, deliver the reminder, advance next_due, then self-reschedule."""
        if delay > 0:
            await asyncio.sleep(delay)

        # Re-fetch from DB — ensures we see any pause/cancel that happened
        # while we were sleeping
        fresh = await db.get_recurring(info["id"])
        if fresh is None or fresh["paused"]:
            self._tasks.pop(info["id"], None)
            return

        rid = fresh["id"]
        target_id = int(fresh["target_id"])
        set_by_id = int(fresh["set_by_id"])
        channel_id = int(fresh["channel_id"])
        message = fresh["message"]
        use_dm = fresh.get("dm", True)
        label = fresh.get("label")
        interval = fresh["interval"]
        fire_count = fresh["fire_count"] + 1

        e = discord.Embed(
            title=f"🔁 {label}" if label else "🔁 Recurring Reminder",
            description=message,
            color=h.BLUE,
        )
        e.set_footer(text=f"NanoBot · repeats every {h.fmt_interval(int(interval))}")
        e.timestamp = datetime.now(timezone.utc)

        if set_by_id != target_id:
            setter = self.bot.get_user(set_by_id)
            setter_name = setter.display_name if setter else f"user {set_by_id}"
            e.add_field(name="Set by", value=setter_name, inline=True)

        delivered = False

        # ── DM attempt ────────────────────────────────────────────────────────
        if use_dm:
            user = self.bot.get_user(target_id) or await self.bot.fetch_user(target_id)
            if user:
                try:
                    await user.send(embed=e)
                    delivered = True
                except discord.Forbidden:
                    pass  # DMs closed — fall through to channel

        # ── Channel fallback ──────────────────────────────────────────────────
        if not delivered:
            channel = self.bot.get_channel(channel_id)
            if channel:
                try:
                    await channel.send(f"<@{target_id}>", embed=e)
                    delivered = True
                except (discord.Forbidden, discord.HTTPException) as exc:
                    log.warning(f"Recurring {rid}: channel delivery failed: {exc}")

        if not delivered:
            log.warning(
                f"Recurring {rid} for {target_id} could not be delivered anywhere"
            )

        # ── Advance next_due ──────────────────────────────────────────────────
        # Advance by one interval, then skip ahead if multiple cycles were
        # missed (e.g. bot was down for several days). This ensures the next
        # fire is always in the future without rapid catch-up spam.
        now = _now()
        next_due = fresh["next_due"] + interval
        while next_due <= now:
            next_due += interval

        fresh["next_due"] = next_due
        fresh["fire_count"] = fire_count
        await db.update_recurring(fresh)

        # ── Schedule next fire ────────────────────────────────────────────────
        self._tasks[rid] = asyncio.create_task(
            self._fire(fresh, delay=next_due - _now())
        )
        log.debug(
            f"Recurring {rid} fired (×{fire_count}), "
            f"next in {h.fmt_interval(int(next_due - now))}"
        )

    # ── Scheduling ─────────────────────────────────────────────────────────────

    async def _schedule(self, info: dict):
        """Persist a new recurring reminder and start its asyncio task."""
        await db.set_recurring(info)
        self._tasks[info["id"]] = asyncio.create_task(
            self._fire(info, delay=max(0.0, info["next_due"] - _now()))
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  /every  — create a recurring reminder
    # ══════════════════════════════════════════════════════════════════════════

    @commands.hybrid_command(
        name="every",
        description="Set a recurring reminder — like a repeating calendar event.",
    )
    @app_commands.describe(
        interval=(
            "How often to remind you. Pick a preset or type your own: "
            "daily, weekly, 2w, 3d, 1h, monthly …"
        ),
        message="What to remind you about (up to 500 characters)",
        label=(
            "Short name shown in your list, e.g. 'Payday' (optional, max 50 chars). "
            "Helps on mobile where long messages get truncated."
        ),
        dm="DM you the reminder (default: yes, falls back to channel ping if DMs closed)",
    )
    @app_commands.autocomplete(interval=_interval_autocomplete)
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def every(
        self,
        ctx: commands.Context,
        interval: str,
        *,
        message: str,
        label: Optional[str] = None,
        dm: Optional[bool] = True,
    ):
        # ── Validate interval ──────────────────────────────────────────────────
        secs = h.parse_interval(interval)
        if secs is None:
            return await ctx.reply(
                embed=h.err(
                    "Couldn't parse that interval.\n"
                    "**Presets:** `daily` · `weekly` · `monthly`\n"
                    "**Custom:** `2w` · `3d` · `6h` · `every 2 weeks`"
                ),
                ephemeral=True,
            )
        if secs < _MIN_SECS:
            return await ctx.reply(
                embed=h.err(
                    f"Minimum interval is **1 hour**. Got `{h.fmt_interval(secs)}`.\n"
                    "Need something shorter? Use `/remindme` instead."
                ),
                ephemeral=True,
            )
        if secs > _MAX_SECS:
            return await ctx.reply(
                embed=h.err("Maximum interval is **1 year**."),
                ephemeral=True,
            )

        # ── Validate message & label ───────────────────────────────────────────
        message = message.strip()
        if not message:
            return await ctx.reply(
                embed=h.err("Reminder message can't be empty."), ephemeral=True
            )
        if len(message) > 500:
            return await ctx.reply(
                embed=h.err("Message must be 500 characters or fewer."), ephemeral=True
            )
        if label:
            label = label.strip()
            if len(label) > 50:
                return await ctx.reply(
                    embed=h.err("Label must be 50 characters or fewer."), ephemeral=True
                )

        # ── Check per-user cap ─────────────────────────────────────────────────
        count = await db.count_user_recurring(ctx.author.id)
        if count >= _MAX:
            return await ctx.reply(
                embed=h.err(
                    f"You already have **{_MAX}** recurring reminders (the maximum).\n"
                    "Cancel one with `/recurring cancel <id>` to make room."
                ),
                ephemeral=True,
            )

        # ── Generate unique ID ─────────────────────────────────────────────────
        rid = _new_id()
        while await db.recurring_id_exists(rid):
            rid = _new_id()

        now = _now()
        info = {
            "id": rid,
            "target_id": str(ctx.author.id),
            "set_by_id": str(ctx.author.id),
            "guild_id": str(ctx.guild.id) if ctx.guild else "0",
            "channel_id": str(ctx.channel.id),
            "message": message,
            "interval": float(secs),
            "next_due": now + secs,
            "dm": dm if dm is not None else True,
            "paused": False,
            "fire_count": 0,
            "label": label or None,
        }

        await self._schedule(info)

        due_dt = datetime.fromtimestamp(info["next_due"], tz=timezone.utc)
        display = label if label else message[:60] + ("…" if len(message) > 60 else "")
        lines = [
            f"📝 {display}",
            f"🔁 Every **{h.fmt_interval(secs)}**",
            f"⏭️ First fire: {discord.utils.format_dt(due_dt, style='R')}",
            (
                "📬 Delivery: DM (falls back to channel ping if DMs are closed)"
                if info["dm"]
                else "📢 Delivery: channel ping"
            ),
            f"🆔 ID: `{rid}`  _(use to pause or cancel)_",
        ]
        await ctx.reply(
            embed=h.ok("\n".join(lines), "🔁 Recurring Reminder Set"),
            ephemeral=True,
        )
        log.info(
            f"Recurring {rid} created by {ctx.author} — "
            f"every {h.fmt_interval(secs)}"
        )

    # ══════════════════════════════════════════════════════════════════════════
    #  /recurring  — list / pause / resume / cancel
    # ══════════════════════════════════════════════════════════════════════════

    @commands.hybrid_group(
        name="recurring",
        aliases=["repeating", "repeat"],
        description="Manage your recurring reminders — list, pause, resume, or cancel.",
        invoke_without_command=True,
    )
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def recurring(self, ctx: commands.Context):
        """Default subcommand: list your recurring reminders."""
        await self._list(ctx)

    @recurring.command(name="list", description="List all your recurring reminders.")
    async def recurring_list(self, ctx: commands.Context):
        await self._list(ctx)

    @recurring.command(
        name="pause",
        description="Pause a recurring reminder — it won't fire until you resume it.",
    )
    @app_commands.describe(
        reminder_id="The 6-character ID shown when the reminder was set"
    )
    async def recurring_pause(self, ctx: commands.Context, reminder_id: str):
        await self._pause(ctx, reminder_id.strip().lower())

    @recurring.command(
        name="resume",
        description="Resume a paused recurring reminder.",
    )
    @app_commands.describe(reminder_id="The 6-character ID of the paused reminder")
    async def recurring_resume(self, ctx: commands.Context, reminder_id: str):
        await self._resume(ctx, reminder_id.strip().lower())

    @recurring.command(
        name="cancel",
        description="Permanently delete a recurring reminder.",
    )
    @app_commands.describe(reminder_id="The 6-character ID of the reminder to delete")
    async def recurring_cancel(self, ctx: commands.Context, reminder_id: str):
        await self._cancel(ctx, reminder_id.strip().lower())

    # ── List ───────────────────────────────────────────────────────────────────

    async def _list(self, ctx: commands.Context):
        rows = await db.get_user_recurring(ctx.author.id)

        if not rows:
            return await ctx.reply(
                embed=h.info(
                    "You have no recurring reminders.\n\n"
                    "**Get started:**\n"
                    "`/every daily Stand up meeting`\n"
                    "`/every 2w Payday!`\n"
                    "`/every weekly Review my goals`",
                    "🔁 Recurring Reminders",
                ),
                ephemeral=True,
            )

        e = h.embed(
            title=f"🔁 Your Recurring Reminders ({len(rows)}/{_MAX})",
            color=h.BLUE,
        )

        for info in rows:  # already ordered by next_due ASC from DB
            rid = info["id"]
            label = info.get("label")
            msg = info["message"]
            interval = int(info["interval"])
            paused = info["paused"]
            count = info["fire_count"]

            # Mobile-friendly display — label beats truncated message
            display = label if label else (msg[:50] + ("…" if len(msg) > 50 else ""))
            due_dt = datetime.fromtimestamp(info["next_due"], tz=timezone.utc)
            delivery = "DM" if info.get("dm") else "channel"

            if paused:
                status_icon = "⏸️"
                status_line = "⏸️ Paused"
            else:
                status_icon = ""
                status_line = f"⏭️ {discord.utils.format_dt(due_dt, style='R')}"

            fired_note = f" · fired ×{count}" if count else ""
            prefix = ctx.prefix or "/"
            cancel_hint = f"`{prefix}recurring cancel {rid}`"

            e.add_field(
                name=f"{status_icon}`{rid}` — {display}".strip(),
                value=(
                    f"🔁 Every {h.fmt_interval(interval)}{fired_note}\n"
                    f"{status_line} · 📬 {delivery}\n"
                    f"_Cancel: {cancel_hint}_"
                ),
                inline=False,
            )

        e.set_footer(text=f"NanoBot · {len(rows)}/{_MAX} recurring reminders")
        await ctx.reply(embed=e, ephemeral=True)

    # ── Pause ──────────────────────────────────────────────────────────────────

    async def _pause(self, ctx: commands.Context, rid: str):
        info = await self._get_owned(ctx, rid)
        if info is None:
            return

        if info["paused"]:
            return await ctx.reply(
                embed=h.warn(
                    f"Recurring reminder `{rid}` is already paused.\n"
                    f"Use `{ctx.prefix or '/'}recurring resume {rid}` to re-enable it."
                ),
                ephemeral=True,
            )

        # Cancel the running task
        task = self._tasks.pop(rid, None)
        if task:
            task.cancel()

        await db.set_recurring_paused(rid, paused=True)

        label = info.get("label") or info["message"][:60]
        await ctx.reply(
            embed=h.ok(
                f"Paused `{rid}` — _{label}_\n\n"
                f"It won't fire again until you resume it.\n"
                f"Use `{ctx.prefix or '/'}recurring resume {rid}` to re-enable.",
                "⏸️ Reminder Paused",
            ),
            ephemeral=True,
        )
        log.info(f"Recurring {rid} paused by {ctx.author}")

    # ── Resume ─────────────────────────────────────────────────────────────────

    async def _resume(self, ctx: commands.Context, rid: str):
        info = await self._get_owned(ctx, rid)
        if info is None:
            return

        if not info["paused"]:
            due_dt = datetime.fromtimestamp(info["next_due"], tz=timezone.utc)
            return await ctx.reply(
                embed=h.warn(
                    f"Recurring reminder `{rid}` is already active.\n"
                    f"Next fire: {discord.utils.format_dt(due_dt, style='R')}"
                ),
                ephemeral=True,
            )

        # Reschedule from now — don't fire immediately for resumed reminders
        now = _now()
        next_due = now + info["interval"]
        info["next_due"] = next_due
        info["paused"] = False
        await db.update_recurring(info)

        self._tasks[rid] = asyncio.create_task(
            self._fire(info, delay=next_due - _now())
        )

        due_dt = datetime.fromtimestamp(next_due, tz=timezone.utc)
        label = info.get("label") or info["message"][:60]
        await ctx.reply(
            embed=h.ok(
                f"Resumed `{rid}` — _{label}_\n\n"
                f"🔁 Every {h.fmt_interval(int(info['interval']))}\n"
                f"⏭️ Next fire: {discord.utils.format_dt(due_dt, style='R')}",
                "▶️ Reminder Resumed",
            ),
            ephemeral=True,
        )
        log.info(f"Recurring {rid} resumed by {ctx.author}")

    # ── Cancel ─────────────────────────────────────────────────────────────────

    async def _cancel(self, ctx: commands.Context, rid: str):
        info = await self._get_owned(ctx, rid)
        if info is None:
            return

        task = self._tasks.pop(rid, None)
        if task:
            task.cancel()

        await db.remove_recurring(rid)

        label = info.get("label") or info["message"][:80]
        fire_word = "time" if info["fire_count"] == 1 else "times"
        await ctx.reply(
            embed=h.ok(
                f"Cancelled `{rid}` — _{label}_\n\n"
                f"It fired **{info['fire_count']}** {fire_word} total.",
                "🗑️ Recurring Reminder Cancelled",
            ),
            ephemeral=True,
        )
        log.info(f"Recurring {rid} cancelled by {ctx.author}")

    # ── Ownership check ────────────────────────────────────────────────────────

    async def _get_owned(self, ctx: commands.Context, rid: str) -> dict | None:
        """
        Fetch a recurring reminder and verify the caller owns it.
        Sends an ephemeral error and returns None on failure.
        """
        info = await db.get_recurring(rid)
        if info is None:
            await ctx.reply(
                embed=h.err(
                    f"No recurring reminder found with ID `{rid}`.\n"
                    "Use `/recurring` to see your active ones."
                ),
                ephemeral=True,
            )
            return None

        if info["target_id"] != str(ctx.author.id):
            await ctx.reply(
                embed=h.err("You can only manage your own recurring reminders."),
                ephemeral=True,
            )
            return None

        return info


# ── Registration ───────────────────────────────────────────────────────────────


async def setup(bot: commands.Bot):
    await bot.add_cog(Recurring(bot))
