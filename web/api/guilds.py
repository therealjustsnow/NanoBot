"""
web/api/guilds.py
A server's command centre: the overview, its channels and roles, and the
permission health check.

The overview is one request on purpose. It is the first screen after picking a
server and it has to answer "is everything alright?" — which means member count,
what's switched on, what the economy did lately, and which permissions are
missing, all at once. Splitting that into six requests would make the most
important page in the dashboard the slowest one.

Channel and role lists exist so every settings screen can offer a *picker*
rather than an id field. Typing a channel id into a web form is the same
usability failure as typing one into a Discord command, and the bot fixed that
years ago with autocomplete.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import discord
from aiohttp import web

from utils import db

from .. import http as H
from .. import permissions

if TYPE_CHECKING:
    from ..app import Dashboard

# Which cogs the overview reports on, and where each one's switch lives. The
# dashboard reads these rather than hard-coding a list of booleans so a feature
# that grows a setting doesn't need this file edited.
_FEATURE_CHECKS = (
    ("automod", "AutoMod", db.get_automod_config, "enabled"),
    ("auditlog", "Logging", db.get_auditlog_config, "enabled"),
    ("welcome", "Welcome", db.get_welcome_config, "enabled"),
    ("leave", "Leave messages", db.get_leave_config, "enabled"),
)


def routes(dash: "Dashboard") -> list:
    @H.require_member
    async def overview(request: web.Request) -> web.Response:
        guild: discord.Guild = H.guild_of(request)
        me = guild.me
        bot_perms = me.guild_permissions.value if me else 0

        features = []
        for key, label, getter, field in _FEATURE_CHECKS:
            cfg = await getter(guild.id)
            features.append(
                {
                    "key": key,
                    "label": label,
                    "configured": cfg is not None,
                    "enabled": bool(cfg and cfg.get(field)),
                }
            )
        econ = await db.get_econ_config(guild.id)
        fishing = await db.get_fishing_config(guild.id)
        activities = await db.get_activities_config(guild.id)
        level = await db.get_level_config(guild.id)
        casino = await db.get_casino_config(guild.id)
        features.extend(
            [
                {
                    "key": "leveling",
                    "label": "Leveling",
                    "configured": True,
                    "enabled": bool(level["enabled"]),
                },
                {
                    "key": "fishing",
                    "label": "Fishing",
                    "configured": True,
                    "enabled": bool(fishing["enabled"]),
                },
                {
                    "key": "casino",
                    "label": "Casino",
                    "configured": True,
                    "enabled": bool(casino["enabled"]),
                },
                {
                    "key": "activities",
                    "label": "Activities",
                    "configured": True,
                    "enabled": any(
                        activities[f"{a}_enabled"]
                        for a in ("work", "mine", "hunt", "explore", "rob")
                    ),
                },
            ]
        )

        member_ids = [m.id for m in guild.members if not m.bot]
        wallets = await db.get_econ_leaderboard_for(member_ids)
        reports = permissions.permission_reports(bot_perms)
        return H.ok(
            {
                "guild": _guild_payload(guild),
                "bot": {
                    "nickname": me.nick if me else None,
                    "joined_at": (
                        me.joined_at.timestamp() if me and me.joined_at else None
                    ),
                    "permissions": bot_perms,
                    "top_role": (
                        {"name": me.top_role.name, "position": me.top_role.position}
                        if me
                        else None
                    ),
                    "latency_ms": (
                        round(dash.bot.latency * 1000)
                        if dash.bot.latency == dash.bot.latency
                        else None
                    ),
                },
                "features": features,
                "permissions": {
                    "reports": reports,
                    "problems": sum(1 for r in reports if not r["ok"]),
                    "degraded": sum(1 for r in reports if r["ok"] and r["degraded"]),
                },
                "economy": {
                    "currency": {
                        "name": econ["currency_name"],
                        "emoji": econ["currency_emoji"],
                    },
                    # The scoped accessors take no limit: the database does the
                    # membership filter and the caller does the ranking, which
                    # is the split every server-scoped board in the bot uses.
                    "members_with_coins": len(wallets),
                    "top": _named(guild, _top(wallets, "coins", 5)),
                    "shop_items": await db.count_shop_items(guild.id),
                    "pending_rewards": await db.count_pending_purchases(guild.id),
                    "jackpot": casino["jackpot_pool"],
                },
                "activity": await _activity_summary(guild, member_ids),
                "can_manage": await _can_manage(dash, request, guild),
            }
        )

    @H.require_manager
    async def channels(request: web.Request) -> web.Response:
        """Text channels the bot can actually post in, grouped by category.

        Filtered rather than listed in full, because a picker offering a channel
        the bot can't write to is a setting that saves cleanly and then silently
        does nothing — the exact failure the permission panel exists to prevent.
        """
        guild: discord.Guild = H.guild_of(request)
        me = guild.me
        rows = []
        for channel in guild.text_channels:
            perms = channel.permissions_for(me) if me else None
            rows.append(
                {
                    "id": str(channel.id),
                    "name": channel.name,
                    "category": channel.category.name if channel.category else None,
                    "position": channel.position,
                    "nsfw": channel.is_nsfw(),
                    "can_send": bool(
                        perms and perms.send_messages and perms.view_channel
                    ),
                    "can_embed": bool(perms and perms.embed_links),
                }
            )
        rows.sort(key=lambda r: (r["category"] or "", r["position"]))
        return H.ok({"channels": rows})

    @H.require_manager
    async def roles(request: web.Request) -> web.Response:
        """Roles, with a flag for whether the bot could actually assign each one.

        `assignable` is the whole point: Discord refuses a role at or above the
        bot's own highest, and a self-role panel offering one is a button that
        errors for every member who presses it.
        """
        guild: discord.Guild = H.guild_of(request)
        me = guild.me
        top = me.top_role.position if me else 0
        rows = [
            {
                "id": str(role.id),
                "name": role.name,
                "color": f"#{role.color.value:06x}" if role.color.value else None,
                "position": role.position,
                "managed": role.managed,
                "members": len(role.members),
                "assignable": (
                    not role.managed
                    and not role.is_default()
                    and role.position < top
                    and bool(me and me.guild_permissions.manage_roles)
                ),
            }
            for role in sorted(guild.roles, key=lambda r: -r.position)
            if not role.is_default()
        ]
        return H.ok({"roles": rows, "bot_top_role_position": top})

    @H.require_member
    async def members(request: web.Request) -> web.Response:
        """A searchable slice of the member list, for pickers.

        Capped and search-first rather than paginated: a picker exists to find
        one person, and shipping ten thousand members to a phone to let it
        filter them locally is the wrong shape of solution.
        """
        guild: discord.Guild = H.guild_of(request)
        query = (request.query.get("q") or "").strip().casefold()
        rows = []
        for member in guild.members:
            if member.bot:
                continue
            if (
                query
                and query not in member.display_name.casefold()
                and query not in member.name.casefold()
            ):
                continue
            rows.append(
                {
                    "id": str(member.id),
                    "name": member.display_name,
                    "handle": member.name,
                    "avatar": str(member.display_avatar.url),
                }
            )
            if len(rows) >= 50:
                break
        rows.sort(key=lambda r: r["name"].casefold())
        return H.ok({"members": rows, "total": guild.member_count})

    return [
        web.get("/api/guilds/{guild_id}/overview", overview),
        web.get("/api/guilds/{guild_id}/channels", channels),
        web.get("/api/guilds/{guild_id}/roles", roles),
        web.get("/api/guilds/{guild_id}/members", members),
    ]


def _guild_payload(guild: discord.Guild) -> dict:
    return {
        "id": str(guild.id),
        "name": guild.name,
        "icon": str(guild.icon.url) if guild.icon else None,
        "banner": str(guild.banner.url) if guild.banner else None,
        "members": guild.member_count,
        "humans": sum(1 for m in guild.members if not m.bot),
        "bots": sum(1 for m in guild.members if m.bot),
        "channels": len(guild.text_channels),
        "roles": len(guild.roles) - 1,
        "owner_id": str(guild.owner_id) if guild.owner_id else None,
        "created_at": guild.created_at.timestamp(),
        "boosts": guild.premium_subscription_count,
        "tier": guild.premium_tier,
    }


async def _activity_summary(guild: discord.Guild, member_ids: list[int]) -> dict:
    """What the server's members have been up to, from tables already indexed.

    Everything here is a leaderboard read filtered to this guild's members —
    which is exactly what a server-scoped board is throughout the economy. There
    is no per-guild counter behind any of it, and there must not be: coins,
    catches and digs belong to the account.
    """
    anglers = await db.get_fishing_leaderboard_for(member_ids)
    contrib = await db.get_contrib_leaderboard_for(member_ids)
    return {
        "anglers": len(anglers),
        "top_anglers": _named(guild, _top(anglers, "earned", 5)),
        "top_contributors": _named(guild, _top(contrib, "contribution", 5)),
        "generated_at": time.time(),
    }


def _top(rows: list[dict], key: str, limit: int) -> list[dict]:
    """Rank an unordered scoped board and take the head of it."""
    return sorted(rows, key=lambda row: (-row.get(key, 0), row["user_id"]))[:limit]


def _named(guild: discord.Guild, rows: list[dict]) -> list[dict]:
    """Attach display names to leaderboard rows.

    The economy tables are user-keyed and know nothing about servers, which is
    the point — a board scoped to a guild is that global table filtered to its
    members. Names are resolved here, at the edge, rather than stored anywhere.
    """
    out = []
    for row in rows:
        member = guild.get_member(int(row["user_id"]))
        out.append(
            {
                **row,
                "user_id": str(row["user_id"]),
                "name": member.display_name if member else f"User {row['user_id']}",
                "avatar": str(member.display_avatar.url) if member else None,
            }
        )
    return out


async def _can_manage(dash: "Dashboard", request: web.Request, guild) -> bool:
    try:
        await dash.assert_manager(request, guild)
        return True
    except H.ApiError:
        return False
