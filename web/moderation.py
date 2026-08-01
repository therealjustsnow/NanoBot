"""
web/moderation.py
The safety framework destructive actions go through — and only then the actions.

Moderation is the highest-blast-radius surface a dashboard can expose, so the
order here is deliberate: the framework first, the verbs after it, and every
verb goes through the framework rather than around it.

Six checks stand between a request and Discord, in this order, because each one
makes the next cheaper to reason about:

  1. **The actor holds the permission.** Not "is a manager" — the *specific*
     Discord permission for the action (Ban Members to ban), read live off the
     gateway, exactly as the `/ban` command's own check reads it.
  2. **The bot holds it too.** `utils/checks.py` exists because checking only
     the human produces a command that passes its check and then 403s.
  3. **Role hierarchy, for the actor.** You cannot act on somebody above you.
     Discord's rule, and the guild owner is exempt from it.
  4. **Role hierarchy, for the bot.** Discord 403s when the bot's top role isn't
     strictly above the target's, permission node or not.
  5. **Sanity.** Not yourself, not the bot, not the server owner.
  6. **A reason.** Required, not optional: it goes in the audit log, and an
     action nobody can explain in six months is one nobody should have taken.

Everything raises `H.forbidden` with a sentence naming *which* check failed,
because "you can't do that" is the least useful thing a moderation tool can say.

This module is deliberately Discord-aware (it takes members and calls the API)
and therefore lives outside `web/engine/`, which is the Discord-free half.
"""

from __future__ import annotations

import logging
from typing import Optional

import discord

from . import http as H
from . import permissions as perms

log = logging.getLogger("NanoBot.dashboard.moderation")

# What each action needs, and what it is called when refusing. `bulk` marks the
# ones that touch more than one thing at a time and therefore need the extra
# confirmation the frontend asks for.
ACTIONS: dict[str, dict] = {
    "warn": {
        "label": "Warn",
        "permission": perms.MANAGE_MESSAGES,
        "permission_name": "Manage Messages",
        "destructive": False,
        "hierarchy": True,
        "blurb": "Recorded against the member. Auto-kick/ban thresholds still apply.",
    },
    "timeout": {
        "label": "Timeout",
        "permission": perms.MODERATE_MEMBERS,
        "permission_name": "Timeout Members",
        "destructive": True,
        "hierarchy": True,
        "blurb": "They can read but not talk until it expires. Reversible.",
    },
    "kick": {
        "label": "Kick",
        "permission": perms.KICK_MEMBERS,
        "permission_name": "Kick Members",
        "destructive": True,
        "hierarchy": True,
        "blurb": "They can rejoin with a new invite. Not reversible by itself.",
    },
    "ban": {
        "label": "Ban",
        "permission": perms.BAN_MEMBERS,
        "permission_name": "Ban Members",
        "destructive": True,
        "hierarchy": True,
        "blurb": "They cannot rejoin until unbanned. Optionally deletes their "
        "recent messages.",
    },
    "unban": {
        "label": "Unban",
        "permission": perms.BAN_MEMBERS,
        "permission_name": "Ban Members",
        "destructive": False,
        "hierarchy": False,
        "blurb": "Lets a banned account rejoin with a new invite.",
    },
    "purge": {
        "label": "Purge",
        "permission": perms.MANAGE_MESSAGES,
        "permission_name": "Manage Messages",
        "destructive": True,
        "hierarchy": False,
        "bulk": True,
        "blurb": "Deletes recent messages in one channel. Nothing deleted can "
        "be recovered.",
    },
}

# Discord's own limits, restated so a refusal can be specific rather than a 400
# bounced back from the API.
MAX_PURGE = 100
MAX_TIMEOUT_SECONDS = 28 * 86400
REASON_MIN = 3
REASON_MAX = 400


def describe(bot_permissions: int, actor_permissions: int) -> list[dict]:
    """Every action, with whether *this* admin could run it right now.

    Rendered as the moderation page's own capability list: an action nobody can
    perform is shown greyed with the reason, rather than hidden — "why can't I
    ban from here?" is a question the page should answer without being asked.
    """
    out = []
    for key, spec in ACTIONS.items():
        you = perms.has_perm(actor_permissions, spec["permission"])
        bot = perms.has_perm(bot_permissions, spec["permission"])
        out.append(
            {
                "key": key,
                "label": spec["label"],
                "blurb": spec["blurb"],
                "destructive": spec["destructive"],
                "bulk": bool(spec.get("bulk")),
                "permission": spec["permission_name"],
                "you_can": you,
                "bot_can": bot,
                "available": you and bot,
                "why_not": (
                    None
                    if (you and bot)
                    else (
                        f"You need {spec['permission_name']} in this server."
                        if not you
                        else f"NanoBot is missing {spec['permission_name']}."
                    )
                ),
            }
        )
    return out


