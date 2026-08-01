"""
web/api/roles.py
Self-role panels: create, edit, order, publish.

The reason this is a dashboard feature rather than another command is ordering.
A panel's buttons are laid out in one dimension and the only way to move one in
Discord was to remove it and add it again — which loses its label, emoji and
style. Dragging is the obvious interface for "these, in this order", and it is
the one thing a chat client genuinely can't offer.

Everything here writes through the same accessors `/roles panel …` writes
through, and any change to a panel that has already been posted **re-renders the
live message** via the Roles cog's own `_refresh_panel_message`. A dashboard that
edited the database and left a stale message in the channel would be worse than
no dashboard: the panel people can actually see would be the wrong one.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from aiohttp import web

from utils import db

from .. import http as H

if TYPE_CHECKING:
    from ..app import Dashboard

log = logging.getLogger("NanoBot.dashboard.roles")

MODES = ("toggle", "single")
STYLES = ("primary", "secondary", "success", "danger")
MAX_ENTRIES = 25  # Discord's limit: five rows of five buttons.


def routes(dash: "Dashboard") -> list:
    @H.require_manager
    async def list_panels(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        panels = await db.get_role_panels_for_guild(guild.id)
        return H.ok(
            {
                "panels": [_panel_payload(guild, panel) for panel in panels],
                "meta": {
                    "modes": [
                        {
                            "key": "toggle",
                            "label": "Pick any",
                            "blurb": "Members can hold as many of these as they like.",
                        },
                        {
                            "key": "single",
                            "label": "Pick one",
                            "blurb": "Choosing one swaps out whichever they had — "
                            "for colours, pronouns, regions.",
                        },
                    ],
                    "styles": STYLES,
                    "max_entries": MAX_ENTRIES,
                },
            }
        )

    @H.require_manager
    async def create_panel(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        body = await H.body_json(request)
        title = _text(body.get("title"), "title", 100)
        mode = body.get("mode", "toggle")
        if mode not in MODES:
            raise H.bad_request("A panel either lets members pick any or pick one.")
        panel_id = _new_id()
        await db.create_role_panel(
            panel_id,
            guild.id,
            title,
            _text(body.get("description"), "description", 500) or None,
            mode,
        )
        return H.ok({"panels": await _all(guild)})

    @H.require_manager
    async def edit_panel(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        panel = await _panel(request, guild)
        body = await H.body_json(request)
        mode = body.get("mode", panel["mode"])
        if mode not in MODES:
            raise H.bad_request("A panel either lets members pick any or pick one.")
        await db.edit_role_panel(
            panel["id"],
            _text(body.get("title", panel["title"]), "title", 100),
            _text(body.get("description", panel["description"]), "description", 500)
            or None,
            mode,
        )
        await _republish(dash, guild, panel["id"])
        return H.ok({"panels": await _all(guild)})

    @H.require_manager
    async def delete_panel(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        panel = await _panel(request, guild)
        await db.delete_role_panel(panel["id"])
        return H.ok({"panels": await _all(guild)})

    @H.require_manager
    async def add_entry(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        panel = await _panel(request, guild)
        if len(panel["entries"]) >= MAX_ENTRIES:
            raise H.conflict(
                f"A panel holds {MAX_ENTRIES} roles — Discord's limit, not ours. "
                "Make a second panel."
            )
        body = await H.body_json(request)
        role = _assignable_role(body.get("role_id"), guild)
        style = body.get("style", "secondary")
        if style not in STYLES:
            raise H.bad_request("That isn't one of the button styles.")
        await db.add_role_to_panel(
            panel["id"],
            {
                "role_id": role.id,
                "label": _text(body.get("label") or role.name, "label", 80),
                "emoji": _text(body.get("emoji"), "emoji", 32) or None,
                "style": style,
            },
        )
        await _republish(dash, guild, panel["id"])
        return H.ok({"panels": await _all(guild)})

    @H.require_manager
    async def remove_entry(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        panel = await _panel(request, guild)
        try:
            role_id = int(request.match_info["role_id"])
        except (TypeError, ValueError):
            raise H.bad_request("That isn't a role.") from None
        await db.remove_role_from_panel(panel["id"], role_id)
        await _republish(dash, guild, panel["id"])
        return H.ok({"panels": await _all(guild)})

    @H.require_manager
    async def reorder(request: web.Request) -> web.Response:
        """Rewrite the button order from a full list — the drag-and-drop save."""
        guild = H.guild_of(request)
        panel = await _panel(request, guild)
        body = await H.body_json(request)
        order = body.get("order")
        if not isinstance(order, list):
            raise H.bad_request("Send the new order as a list of role ids.")
        try:
            ids = [int(value) for value in order]
        except (TypeError, ValueError):
            raise H.bad_request(
                "Every entry in the order has to be a role id."
            ) from None
        await db.reorder_role_panel(panel["id"], ids)
        await _republish(dash, guild, panel["id"])
        return H.ok({"panels": await _all(guild)})

    @H.require_manager
    async def publish(request: web.Request) -> web.Response:
        """Post the panel to a channel (or move it to a different one).

        Goes through the Roles cog so the posted message carries the *same*
        persistent view the cog registers — a message posted from here with a
        hand-rolled view would have buttons that stop working on the next
        restart.
        """
        guild = H.guild_of(request)
        panel = await _panel(request, guild)
        if not panel["entries"]:
            raise H.conflict("Add at least one role before posting the panel.")
        body = await H.body_json(request)
        channel = _postable_channel(body.get("channel_id"), guild)

        cog = dash.bot.get_cog("Roles")
        if cog is None:
            raise H.ApiError(503, "cog_unavailable", "The roles feature isn't loaded.")
        from cogs.roles.views import _build_embed, _build_view

        try:
            view = _build_view(panel)
            message = await channel.send(embed=_build_embed(panel), view=view)
            dash.bot.add_view(view)
        except discord.Forbidden:
            raise H.bad_request(f"NanoBot can't post in #{channel.name}.") from None
        except discord.HTTPException as exc:
            log.warning("Publishing panel %s failed: %s", panel["id"], exc)
            raise H.ApiError(
                502, "discord_error", "Discord refused to post that panel."
            ) from None

        await db.update_role_panel_message(panel["id"], channel.id, message.id)
        return H.ok({"panels": await _all(guild), "jump_url": message.jump_url})

    return [
        web.get("/api/guilds/{guild_id}/roles/panels", list_panels),
        web.post("/api/guilds/{guild_id}/roles/panels", create_panel),
        web.patch("/api/guilds/{guild_id}/roles/panels/{panel_id}", edit_panel),
        web.delete("/api/guilds/{guild_id}/roles/panels/{panel_id}", delete_panel),
        web.post("/api/guilds/{guild_id}/roles/panels/{panel_id}/entries", add_entry),
        web.delete(
            "/api/guilds/{guild_id}/roles/panels/{panel_id}/entries/{role_id}",
            remove_entry,
        ),
        web.patch("/api/guilds/{guild_id}/roles/panels/{panel_id}/order", reorder),
        web.post("/api/guilds/{guild_id}/roles/panels/{panel_id}/publish", publish),
    ]


# ── helpers ───────────────────────────────────────────────────────────────────
async def _all(guild) -> list[dict]:
    return [
        _panel_payload(guild, p) for p in await db.get_role_panels_for_guild(guild.id)
    ]


def _panel_payload(guild, panel: dict) -> dict:
    """One panel, with each entry told whether the bot could actually grant it.

    `assignable` is the whole point of resolving roles here: a panel offering a
    role above NanoBot's own is a button that errors for every member who
    presses it, and the only moment anyone will look at that fact is now.
    """
    me = guild.me
    top = me.top_role.position if me else 0
    can_manage_roles = bool(me and me.guild_permissions.manage_roles)
    entries = []
    for entry in panel["entries"]:
        role = guild.get_role(int(entry["role_id"]))
        entries.append(
            {
                **entry,
                "role_id": str(entry["role_id"]),
                "name": role.name if role else None,
                "deleted": role is None,
                "color": (
                    f"#{role.color.value:06x}" if role and role.color.value else None
                ),
                "assignable": bool(
                    role
                    and can_manage_roles
                    and not role.managed
                    and role.position < top
                ),
            }
        )
    channel = (
        guild.get_channel(int(panel["channel_id"])) if panel["channel_id"] else None
    )
    return {
        "id": panel["id"],
        "title": panel["title"],
        "description": panel["description"],
        "mode": panel["mode"],
        "entries": entries,
        "posted": bool(panel["message_id"]),
        "channel": {"id": str(channel.id), "name": channel.name} if channel else None,
        "problems": [
            f"@{e['name'] or e['role_id']} can't be assigned by NanoBot"
            for e in entries
            if not e["assignable"] and not e["deleted"]
        ]
        + ["A role on this panel was deleted" for e in entries if e["deleted"]][:1],
    }


async def _panel(request: web.Request, guild) -> dict:
    """The panel named in the path, checked to belong to *this* guild.

    Panel ids are short and global, so without this check a manager of one
    server could edit another server's panel by guessing an id.
    """
    panel = await db.get_role_panel(request.match_info["panel_id"])
    if panel is None or str(panel["guild_id"]) != str(guild.id):
        raise H.not_found("That panel doesn't exist here.")
    return panel


async def _republish(dash: "Dashboard", guild, panel_id: str) -> None:
    """Re-render the posted message, if there is one. Never fatal."""
    cog = dash.bot.get_cog("Roles")
    if cog is None:
        return
    panel = await db.get_role_panel(panel_id)
    if not panel:
        return
    try:
        await cog._refresh_panel_message(guild, panel)
    except Exception:
        # The database change already landed; a message that couldn't be edited
        # is worth a log line, not a failed request the user has to retry.
        log.warning("Couldn't refresh panel %s after an edit", panel_id, exc_info=True)


def _new_id() -> str:
    import secrets

    return secrets.token_urlsafe(6)


def _text(value, field: str, limit: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise H.bad_request(f"“{field}” has to be text.")
    text = value.strip()
    if len(text) > limit:
        raise H.bad_request(f"“{field}” is too long — {limit} characters at most.")
    return text


def _assignable_role(value, guild):
    try:
        rid = int(value)
    except (TypeError, ValueError):
        raise H.bad_request("Pick a role.") from None
    role = guild.get_role(rid)
    if role is None:
        raise H.bad_request("That role isn't in this server any more.")
    me = guild.me
    if not me or not me.guild_permissions.manage_roles:
        raise H.bad_request("NanoBot needs the Manage Roles permission for this.")
    if role.managed or role.position >= me.top_role.position:
        raise H.bad_request(
            f"NanoBot can't assign @{role.name} — it sits at or above NanoBot's "
            "own highest role.",
            hint="Drag NanoBot's role above it in Server Settings → Roles.",
        )
    return role


def _postable_channel(value, guild):
    try:
        cid = int(value)
    except (TypeError, ValueError):
        raise H.bad_request("Pick a channel to post the panel in.") from None
    channel = next((c for c in guild.text_channels if c.id == cid), None)
    if channel is None:
        raise H.bad_request("That channel isn't in this server any more.")
    me = guild.me
    perms = channel.permissions_for(me) if me else None
    if not perms or not (
        perms.view_channel and perms.send_messages and perms.embed_links
    ):
        raise H.bad_request(
            f"NanoBot needs View Channel, Send Messages and Embed Links in "
            f"#{channel.name}."
        )
    return channel
