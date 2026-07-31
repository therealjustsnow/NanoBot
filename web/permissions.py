"""
web/permissions.py
Who may see and change what, expressed as pure functions over plain data.

The dashboard's authorisation question is the same one every `/…` admin command
asks — "does this member have Manage Server here?" — and the answer has to match
exactly, because a setting changed on the web is the setting Discord reads. So
the gate is Discord's own permission bits rather than a dashboard-specific role
model: there is no such thing as dashboard-only access, and nothing here can
grant someone a power they don't already have in the server.

Kept Discord-free (ints and dicts in, dicts out) so it is unit-testable and so a
route handler can't quietly reimplement the check inline.

The second half of the file is the **permission map**: which Discord permissions
each dashboard feature needs the *bot* to hold. It exists because the most
common "the bot is broken" report is a missing permission, and a dashboard is
the one place that can say so before a setting is saved rather than after it
silently fails at 3am.
"""

from __future__ import annotations

from typing import Iterable, Optional

# Discord permission bits (docs: Permissions → Bitwise Permission Flags).
ADMINISTRATOR = 1 << 3
MANAGE_GUILD = 1 << 5
KICK_MEMBERS = 1 << 1
BAN_MEMBERS = 1 << 2
MANAGE_CHANNELS = 1 << 4
MANAGE_MESSAGES = 1 << 13
MANAGE_ROLES = 1 << 28
MODERATE_MEMBERS = 1 << 40
VIEW_AUDIT_LOG = 1 << 7
SEND_MESSAGES = 1 << 11
EMBED_LINKS = 1 << 14
ATTACH_FILES = 1 << 15
READ_MESSAGE_HISTORY = 1 << 16
MANAGE_THREADS = 1 << 34
CREATE_PRIVATE_THREADS = 1 << 36
CONNECT = 1 << 20
SPEAK = 1 << 21


def has_perm(permissions: int, bit: int) -> bool:
    """Whether a permission integer carries a bit — Administrator implying all.

    Administrator overriding everything is Discord's rule, not ours; a dashboard
    that ignored it would refuse the one person who can never actually be
    refused, which reads as a bug.
    """
    try:
        value = int(permissions)
    except (TypeError, ValueError):
        return False
    if value & ADMINISTRATOR:
        return True
    return bool(value & bit)


def can_manage(permissions: int, *, is_owner: bool = False) -> bool:
    """Whether someone may change this server's NanoBot settings.

    Manage Server is the gate every `/…` admin command in the bot uses, so the
    dashboard uses it too and nothing more: a member who cannot run `/level set`
    in Discord cannot reach it here either.
    """
    return bool(is_owner) or has_perm(permissions, MANAGE_GUILD)


def guild_summary(
    oauth_guild: dict,
    bot_guild_ids: Iterable[int],
    *,
    bot_owner: bool = False,
) -> dict:
    """One row for the server picker.

    `managed` and `present` are separate answers to separate questions, and the
    picker needs both: a server the user manages but the bot isn't in is an
    *invite* button, while a server the bot is in but the user doesn't manage is
    somewhere they can still play the economy. Collapsing them into one boolean
    is what makes bot dashboards feel like they've lost your server.
    """
    try:
        gid = int(oauth_guild.get("id"))
    except (TypeError, ValueError):
        gid = 0
    perms = oauth_guild.get("permissions") or 0
    owner = bool(oauth_guild.get("owner"))
    return {
        "id": str(gid),
        "name": oauth_guild.get("name") or "Unknown server",
        "icon": oauth_guild.get("icon"),
        "owner": owner,
        "managed": bool(bot_owner) or can_manage(perms, is_owner=owner),
        "present": gid in set(int(g) for g in bot_guild_ids),
    }


def manageable_guilds(
    oauth_guilds: Iterable[dict],
    bot_guild_ids: Iterable[int],
    *,
    bot_owner: bool = False,
) -> list[dict]:
    """Every server the picker should show, most useful first.

    Sort order is the picker's whole ergonomics on a phone: servers you can
    actually configure right now, then ones needing an invite, then the rest,
    each group alphabetical. Scrolling past forty servers you have no power in
    to reach the one you do is the thing this avoids.
    """
    present = set(int(g) for g in bot_guild_ids)
    rows = [guild_summary(g, present, bot_owner=bot_owner) for g in oauth_guilds]
    rows.sort(
        key=lambda r: (
            0 if (r["managed"] and r["present"]) else 1 if r["managed"] else 2,
            r["name"].casefold(),
        )
    )
    return rows


# ══════════════════════════════════════════════════════════════════════════════
#  What each feature needs the bot to be able to do
#
#  One entry per dashboard feature area. `required` is what the feature cannot
#  work without at all; `optional` degrades a specific capability rather than
#  the feature. The dashboard renders this as a checklist against the bot's real
#  permissions in the guild, so a misconfiguration is visible on the page that
#  configures it rather than discovered from a member's bug report.
# ══════════════════════════════════════════════════════════════════════════════
PERMISSION_LABELS: dict[int, str] = {
    ADMINISTRATOR: "Administrator",
    MANAGE_GUILD: "Manage Server",
    KICK_MEMBERS: "Kick Members",
    BAN_MEMBERS: "Ban Members",
    MANAGE_CHANNELS: "Manage Channels",
    MANAGE_MESSAGES: "Manage Messages",
    MANAGE_ROLES: "Manage Roles",
    MODERATE_MEMBERS: "Timeout Members",
    VIEW_AUDIT_LOG: "View Audit Log",
    SEND_MESSAGES: "Send Messages",
    EMBED_LINKS: "Embed Links",
    ATTACH_FILES: "Attach Files",
    READ_MESSAGE_HISTORY: "Read Message History",
    MANAGE_THREADS: "Manage Threads",
    CREATE_PRIVATE_THREADS: "Create Private Threads",
    CONNECT: "Connect",
    SPEAK: "Speak",
}

