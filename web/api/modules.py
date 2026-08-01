"""
web/api/modules.py
Tickets, birthdays, gatekeeper and music — the four remaining config surfaces.

Same two verbs and the same contract as `settings.py`: `GET` returns the current
config *and* the metadata to render it, `PATCH` merges a partial update through
coercers that narrow every value before it reaches SQL. New pages, established
pattern — the frontend's settings component didn't need changing to hold them.

Each one carries the explanation the Discord command can't fit. Gatekeeper's
`match_mode` is the clearest case: "or" versus "and" is two words in a config
row and a completely different moderation policy, so the API sends what each one
actually *does* rather than the string it stores.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import discord
from aiohttp import web

from utils import db

from .. import http as H
from .settings import _apply, _bool, _channel, _int, _text

if TYPE_CHECKING:
    from ..app import Dashboard


def routes(dash: "Dashboard") -> list:
    # ── Tickets ──────────────────────────────────────────────────────────────
    @H.require_manager
    async def get_tickets(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        cfg = await db.get_ticket_config(guild.id)
        me = guild.me
        return H.ok(
            {
                "config": cfg,
                "meta": {
                    "why": "A ticket is a private thread under the panel channel "
                    "— no per-ticket channels, no permission overwrites piling "
                    "up. Staff are pulled in by the role mention.",
                    "needs_threads": bool(
                        me and me.guild_permissions.create_private_threads
                    ),
                    "can_lock": bool(me and me.guild_permissions.manage_threads),
                    "max_open_range": [1, 25],
                },
            }
        )

    @H.require_manager
    async def patch_tickets(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        body = await H.body_json(request)
        patch = _apply(
            body,
            {
                "enabled": lambda v: _bool(v, "enabled"),
                "log_channel_id": lambda v: _channel(v, guild, "transcript channel"),
                "panel_channel_id": lambda v: _channel(v, guild, "panel channel"),
                "staff_role_id": lambda v: _role_id(v, guild),
                "panel_title": lambda v: _text(v, "panel title", limit=100) or None,
                "panel_message": lambda v: _text(v, "panel message", limit=1000)
                or None,
                "max_open": lambda v: _int(v, "open tickets per member", lo=1, hi=25),
            },
        )
        merged = {**await db.get_ticket_config(guild.id), **patch}
        if merged.get("enabled") and not merged.get("staff_role_id"):
            raise H.bad_request(
                "Pick a staff role first — otherwise a ticket opens with nobody "
                "in it."
            )
        if patch:
            await db.set_ticket_config(guild.id, **patch)
        return H.ok({"config": await db.get_ticket_config(guild.id)})

    # ── Birthdays ────────────────────────────────────────────────────────────
    @H.require_manager
    async def get_birthdays(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        cfg = await db.get_birthday_config(guild.id)
        rows = await db.get_guild_birthdays(guild.id)
        from cogs.birthday.constants import _TZ_CHOICES
        from cogs.birthday.helpers import fmt_birthday, guess_timezone_from_regions

        registered = []
        for row in rows:
            member = guild.get_member(int(row["user_id"]))
            registered.append(
                {
                    "id": str(row["user_id"]),
                    "name": member.display_name if member else f"User {row['user_id']}",
                    "avatar": str(member.display_avatar.url) if member else None,
                    "date": fmt_birthday(row["month"], row["day"], row.get("year")),
                    "month": row["month"],
                    "day": row["day"],
                }
            )
        registered.sort(key=lambda r: (r["month"], r["day"]))

        return H.ok(
            {
                "config": cfg,
                "birthdays": registered,
                "meta": {
                    # Curated zones first, then the full IANA list — the same
                    # ordering the /birthday timezone picker uses, so the two
                    # offer the same thing in the same order.
                    "timezones": [
                        {"value": value, "label": label, "emoji": emoji}
                        for label, value, emoji in _TZ_CHOICES
                    ],
                    "suggested_timezone": guess_timezone_from_regions(
                        [c.rtc_region for c in guild.voice_channels if c.rtc_region]
                    ),
                    "variables": [
                        {"token": "{user}", "means": "Mentions them"},
                        {"token": "{username}", "means": "Their name"},
                        {
                            "token": "{age}",
                            "means": "How old they're turning (needs a birth year)",
                        },
                    ],
                    "why": "Checked every 15 minutes against each server's own "
                    "timezone, and announced once per birthday per year. "
                    "Members sharing a day get one combined post.",
                },
            }
        )

    @H.require_manager
    async def patch_birthdays(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        body = await H.body_json(request)
        patch = _apply(
            body,
            {
                "enabled": lambda v: _bool(v, "enabled"),
                "channel_id": lambda v: _channel(v, guild, "announcement channel"),
                "message": lambda v: _text(v, "message", limit=1000) or None,
                "timezone": _timezone,
                "hour": lambda v: _int(v, "hour", lo=0, hi=23),
                "gif_enabled": lambda v: _bool(v, "GIFs"),
                "vc_enabled": lambda v: _bool(v, "voice song"),
                "ping_enabled": lambda v: _bool(v, "ping"),
            },
        )
        merged = {**await db.get_birthday_config(guild.id), **patch}
        if merged.get("enabled") and not merged.get("channel_id"):
            raise H.bad_request("Pick a channel for the announcement first.")
        if patch:
            await db.set_birthday_config(guild.id, **patch)
        return H.ok({"config": await db.get_birthday_config(guild.id)})

    # ── Gatekeeper ───────────────────────────────────────────────────────────
    @H.require_manager
    async def get_gatekeeper(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        cfg = await db.get_gatekeeper_config(guild.id)
        me = guild.me
        role = guild.get_role(int(cfg["mute_role_id"])) if cfg["mute_role_id"] else None
        return H.ok(
            {
                "config": cfg,
                "meta": {
                    "match_modes": [
                        {
                            "key": "or",
                            "label": "Either signal",
                            "blurb": "Mute an account that is too new OR has a "
                            "suspicious avatar. Catches more, including some "
                            "genuine newcomers.",
                        },
                        {
                            "key": "and",
                            "label": "Both signals",
                            "blurb": "Mute only an account that is too new AND "
                            "has a suspicious avatar. Far fewer false alarms, "
                            "and it lets some through.",
                        },
                    ],
                    "role_ok": bool(
                        role is None
                        or (
                            me
                            and me.guild_permissions.manage_roles
                            and role < me.top_role
                        )
                    ),
                    "role_name": role.name if role else None,
                    "sensitivity": {
                        "min": 0,
                        "max": 20,
                        "blurb": "How close a new member's avatar has to be to a "
                        "known stock one. Lower is stricter; 8 is the default and "
                        "a sensible place to stay.",
                    },
                    "why": "Runs when somebody joins. A muted member gets a "
                    "verification prompt (DM first, then the quarantine channel) "
                    "and is kicked if they never complete it.",
                },
            }
        )

    @H.require_manager
    async def patch_gatekeeper(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        body = await H.body_json(request)
        patch = _apply(
            body,
            {
                "enabled": lambda v: _bool(v, "enabled"),
                "mute_role_id": lambda v: _role_id(v, guild, assignable=True),
                "quarantine_channel_id": lambda v: _channel(
                    v, guild, "quarantine channel"
                ),
                "log_channel_id": lambda v: _channel(v, guild, "log channel"),
                "min_account_age": lambda v: _int(
                    v, "minimum account age", lo=0, hi=31_536_000
                ),
                "unmute_age": lambda v: _int(v, "auto-unmute age", lo=0, hi=31_536_000),
                "kick_timeout": lambda v: _int(
                    v, "kick timeout", lo=3600, hi=2_592_000
                ),
                "stock_threshold": lambda v: _int(v, "sensitivity", lo=0, hi=20),
                "mute_new_accounts": lambda v: _bool(v, "mute new accounts"),
                "mute_default_avatar": lambda v: _bool(v, "mute default avatars"),
                "mute_stock_avatar": lambda v: _bool(v, "mute stock avatars"),
                "verify_enabled": lambda v: _bool(v, "verification"),
                "age_unmute_enabled": lambda v: _bool(v, "age auto-unmute"),
                "verify_message": lambda v: _text(v, "verify message", limit=1000)
                or None,
                "match_mode": _match_mode,
            },
        )
        merged = {**await db.get_gatekeeper_config(guild.id), **patch}
        if merged.get("enabled") and not merged.get("mute_role_id"):
            raise H.bad_request(
                "Pick the mute role first — the gate has nothing to apply otherwise."
            )
        if merged["unmute_age"] and merged["unmute_age"] < merged["min_account_age"]:
            raise H.bad_request(
                "Auto-unmute has to be later than the minimum age, or a muted "
                "account would be released immediately."
            )
        if patch:
            await db.set_gatekeeper_config(guild.id, **patch)
        return H.ok({"config": await db.get_gatekeeper_config(guild.id)})

    # ── Music ────────────────────────────────────────────────────────────────
    @H.require_manager
    async def get_music(request: web.Request) -> web.Response:
        """This server's music settings — and only this server's.

        The playback knobs (`music_*` in config.ini) are the *operator's*, not a
        server's: they set cookies, proxies and cache limits for the whole bot.
        They are reported read-only with that reason, the same way the economy
        page reports the bot-wide faucets.
        """
        guild = H.guild_of(request)
        playlist = await db.get_autoplaylist(guild.id)
        blocked_songs = await db.get_music_song_blocks(guild.id)
        blocked_users = await db.get_music_user_blocks(guild.id)
        history = await db.get_music_history(guild.id, limit=20)
        cfg = dash.bot.config or {}
        return H.ok(
            {
                "config": {"stay_connected": await db.get_music_stay(guild.id)},
                "playlist": playlist,
                "blocked_songs": blocked_songs,
                "blocked_users": [
                    {
                        "id": str(uid),
                        "name": (
                            guild.get_member(int(uid)).display_name
                            if guild.get_member(int(uid))
                            else f"User {uid}"
                        ),
                    }
                    for uid in blocked_users
                ],
                "history": history,
                "operator": {
                    "editable": False,
                    "why": "These are the bot operator's settings, not a "
                    "server's — they cover every server at once. Change them in "
                    "config.ini.",
                    "values": {
                        key: cfg.get(key)
                        for key in (
                            "music_max_queue",
                            "music_default_volume",
                            "music_idle_timeout",
                            "music_skip_ratio",
                            "music_search_service",
                            "music_persist_queue",
                            "music_sponsorblock",
                        )
                    },
                },
            }
        )

    @H.require_manager
    async def patch_music(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        body = await H.body_json(request)
        if "stay_connected" in body:
            await db.set_music_stay(
                guild.id, _bool(body["stay_connected"], "stay connected")
            )
        return H.ok({"config": {"stay_connected": await db.get_music_stay(guild.id)}})

    @H.require_manager
    async def patch_music_list(request: web.Request) -> web.Response:
        """Add to or remove from the guild playlist / the two block lists."""
        guild = H.guild_of(request)
        which = request.match_info["list"]
        body = await H.body_json(request)
        action = body.get("action", "add")
        if action not in ("add", "remove"):
            raise H.bad_request("Action has to be add or remove.")

        if which == "playlist":
            value = _text(body.get("value"), "URL", limit=500, allow_empty=False)
            if action == "add":
                await db.add_autoplaylist_entry(guild.id, value)
            else:
                await db.remove_autoplaylist_entry(guild.id, value)
            return H.ok({"playlist": await db.get_autoplaylist(guild.id)})

        if which == "songs":
            value = _text(body.get("value"), "song", limit=300, allow_empty=False)
            if action == "add":
                await db.add_music_song_block(guild.id, value)
            else:
                await db.remove_music_song_block(guild.id, value)
            return H.ok({"blocked_songs": await db.get_music_song_blocks(guild.id)})

        if which == "users":
            try:
                uid = int(body.get("value"))
            except (TypeError, ValueError):
                raise H.bad_request("Pick a member.") from None
            if action == "add":
                await db.add_music_user_block(guild.id, uid)
            else:
                await db.remove_music_user_block(guild.id, uid)
            blocked = await db.get_music_user_blocks(guild.id)
            return H.ok(
                {
                    "blocked_users": [
                        {
                            "id": str(u),
                            "name": (
                                guild.get_member(int(u)).display_name
                                if guild.get_member(int(u))
                                else f"User {u}"
                            ),
                        }
                        for u in blocked
                    ]
                }
            )
        raise H.not_found("No such list.")

    return [
        web.get("/api/guilds/{guild_id}/settings/tickets", get_tickets),
        web.patch("/api/guilds/{guild_id}/settings/tickets", patch_tickets),
        web.get("/api/guilds/{guild_id}/settings/birthdays", get_birthdays),
        web.patch("/api/guilds/{guild_id}/settings/birthdays", patch_birthdays),
        web.get("/api/guilds/{guild_id}/settings/gatekeeper", get_gatekeeper),
        web.patch("/api/guilds/{guild_id}/settings/gatekeeper", patch_gatekeeper),
        web.get("/api/guilds/{guild_id}/settings/music", get_music),
        web.patch("/api/guilds/{guild_id}/settings/music", patch_music),
        web.patch(
            "/api/guilds/{guild_id}/settings/music/lists/{list}", patch_music_list
        ),
    ]


def _role_id(value: Any, guild: discord.Guild, *, assignable: bool = False):
    """Resolve a role id, optionally checking the bot could actually apply it."""
    if value in (None, "", 0, "0"):
        return None
    try:
        rid = int(value)
    except (TypeError, ValueError):
        raise H.bad_request("Pick a role.") from None
    role = guild.get_role(rid)
    if role is None:
        raise H.bad_request("That role isn't in this server any more.")
    if assignable:
        me = guild.me
        if not me or not me.guild_permissions.manage_roles:
            raise H.bad_request("NanoBot needs the Manage Roles permission for this.")
        if role.managed or role >= me.top_role:
            raise H.bad_request(
                f"NanoBot can't apply @{role.name} — it sits at or above "
                "NanoBot's own highest role.",
                hint="Drag NanoBot's role above it in Server Settings → Roles.",
            )
    return str(rid)


def _timezone(value: Any) -> str:
    """An IANA zone name, checked against the system database.

    Names rather than fixed offsets, so daylight saving is handled for free —
    and a typo'd zone would mean the announcement fires at the wrong hour every
    day without ever erroring.
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    name = str(value or "UTC").strip()
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise H.bad_request(
            f"“{name}” isn't a timezone. Pick one from the list — they look "
            "like Europe/London or America/New_York."
        ) from None
    return name


def _match_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode not in ("and", "or"):
        raise H.bad_request("The gate matches on either signal, or on both.")
    return mode
