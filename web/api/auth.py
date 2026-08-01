"""
web/api/auth.py
Sign in with Discord, sign out, and "who am I".

The OAuth round trip is three endpoints and one rule: nothing that comes back
from Discord is trusted until the signed `state` proves the browser started this
login. `state` is signed rather than remembered, so an abandoned login leaks no
memory and a restart mid-login isn't a confusing failure.

`/api/auth/me` is the app's first call on every load. It answers even when
nobody is signed in — with `{"user": null}` plus enough setup information for
the login screen to say *why* it can't sign anyone in, which is the difference
between an operator debugging their config in five seconds and in an hour.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiohttp import web

from .. import http as H
from .. import oauth, permissions, security

if TYPE_CHECKING:
    from ..app import Dashboard

log = logging.getLogger("NanoBot.dashboard.auth")


def _home(dash: "Dashboard", path: str = "/") -> str:
    """Where to send the browser after a login attempt.

    Same-origin that is just the path. When the frontend is hosted elsewhere
    (`dashboard_frontend_url` — GitHub Pages, a CDN), the browser has to be sent
    *there*: it landed here because only this process can exchange the code, and
    this host has no page to show once that is done.
    """
    if dash.frontend_url:
        return f"{dash.frontend_url}{path}"
    return path


def routes(dash: "Dashboard") -> list:
    async def login(request: web.Request) -> web.StreamResponse:
        """Bounce the browser to Discord."""
        if not dash.configured:
            raise H.ApiError(
                503,
                "not_configured",
                "This dashboard isn't finished being set up yet.",
                hint=", ".join(dash.missing_setup()),
            )
        url = oauth.authorize_url(
            dash.oauth_client_id, dash.redirect_uri, dash.new_state()
        )
        raise web.HTTPFound(url)

    async def callback(request: web.Request) -> web.StreamResponse:
        """Where Discord sends the browser back.

        Redirects rather than returning JSON — the browser lands here directly,
        so an error has to be something a *page* can render, which is why
        failures go back to `/login` with a reason in the query string.
        """
        if request.query.get("error"):
            raise web.HTTPFound(_home(dash, "/login?error=denied"))

        state = request.query.get("state") or ""
        if not security.verify_state(state, dash.secret):
            # Also what an expired login looks like: someone left the Discord
            # consent screen open for a while, which is not an attack.
            raise web.HTTPFound(_home(dash, "/login?error=state"))

        code = request.query.get("code") or ""
        if not code:
            raise web.HTTPFound(_home(dash, "/login?error=nocode"))

        session = await dash.session()
        try:
            token = await oauth.exchange_code(
                session,
                client_id=dash.oauth_client_id,
                client_secret=dash.client_secret,
                code=code,
                redirect_uri=dash.redirect_uri,
            )
        except oauth.OAuthError:
            raise web.HTTPFound(_home(dash, "/login?error=exchange")) from None

        access_token = token.get("access_token")
        user = await oauth.fetch_user(session, access_token) if access_token else None
        if not user:
            raise web.HTTPFound(_home(dash, "/login?error=identity"))

        # Raised rather than returned: aiohttp deprecated returning an
        # HTTPException, and the redirect still carries the Set-Cookie header
        # because the exception *is* the response object.
        response = web.HTTPFound(_home(dash))
        dash.issue_session(
            response,
            {
                "uid": user["id"],
                "name": user["global_name"],
                "handle": user["username"],
                "avatar": user["avatar"],
                "at": access_token,
            },
        )
        raise response

    async def logout(request: web.Request) -> web.Response:
        session = H.session_of(request)
        if session:
            oauth.forget_guilds(str(session.get("uid")))
        return dash.clear_session(H.ok({"user": None}))

    async def me(request: web.Request) -> web.Response:
        """Identity, CSRF token, and what this instance can do.

        Deliberately one call: the app needs all three before it can paint
        anything, and three requests on a cold phone connection is three
        chances to look broken.
        """
        session = H.session_of(request)
        payload = {
            "configured": dash.configured,
            "missing": dash.missing_setup() if not dash.configured else [],
            "play_enabled": dash.play_enabled,
            "bot": {
                "name": getattr(dash.bot.user, "name", "NanoBot"),
                "avatar": (
                    str(dash.bot.user.display_avatar.url) if dash.bot.user else None
                ),
                "guilds": len(dash.bot.guilds),
                "ready": dash.bot.is_ready(),
            },
        }
        if not session:
            return H.ok({**payload, "user": None})
        return H.ok(
            {
                **payload,
                "user": {
                    "id": str(session["uid"]),
                    "name": session.get("name"),
                    "handle": session.get("handle"),
                    "avatar": oauth.avatar_url(
                        str(session["uid"]), session.get("avatar")
                    ),
                    "bot_owner": await dash.is_bot_owner(request),
                },
                "csrf": security.csrf_for(session, dash.secret),
            }
        )

    @H.require_session
    async def my_guilds(request: web.Request) -> web.Response:
        """The server picker.

        A user who has signed out of Discord elsewhere still holds a valid
        session cookie here, so a 401 from Discord is reported as "sign in
        again" rather than "you have no servers".
        """
        session = H.session_of(request)
        uid = str(session["uid"])
        force = request.query.get("refresh") == "1"
        if force:
            oauth.forget_guilds(uid)
        raw = await oauth.fetch_guilds(
            await dash.session(), session.get("at"), uid, force=force
        )
        if raw is None:
            raise H.ApiError(
                503,
                "discord_unavailable",
                "Discord didn't answer when we asked for your servers. "
                "Give it a moment and try again.",
            )
        bot_ids = [g.id for g in dash.bot.guilds]
        rows = permissions.manageable_guilds(
            raw, bot_ids, bot_owner=await dash.is_bot_owner(request)
        )
        for row in rows:
            row["icon_url"] = oauth.guild_icon_url(row["id"], row.get("icon"))
            guild = dash.bot.get_guild(int(row["id"]))
            row["members"] = guild.member_count if guild else None
        return H.ok(
            {
                "guilds": rows,
                "invite": _invite_url(dash),
            }
        )

    return [
        web.get("/api/auth/login", login),
        web.get("/api/auth/callback", callback),
        web.post("/api/auth/logout", logout),
        web.get("/api/auth/me", me),
        web.get("/api/auth/guilds", my_guilds),
    ]


def _invite_url(dash: "Dashboard") -> str | None:
    """An invite link with exactly the permissions the features here need.

    Built from the same `FEATURE_PERMISSIONS` map the permission checklist is
    built from, so "add to server" and "you're missing X" can never disagree
    about what the bot needs.
    """
    client_id = dash.oauth_client_id
    if not client_id:
        return None
    bits = 0
    for spec in permissions.FEATURE_PERMISSIONS.values():
        for bit in spec["required"] + spec["optional"]:
            bits |= bit
    return (
        f"https://discord.com/oauth2/authorize?client_id={client_id}"
        f"&permissions={bits}&scope=bot%20applications.commands"
    )