FEATURE_PERMISSIONS: dict[str, dict] = {
    "moderation": {
        "label": "Moderation",
        "required": [KICK_MEMBERS, BAN_MEMBERS, MODERATE_MEMBERS, MANAGE_MESSAGES],
        "optional": [MANAGE_CHANNELS],
        "why": "Ban, kick, timeout and purge each map to the matching Discord "
        "permission. Manage Channels is only needed for lock and slowmode.",
    },
    "automod": {
        "label": "AutoMod",
        "required": [MANAGE_MESSAGES],
        "optional": [MODERATE_MEMBERS, KICK_MEMBERS, BAN_MEMBERS],
        "why": "Deleting a rule-breaking message needs Manage Messages. The "
        "optional ones are only used by the actions you actually pick.",
    },
    "gatekeeper": {
        "label": "Gatekeeper",
        "required": [MANAGE_ROLES, KICK_MEMBERS],
        "optional": [],
        "why": "New accounts are muted with a role and kicked if they never "
        "verify. NanoBot's own role must also sit above the mute role.",
    },
    "welcome": {
        "label": "Welcome & Leave",
        "required": [SEND_MESSAGES, EMBED_LINKS],
        "optional": [ATTACH_FILES],
        "why": "Greetings are embeds. Attach Files is only needed when the "
        "message carries an image.",
    },
    "roles": {
        "label": "Self Roles",
        "required": [MANAGE_ROLES, SEND_MESSAGES, EMBED_LINKS],
        "optional": [],
        "why": "Every role a panel offers has to sit below NanoBot's own "
        "highest role, or Discord refuses the assignment.",
    },
    "auditlog": {
        "label": "Logging",
        "required": [SEND_MESSAGES, EMBED_LINKS],
        "optional": [VIEW_AUDIT_LOG],
        "why": "View Audit Log lets a log entry name who performed an action "
        "rather than only that it happened.",
    },
    "leveling": {
        "label": "Leveling",
        "required": [SEND_MESSAGES, EMBED_LINKS],
        "optional": [MANAGE_ROLES],
        "why": "Manage Roles is only needed if you hand out roles as level " "rewards.",
    },
    "economy": {
        "label": "Economy",
        "required": [SEND_MESSAGES, EMBED_LINKS, ATTACH_FILES],
        "optional": [MANAGE_ROLES],
        "why": "Wallet and profile cards are rendered images. Manage Roles is "
        "needed only for shop items that grant a role.",
    },
    "tickets": {
        "label": "Tickets",
        "required": [
            SEND_MESSAGES,
            EMBED_LINKS,
            CREATE_PRIVATE_THREADS,
            READ_MESSAGE_HISTORY,
        ],
        "optional": [MANAGE_THREADS],
        "why": "Tickets are private threads. Manage Threads is what lets a "
        "closed ticket be locked as well as archived.",
    },
    "birthday": {
        "label": "Birthdays",
        "required": [SEND_MESSAGES, EMBED_LINKS],
        "optional": [CONNECT, SPEAK],
        "why": "Connect and Speak are only used by the optional birthday song.",
    },
    "music": {
        "label": "Music",
        "required": [CONNECT, SPEAK, SEND_MESSAGES, EMBED_LINKS],
        "optional": [],
        "why": "NanoBot joins the voice channel the requester is in.",
    },
}


def permission_report(feature: str, bot_permissions: int) -> Optional[dict]:
    """Check one feature's needs against what the bot actually holds.

    Returns None for an unknown feature so a caller iterating a list of feature
    keys can't be tripped up by a typo turning into a false "all good".
    """
    spec = FEATURE_PERMISSIONS.get(feature)
    if not spec:
        return None

    def rows(bits: list[int]) -> list[dict]:
        return [
            {
                "bit": bit,
                "name": PERMISSION_LABELS.get(bit, f"Permission {bit}"),
                "granted": has_perm(bot_permissions, bit),
            }
            for bit in bits
        ]

    required = rows(spec["required"])
    optional = rows(spec["optional"])
    missing = [r["name"] for r in required if not r["granted"]]
    return {
        "feature": feature,
        "label": spec["label"],
        "why": spec["why"],
        "required": required,
        "optional": optional,
        "missing": missing,
        "degraded": [r["name"] for r in optional if not r["granted"]],
        "ok": not missing,
    }


def permission_reports(bot_permissions: int) -> list[dict]:
    """Every feature's report, the ones needing attention first."""
    reports = [
        r
        for r in (permission_report(k, bot_permissions) for k in FEATURE_PERMISSIONS)
        if r
    ]
    reports.sort(key=lambda r: (r["ok"], not r["degraded"], r["label"]))
    return reports