def check_reason(raw) -> str:
    """A reason is required. It lands in Discord's audit log verbatim."""
    reason = (raw or "").strip() if isinstance(raw, str) else ""
    if len(reason) < REASON_MIN:
        raise H.bad_request(
            "Give a reason — it goes in the audit log, and future-you will want it.",
            hint="reason",
        )
    if len(reason) > REASON_MAX:
        raise H.bad_request(f"Keep the reason under {REASON_MAX} characters.")
    return reason


async def authorise(
    dash,
    request,
    guild: discord.Guild,
    action: str,
    target: Optional[discord.Member] = None,
) -> discord.Member:
    """Run every check for one action, and hand back the acting member.

    Returns the actor rather than a boolean so a caller has to have called this
    to have the object it needs next — a guard you can forget is a guard that
    gets forgotten.
    """
    spec = ACTIONS.get(action)
    if spec is None:
        raise H.not_found("That isn't a moderation action.")

    actor = await dash.member_of(request, guild)
    if actor is None:
        raise H.forbidden("You're not in that server.")

    # 1. The actor's own permission — the specific one, not "manager".
    is_owner = guild.owner_id == actor.id
    if not is_owner and not perms.has_perm(
        actor.guild_permissions.value, spec["permission"]
    ):
        raise H.forbidden(
            f"You need {spec['permission_name']} in this server to do that.",
            hint="Ask an admin to grant it, or to make the change for you.",
        )

    # 2. The bot's permission. Checking only the human ships a button that
    #    passes its own check and then 403s.
    me = guild.me
    if me is None or not perms.has_perm(me.guild_permissions.value, spec["permission"]):
        raise H.forbidden(
            f"NanoBot is missing {spec['permission_name']} in this server, so it "
            "can't carry that out.",
            hint="Server Settings → Roles → NanoBot.",
        )

    if target is not None and spec.get("hierarchy"):
        # 5. Sanity first — these read better than a hierarchy refusal would.
        if target.id == actor.id:
            raise H.bad_request("You can't do that to yourself.")
        if target.id == (dash.bot.user.id if dash.bot.user else 0):
            raise H.bad_request("NanoBot isn't going to do that to itself.")
        if target.id == guild.owner_id:
            raise H.forbidden("Nobody can moderate the server owner.")

        # 3. Actor hierarchy — Discord's rule, owner exempt.
        if not is_owner and actor.top_role <= target.top_role:
            raise H.forbidden(
                f"@{target.display_name} has a role at or above yours, so you "
                "can't act on them.",
            )
        # 4. Bot hierarchy — Discord 403s regardless of the permission node.
        if me.top_role <= target.top_role:
            raise H.forbidden(
                f"NanoBot's role sits at or below @{target.display_name}'s, so "
                "Discord would refuse this.",
                hint="Drag NanoBot's role higher in Server Settings → Roles.",
            )
    return actor


def audit_reason(actor: discord.Member, reason: str) -> str:
    """What Discord's audit log records.

    Names the dashboard explicitly. Somebody reading the audit log six months
    from now should be able to tell *where* an action came from, not just who
    did it — the two are different questions during an incident.
    """
    return f"{actor} via NanoBot dashboard: {reason}"[:512]


async def log_action(
    dash,
    guild: discord.Guild,
    actor: discord.Member,
    action: str,
    *,
    target: Optional[discord.abc.User] = None,
    detail: str = "",
) -> None:
    """Write the action to the server's own audit-log channel.

    Best-effort and never fatal: the action already happened, and failing the
    request because the *log* couldn't be written would tell the admin the
    opposite of the truth. The failure goes to the bot's log instead.
    """
    from utils import db
    from utils import helpers as h

    try:
        cfg = await db.get_auditlog_config(guild.id)
        if not cfg or not cfg.get("enabled") or not cfg.get("channel_id"):
            return
        channel = guild.get_channel(int(cfg["channel_id"]))
        if channel is None:
            return
        spec = ACTIONS.get(action, {})
        if target is not None:
            embed = h.mod_action_embed(
                f"🖥️ {spec.get('label', action.title())}",
                target,
                detail or "No reason given",
                moderator=actor,
                color=h.GREY,
            )
        else:
            embed = discord.Embed(
                description=f"🖥️ **{actor.display_name}** used **{action}** "
                f"from the dashboard\n{detail}",
                color=h.GREY,
            )
            embed.timestamp = discord.utils.utcnow()
        embed.set_footer(text="NanoBot dashboard")
        await channel.send(embed=embed)
    except Exception:
        log.warning(
            "Couldn't write the dashboard action to the log channel", exc_info=True
        )
