"""Timed-action scheduling for the moderation cog (auto-unban, auto-unslow).

Lives as a mixin so the cog class stays focused on commands. State (the task
dicts) is created in Moderation.__init__; restore is driven by the cog's
on_restore_schedules listener.
"""

import asyncio
import logging
from datetime import datetime, timezone

import discord

from utils import db

from .helpers import _chunked_sleep

log = logging.getLogger("NanoBot.moderation")


class TimedActionsMixin:
    """Auto-unban / auto-unslow scheduling, persisted in SQLite."""

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
                    except (discord.Forbidden, discord.HTTPException) as exc:
                        log.warning(f"Overdue unslow failed for {channel_id}: {exc}")
                await db.remove_slow(channel_id)

    async def _auto_unban(self, guild_id, user_id, delay):
        # Cleanup runs only on non-cancelled completion: when a scheduled task
        # is cancelled (reschedule / manual unban) the caller owns cleanup, and
        # running it here would clobber the replacement task's state.
        await _chunked_sleep(delay)
        guild = self.bot.get_guild(guild_id)
        if guild:
            try:
                await guild.unban(
                    discord.Object(id=user_id),
                    reason="NanoBot: Timed unban complete",
                )
                log.info(f"Timed unban: {user_id} in {guild_id}")
            except discord.NotFound:
                pass
            except discord.HTTPException as exc:
                log.warning(f"Timed unban failed for {user_id} in {guild_id}: {exc}")
        key = f"{guild_id}:{user_id}"
        self._unban_tasks.pop(key, None)
        await db.remove_unban(key)

    async def _auto_unslow(self, channel_id, delay):
        await _chunked_sleep(delay)
        ch = self.bot.get_channel(channel_id)
        if ch:
            try:
                await ch.edit(
                    slowmode_delay=0, reason="NanoBot: Timed slowmode expired"
                )
                log.info(f"Timed slowmode removed: #{ch}")
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.warning(f"Timed unslow failed for {channel_id}: {exc}")
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
