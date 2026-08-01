"""
web/api/moderation.py
Warn, timeout, kick, ban, unban, purge — behind the framework in `web/moderation.py`.

Every handler here is four lines of its own logic wrapped around
`moderation.authorise`, which runs the six checks before anything reaches
Discord. That shape is the point: a handler cannot perform an action without
having called the thing that decides whether it may, because `authorise` is what
returns the actor object the handler then needs.

Three rules on top of the framework:

* **A reason is mandatory** on every action, and it is written into Discord's
  audit log naming the dashboard, so an incident review can tell where an action
  came from and not just who took it.
* **Every action is logged twice** — once in Discord's audit log (by the API
  call itself) and once in the server's own log channel, best-effort, so it
  appears in the same feed as the actions taken from chat.
* **Purge is capped and channel-scoped.** Discord's bulk delete only reaches 14
  days and 100 messages; the endpoint refuses more rather than silently doing
  less, and it never crosses channels.

`GET /moderation` is what the page paints itself with: every action, whether the
admin can run it, whether the *bot* can, and why not. An action nobody can
perform is shown greyed with the reason rather than hidden.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING

import discord
from aiohttp import web

from utils import db

from .. import http as H
from .. import moderation as mod

if TYPE_CHECKING:
    from ..app import Dashboard

log = logging.getLogger("NanoBot.dashboard.moderation")

PURGE_FILTERS = ("anyone", "humans", "bots", "nanobot")


def routes(dash: "Dashboard") -> list:
    @H.require_manager
    async def capabilities(request: web.Request) -> web.Response:
        """What this admin can actually do here, and why not where they can't."""
        guild = H.guild_of(request)
        actor = await dash.member_of(request, guild)
        me = guild.me
        return H.ok(
            {
                "actions": mod.describe(
                    me.guild_permissions.value if me else 0,
                    actor.guild_permissions.value if actor else 0,
                ),
                "limits": {
                    "purge_max": mod.MAX_PURGE,
                    "timeout_max": mod.MAX_TIMEOUT_SECONDS,
                    "reason_max": mod.REASON_MAX,
                },
                "purge_filters": [
                    {"key": "anyone", "label": "Anyone"},
                    {"key": "humans", "label": "People only"},
                    {"key": "bots", "label": "Bots only"},
                    {"key": "nanobot", "label": "NanoBot's own messages"},
                ],
                "warn_config": await db.get_warn_config(guild.id),
                "safety": (
                    "Every action here is the same one the Discord command "
                    "performs, with the same permission and role-hierarchy "
                    "checks. Reasons are required and land in the audit log."
                ),
            }
        )

    @H.require_manager
    async def recent(request: web.Request) -> web.Response:
        """A member's warning history — the read that makes a warn decision."""
        guild = H.guild_of(request)
        raw = request.query.get("member")
        if not raw or not raw.isdigit():
            raise H.bad_request("Which member?")
        warnings = await db.get_warnings(guild.id, int(raw))
        return H.ok({"warnings": warnings, "count": len(warnings)})

    # ── Actions ──────────────────────────────────────────────────────────────
    @H.require_manager
    async def warn(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        body = await H.body_json(request)
        target = await _member(guild, body.get("member"))
        reason = mod.check_reason(body.get("reason"))
        actor = await mod.authorise(dash, request, guild, "warn", target)

        now = dt.datetime.now(dt.timezone.utc)
        count = await db.add_warning(
            guild.id, target.id, reason, str(actor.id), str(actor), now.isoformat()
        )
        await mod.log_action(dash, guild, actor, "warn", target=target, detail=reason)
        # The auto-kick/ban thresholds are the warnings cog's, and firing them
        # from here would be a second implementation of an escalation policy.
        # The count comes back so the page can say how close they are.
        cfg = await db.get_warn_config(guild.id)
        return H.ok(
            {
                "warnings": count,
                "thresholds": {"kick": cfg.get("kick_at"), "ban": cfg.get("ban_at")},
                "member": _member_payload(target),
            }
        )

    @H.require_manager
    async def timeout(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        body = await H.body_json(request)
        target = await _member(guild, body.get("member"))
        reason = mod.check_reason(body.get("reason"))
        seconds = _duration(body.get("seconds"))
        actor = await mod.authorise(dash, request, guild, "timeout", target)

        try:
            await target.timeout(
                dt.timedelta(seconds=seconds), reason=mod.audit_reason(actor, reason)
            )
        except discord.Forbidden:
            raise H.forbidden(
                "Discord refused that — check NanoBot's role sits above theirs."
            ) from None
        except discord.HTTPException as exc:
            raise _discord_error(exc) from None

        await mod.log_action(
            dash,
            guild,
            actor,
            "timeout",
            target=target,
            detail=f"{reason} · {seconds // 60} min",
        )
        return H.ok({"member": _member_payload(target), "seconds": seconds})

    @H.require_manager
    async def kick(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        body = await H.body_json(request)
        target = await _member(guild, body.get("member"))
        reason = mod.check_reason(body.get("reason"))
        actor = await mod.authorise(dash, request, guild, "kick", target)
        payload = _member_payload(target)

        try:
            await target.kick(reason=mod.audit_reason(actor, reason))
        except discord.Forbidden:
            raise H.forbidden(
                "Discord refused that — check the role hierarchy."
            ) from None
        except discord.HTTPException as exc:
            raise _discord_error(exc) from None

        await mod.log_action(dash, guild, actor, "kick", target=target, detail=reason)
        return H.ok({"member": payload})

    @H.require_manager
    async def ban(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        body = await H.body_json(request)
        target = await _member(guild, body.get("member"))
        reason = mod.check_reason(body.get("reason"))
        # Days of their messages to remove. Discord's own range; defaulting to 0
        # because deleting somebody's history is a separate decision from
        # removing them, and it should be asked for rather than assumed.
        delete_days = _int(body.get("delete_days", 0), "message days", lo=0, hi=7)
        actor = await mod.authorise(dash, request, guild, "ban", target)
        payload = _member_payload(target)

        try:
            await guild.ban(
                target,
                reason=mod.audit_reason(actor, reason),
                delete_message_days=delete_days,
            )
        except discord.Forbidden:
            raise H.forbidden(
                "Discord refused that — check the role hierarchy."
            ) from None
        except discord.HTTPException as exc:
            raise _discord_error(exc) from None

        await mod.log_action(
            dash,
            guild,
            actor,
            "ban",
            target=target,
            detail=reason
            + (f" · removed {delete_days}d of messages" if delete_days else ""),
        )
        return H.ok({"member": payload, "delete_days": delete_days})

    @H.require_manager
    async def unban(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        body = await H.body_json(request)
        reason = mod.check_reason(body.get("reason"))
        actor = await mod.authorise(dash, request, guild, "unban", None)
        raw = body.get("member")
        try:
            user_id = int(raw)
        except (TypeError, ValueError):
            raise H.bad_request("Which account?") from None

        try:
            user = await dash.bot.fetch_user(user_id)
            await guild.unban(user, reason=mod.audit_reason(actor, reason))
        except discord.NotFound:
            raise H.not_found("That account isn't banned here.") from None
        except discord.Forbidden:
            raise H.forbidden("NanoBot can't manage bans in this server.") from None
        except discord.HTTPException as exc:
            raise _discord_error(exc) from None

        await mod.log_action(dash, guild, actor, "unban", target=user, detail=reason)
        return H.ok({"member": {"id": str(user_id), "name": str(user)}})

    @H.require_manager
    async def bans(request: web.Request) -> web.Response:
        """The ban list, so unbanning is a pick rather than an id you must find."""
        guild = H.guild_of(request)
        await mod.authorise(dash, request, guild, "unban", None)
        rows = []
        try:
            async for entry in guild.bans(limit=100):
                rows.append(
                    {
                        "id": str(entry.user.id),
                        "name": str(entry.user),
                        "avatar": str(entry.user.display_avatar.url),
                        "reason": entry.reason,
                    }
                )
        except discord.Forbidden:
            raise H.forbidden("NanoBot can't read the ban list here.") from None
        return H.ok({"bans": rows})

    @H.require_manager
    async def purge(request: web.Request) -> web.Response:
        """Delete recent messages in one channel.

        Deliberately the *fast* path only — Discord's bulk delete, 100 messages,
        nothing older than 14 days. `/purge mode:slow` exists in chat behind a
        typed confirmation code because it deletes up to 500 at any age one call
        at a time; that is not something to run from a web form, so it stays in
        Discord where the confirmation lives.
        """
        guild = H.guild_of(request)
        body = await H.body_json(request)
        reason = mod.check_reason(body.get("reason"))
        amount = _int(body.get("amount"), "amount", lo=1, hi=mod.MAX_PURGE)
        only = str(body.get("only") or "anyone")
        if only not in PURGE_FILTERS:
            raise H.bad_request("That isn't one of the filters.")
        actor = await mod.authorise(dash, request, guild, "purge", None)

        channel = _channel(guild, body.get("channel_id"))
        me = guild.me
        perms = channel.permissions_for(me) if me else None
        if not perms or not perms.manage_messages:
            raise H.forbidden(f"NanoBot can't delete messages in #{channel.name}.")

        bot_id = dash.bot.user.id if dash.bot.user else 0

        def keep(message: discord.Message) -> bool:
            if only == "humans":
                return not message.author.bot
            if only == "bots":
                return message.author.bot
            if only == "nanobot":
                return message.author.id == bot_id
            return True

        try:
            deleted = await channel.purge(
                limit=amount, check=keep, reason=mod.audit_reason(actor, reason)
            )
        except discord.Forbidden:
            raise H.forbidden(
                f"NanoBot can't delete messages in #{channel.name}."
            ) from None
        except discord.HTTPException as exc:
            raise _discord_error(exc) from None

        await mod.log_action(
            dash,
            guild,
            actor,
            "purge",
            detail=f"{len(deleted)} message(s) in #{channel.name} · {reason}",
        )
        return H.ok(
            {
                "deleted": len(deleted),
                "channel": {"id": str(channel.id), "name": channel.name},
                "note": (
                    "Discord's bulk delete only reaches messages from the last "
                    "14 days, so older ones were skipped."
                    if len(deleted) < amount
                    else None
                ),
            }
        )

    return [
        web.get("/api/guilds/{guild_id}/moderation", capabilities),
        web.get("/api/guilds/{guild_id}/moderation/warnings", recent),
        web.get("/api/guilds/{guild_id}/moderation/bans", bans),
        web.post("/api/guilds/{guild_id}/moderation/warn", warn),
        web.post("/api/guilds/{guild_id}/moderation/timeout", timeout),
        web.post("/api/guilds/{guild_id}/moderation/kick", kick),
        web.post("/api/guilds/{guild_id}/moderation/ban", ban),
        web.post("/api/guilds/{guild_id}/moderation/unban", unban),
        web.post("/api/guilds/{guild_id}/moderation/purge", purge),
    ]


# ── helpers ───────────────────────────────────────────────────────────────────
async def _member(guild: discord.Guild, raw) -> discord.Member:
    try:
        uid = int(raw)
    except (TypeError, ValueError):
        raise H.bad_request("Pick a member.") from None
    member = guild.get_member(uid)
    if member is None:
        try:
            member = await guild.fetch_member(uid)
        except Exception:
            raise H.not_found("They're not in this server.") from None
    return member


def _member_payload(member) -> dict:
    return {
        "id": str(member.id),
        "name": getattr(member, "display_name", str(member)),
        "handle": getattr(member, "name", str(member)),
        "avatar": str(member.display_avatar.url),
    }


def _channel(guild: discord.Guild, raw) -> discord.TextChannel:
    try:
        cid = int(raw)
    except (TypeError, ValueError):
        raise H.bad_request("Pick a channel.") from None
    channel = next((c for c in guild.text_channels if c.id == cid), None)
    if channel is None:
        raise H.bad_request("That channel isn't in this server any more.")
    return channel


def _duration(raw) -> int:
    """Discord's own bounds: a minute at least, 28 days at most."""
    return _int(raw, "duration", lo=60, hi=mod.MAX_TIMEOUT_SECONDS)


def _int(value, field: str, *, lo: int, hi: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise H.bad_request(f"“{field}” has to be a whole number.") from None
    if not lo <= number <= hi:
        raise H.bad_request(f"“{field}” has to be between {lo:,} and {hi:,}.")
    return number


def _discord_error(exc: discord.HTTPException) -> H.ApiError:
    """Surface Discord's own complaint rather than a generic failure."""
    log.warning("Discord refused a moderation action: %s", exc)
    return H.ApiError(
        502,
        "discord_error",
        f"Discord refused that: {getattr(exc, 'text', None) or exc}",
    )
