"""AutoMod side-effects: log-channel resolution, the action-log embed, the
delete/warn/timeout/kick/softban executor, and soft-delete of notices.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord.ext import commands

from utils import db
from utils import helpers as h
from utils import logfeed

from .constants import ACTION_LABELS, RULE_LABELS, TIMEOUT_SECONDS

log = logging.getLogger("NanoBot.automod")


async def _get_automod_log_channel(
    bot: commands.Bot,
    guild: discord.Guild,
    am_cfg: dict,
) -> discord.TextChannel | None:
    """Resolve the channel for automod action logs.

    Priority:
      1. Dedicated automod log channel (am_cfg["log_channel_id"])
      2. Audit log channel, if the automod_action event is toggled on there
    """
    log_ch_id = am_cfg.get("log_channel_id")
    if log_ch_id:
        ch = guild.get_channel(int(log_ch_id))
        if isinstance(ch, discord.TextChannel):
            return ch

    # Fall back to audit log if automod_action event is enabled
    al_cfg = await db.get_auditlog_config(guild.id)
    if not al_cfg or not al_cfg["enabled"]:
        return None
    if "automod_action" not in al_cfg["events"]:
        return None
    if not al_cfg["channel_id"]:
        return None
    ch = guild.get_channel(int(al_cfg["channel_id"]))
    return ch if isinstance(ch, discord.TextChannel) else None


async def _send_action_log(
    bot: commands.Bot,
    message: discord.Message,
    action: str,
    rule: str,
    detail: str,
) -> None:
    """Post an automod action embed to the configured log channel."""
    am_cfg = await db.get_automod_config(message.guild.id)
    if am_cfg is None:
        return
    ch = await _get_automod_log_channel(bot, message.guild, am_cfg)
    if ch is None:
        return
    member = message.author
    e = discord.Embed(title="🛡️ AutoMod Action", color=h.YELLOW)
    e.add_field(
        name="User",
        value=f"{member.mention} (`{member.id}`)",
        inline=True,
    )
    e.add_field(name="Channel", value=message.channel.mention, inline=True)
    e.add_field(name="Rule", value=RULE_LABELS.get(rule, rule), inline=True)
    e.add_field(name="Action", value=ACTION_LABELS.get(action, action), inline=True)
    e.add_field(name="Reason", value=detail[:512], inline=False)
    e.add_field(name="Moderator", value="NanoBot (automated)", inline=True)
    e.set_footer(text=f"NanoBot AutoMod  •  User ID: {member.id}")
    e.timestamp = discord.utils.utcnow()
    # Through the shared feed, not ch.send: AutoMod's busiest moment is a
    # spammer flooding a channel, which is precisely when it would post one
    # message per caught message and rate-limit itself. It also shares a channel
    # with the audit log whenever no dedicated logchannel is set, so the two
    # have to be spaced together rather than each on its own.
    logfeed.post(ch, e)


# ── Action executor ────────────────────────────────────────────────────────────


async def _execute_action(
    message: discord.Message,
    action: str,
    rule: str,
    detail: str,
    timeout_seconds: int = TIMEOUT_SECONDS,
    dm_message: Optional[str] = None,
    bot: Optional[commands.Bot] = None,
) -> None:
    """
    Delete the offending message and optionally warn/timeout the author.
    Silently handles permission errors so a missing perm never crashes the listener.
    """
    member = message.author
    guild = message.guild

    # Always delete first
    try:
        await message.delete()
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        pass

    # DM the user if configured — sent before any further action so kick/ban
    # don't close the DM channel before we can reach them.
    if dm_message:
        try:
            await member.send(dm_message)
        except (discord.Forbidden, discord.HTTPException):
            pass

    try:
        if action == "delete":
            return

        # Notify the user with a short ephemeral-style message that auto-deletes
        reason_text = f"AutoMod ({RULE_LABELS.get(rule, rule)}): {detail}"
        try:
            notice = await message.channel.send(
                embed=discord.Embed(
                    description=f"⚠️ {member.mention} — your message was removed.\n`{detail}`",
                    color=h.YELLOW,
                )
            )
            _spawn_soft_delete(notice, 6.0)
        except (discord.Forbidden, discord.HTTPException):
            pass

        if action in ("warn", "timeout"):
            now = datetime.now(timezone.utc)
            try:
                count = await db.add_warning(
                    guild.id,
                    member.id,
                    reason_text,
                    "AutoMod",
                    "AutoMod",
                    now.isoformat(),
                )
                log.info(
                    f"AutoMod warned {h.user_log(member)} in {guild} "
                    f"— {rule}: {detail} (warning #{count})"
                )

                # Respect warnconfig auto-kick/ban thresholds
                warn_cfg = await db.get_warn_config(guild.id)
                if warn_cfg["ban_at"] and count >= warn_cfg["ban_at"]:
                    try:
                        await guild.ban(
                            member,
                            reason=f"NanoBot auto-ban: {count} warnings (AutoMod)",
                            delete_message_days=0,
                        )
                    except discord.Forbidden:
                        pass
                elif warn_cfg["kick_at"] and count >= warn_cfg["kick_at"]:
                    try:
                        await guild.kick(
                            member,
                            reason=f"NanoBot auto-kick: {count} warnings (AutoMod)",
                        )
                    except discord.Forbidden:
                        pass

            except Exception as exc:
                log.error(f"AutoMod warn failed: {exc}", exc_info=exc)

        if action == "timeout":
            try:
                until = discord.utils.utcnow() + timedelta(seconds=timeout_seconds)
                await member.timeout(until, reason=reason_text)
                log.info(f"AutoMod timed out {h.user_log(member)} in {guild} — {rule}")
            except discord.Forbidden:
                pass
            except Exception as exc:
                log.error(f"AutoMod timeout failed: {exc}", exc_info=exc)
            return

        if action == "kick":
            try:
                await guild.kick(member, reason=reason_text)
                log.info(f"AutoMod kicked {h.user_log(member)} in {guild} — {rule}")
            except discord.Forbidden:
                pass
            except Exception as exc:
                log.error(f"AutoMod kick failed: {exc}", exc_info=exc)
            return

        if action == "softban":
            try:
                await guild.ban(
                    member,
                    reason=f"{reason_text} (softban)",
                    delete_message_days=1,
                )
                await guild.unban(member, reason=f"{reason_text} (softban release)")
                log.info(f"AutoMod softbanned {h.user_log(member)} in {guild} — {rule}")
            except discord.Forbidden:
                pass
            except Exception as exc:
                log.error(f"AutoMod softban failed: {exc}", exc_info=exc)

    finally:
        if bot is not None:
            await _send_action_log(bot, message, action, rule, detail)


# Strong refs to fire-and-forget soft-delete tasks. Without this the event loop
# only holds a weak reference and may garbage-collect the task before it runs.
_bg_tasks: set[asyncio.Task] = set()


def _spawn_soft_delete(message: discord.Message, delay: float) -> None:
    task = asyncio.create_task(_soft_delete_after(message, delay))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


async def _soft_delete_after(message: discord.Message, delay: float) -> None:
    """Delete *message* after *delay* seconds, ignoring any errors."""
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except (discord.NotFound, discord.HTTPException):
        pass
