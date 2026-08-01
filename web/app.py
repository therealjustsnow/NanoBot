"""
web/app.py
The dashboard itself: configuration, guild resolution, and route assembly.

Mounted on the shared `HttpServer` the same way `/health` and the vote webhook
are, so a host that grants one inbound port can still run all three. It is off
until `dashboard_port` is set — an unconfigured bot exposes nothing.

**Permissions come from the gateway, not from OAuth.** Discord's OAuth guild
list carries a `permissions` field, but it is a snapshot from whenever the
user's token was minted and it does not know about role changes since. The bot
is *in* the server with a live member cache, so `assert_manager` asks the same
object `/level set` asks: `member.guild_permissions.manage_guild`. The OAuth
list is used only to decide which servers to *offer*, which is a menu, not a
gate.

The SPA is served from `web/static/` with a catch-all that hands `index.html` to
any non-API path, so deep links like `/g/123/fishing` survive a refresh.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Optional

import aiohttp
from aiohttp import web

from . import http as H
from . import permissions, security
from .api import routes as api_routes

if TYPE_CHECKING:
    from main import NanoBot

log = logging.getLogger("NanoBot.dashboard")

DASHBOARD_OWNER = "dashboard"
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


class Dashboard:
    """One dashboard, bound to one running bot."""

    def __init__(self, bot: "NanoBot") -> None:
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None
        cfg = bot.config or {}

        self.port = _int(cfg.get("dashboard_port"))
        self.host = str(cfg.get("dashboard_host") or "0.0.0.0")
        self.base_url = str(cfg.get("dashboard_base_url") or "").rstrip("/")
        self.client_id = str(cfg.get("dashboard_client_id") or "") or None
        self.client_secret = str(cfg.get("dashboard_client_secret") or "")
        self.session_days = max(1, _int(cfg.get("dashboard_session_days")) or 7)
        self.play_enabled = cfg.get("dashboard_play_enabled", True) is not False
        # Origins allowed to call the API from another host — the separately
        # hosted frontend (GitHub Pages, say). Empty is the normal case and
        # switches CORS off entirely.
        self.allowed_origins = _origins(cfg.get("dashboard_allowed_origins"))
        # Where the *browser app* lives, when it isn't this process serving it.
        # Blank means it is, which is the normal case and why this is separate
        # from `base_url`: the OAuth callback always lands on the API (only the
        # API can exchange a code — it holds the client secret), and the browser
        # is sent on to the app afterwards. Same-origin those are one URL, and
        # conflating them is what breaks a split deployment.
        self.frontend_url = str(cfg.get("dashboard_frontend_url") or "").rstrip("/")

        secret = str(cfg.get("dashboard_session_secret") or "")
        if not secret:
            # An ephemeral secret is better than refusing to start, but it signs
            # everyone out on every restart — and this bot restarts on
            # `!upgrade`, so say so rather than letting it be mysterious.
            secret = security.new_secret()
            if self.port:
                log.warning(
                    "dashboard_session_secret is unset — using a random one. "
                    "Everyone will be signed out on every restart. Generate a "
                    'stable one with: python -c "import secrets; '
                    'print(secrets.token_urlsafe(48))"'
                )
        self.secret = secret

    # ── lifecycle ────────────────────────────────────────────────────────────
    @property
    def enabled(self) -> bool:
        return self.port > 0

    @property
    def configured(self) -> bool:
        """Whether OAuth can actually work, as opposed to the port being open."""
        return bool(self.enabled and self.client_secret and self.base_url)

    @property
    def redirect_uri(self) -> str:
        """Where Discord sends the browser back — always this API.

        Never the frontend, even when the frontend is somewhere else: the
        callback exchanges the code for a token, which takes the client secret,
        which only this process has.
        """
        return f"{self.base_url}/api/auth/callback"

    @property
    def app_url(self) -> str:
        """The browser app's own base — the frontend if it is hosted apart."""
        return self.frontend_url or self.base_url

    @property
    def oauth_client_id(self) -> str:
        """The configured client id, else the bot's own application id."""
        return self.client_id or str(getattr(self.bot, "application_id", "") or "")

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    def missing_setup(self) -> list[str]:
        """Which settings still stand between here and a working login."""
        missing = []
        if not self.base_url:
            missing.append("dashboard_base_url")
        if not self.oauth_client_id:
            missing.append("dashboard_client_id")
        if not self.client_secret:
            missing.append("dashboard_client_secret")
        return missing

    # ── guild + member resolution ────────────────────────────────────────────
    async def resolve_guild(self, request: web.Request):
        """The guild named in the path, or a 404 the user can act on."""
        raw = request.match_info.get("guild_id", "")
        try:
            gid = int(raw)
        except (TypeError, ValueError):
            raise H.bad_request("That isn't a server id.") from None
        guild = self.bot.get_guild(gid)
        if guild is None:
            raise H.not_found(
                "NanoBot isn't in that server — or hasn't finished connecting to "
                "it yet."
            )
        return guild

    async def member_of(self, request: web.Request, guild):
        """The requesting user as a member of `guild`, or None.

        Falls back to an HTTP fetch on a cache miss, the way
        `utils/converters.SafeTextChannel` does: a member who joined while the
        gateway was down is a real member, and refusing them because of a cache
        gap is the bug that converter exists to avoid.
        """
        uid = H.user_id_of(request)
        member = guild.get_member(uid)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(uid)
        except Exception:
            return None

    async def is_bot_owner(self, request: web.Request) -> bool:
        uid = H.user_id_of(request)
        try:
            owner_ids = self.bot.owner_ids or set()
            if owner_ids:
                return uid in owner_ids
            owner_id = getattr(self.bot, "owner_id", None)
            return bool(owner_id and uid == owner_id)
        except Exception:
            return False

    async def assert_member(self, request: web.Request, guild) -> None:
        if await self.is_bot_owner(request):
            return
        if await self.member_of(request, guild) is None:
            raise H.forbidden("You're not in that server.")

    async def assert_manager(self, request: web.Request, guild) -> None:
        """The dashboard's one gate: Manage Server, read live off the gateway."""
        if await self.is_bot_owner(request):
            return
        member = await self.member_of(request, guild)
        if member is None:
            raise H.forbidden("You're not in that server.")
        if not permissions.can_manage(
            member.guild_permissions.value, is_owner=guild.owner_id == member.id
        ):
            raise H.forbidden(
                "You need Manage Server in this server to change its settings.",
                hint="Ask an admin to give you the Manage Server permission, or "
                "to make the change for you.",
            )

    def assert_play_enabled(self) -> None:
        if not self.play_enabled:
            raise H.forbidden(
                "Playing from the web is switched off on this instance. "
                "Everything still works in Discord.",
                hint="dashboard_play_enabled",
            )

    # ── sessions ─────────────────────────────────────────────────────────────
    def issue_session(self, response: web.Response, payload: dict) -> web.Response:
        token = security.sign_session(payload, self.secret, self.session_days * 86400)
        response.set_cookie(
            security.SESSION_COOKIE,
            token,
            max_age=self.session_days * 86400,
            httponly=True,
            # `None` is required for a cookie to survive a cross-origin fetch,
            # and browsers only accept `SameSite=None` alongside `Secure` — so
            # the cross-origin deployment is HTTPS-only by construction. The
            # same-origin case stays on `Lax`, which is strictly safer.
            samesite="None" if self.allowed_origins else "Lax",
            # Only mark Secure when the site is actually served over TLS —
            # a Secure cookie on a plain-http localhost dev instance is simply
            # never sent, which looks exactly like a broken login.
            secure=bool(self.allowed_origins) or self.base_url.startswith("https://"),
            path="/",
        )
        return response

    def clear_session(self, response: web.Response) -> web.Response:
        response.del_cookie(security.SESSION_COOKIE, path="/")
        return response

    def new_state(self) -> str:
        return security.sign_state(security.new_state(), self.secret)

    # ── routes ───────────────────────────────────────────────────────────────
    def routes(self) -> list:
        routes: list = list(api_routes(self))
        routes.append(web.get("/api/{tail:.*}", _api_not_found))
        if os.path.isdir(STATIC_DIR):
            routes.append(web.static("/assets", os.path.join(STATIC_DIR, "assets")))
            routes.append(web.get("/{tail:.*}", self._spa))
        return routes

    async def _spa(self, request: web.Request) -> web.StreamResponse:
        """Serve a static file if one matches, else the app shell.

        The shell fallback is what makes `/g/123/fishing` survive a reload; the
        static check comes first so `/app.css` isn't answered with HTML.
        """
        rel = request.match_info.get("tail", "").strip("/")
        if rel:
            candidate = os.path.normpath(os.path.join(STATIC_DIR, rel))
            # normpath collapses `..`, so this comparison is what keeps a
            # crafted path from escaping the static directory.
            if candidate.startswith(STATIC_DIR + os.sep) and os.path.isfile(candidate):
                return web.FileResponse(
                    candidate,
                    headers={
                        "Cache-Control": (
                            "public, max-age=3600"
                            if rel.startswith("assets/")
                            else "no-cache"
                        )
                    },
                )
        index = os.path.join(STATIC_DIR, "index.html")
        if not os.path.isfile(index):
            raise H.not_found("The dashboard's files aren't installed.")
        return web.FileResponse(index, headers={"Cache-Control": "no-cache"})

    def middlewares(self) -> list:
        """Outermost first.

        CORS sits above the error handler on purpose: a preflight has to be
        answered even when the thing it is asking about would fail, and a 500
        without the CORS headers reads in the browser as a network error rather
        than as the server error it is.
        """
        return [
            H.cors_middleware(self),
            H.error_middleware,
            H.security_headers,
            H.session_middleware(self),
        ]


async def _api_not_found(request: web.Request) -> web.Response:
    raise H.not_found("No such endpoint.")


def _origins(raw) -> set[str]:
    """Parse the configured allow-list into normalised origins.

    Anything with a path, or without a scheme, is dropped rather than guessed
    at — a half-understood entry in a security allow-list is worse than a
    missing one.
    """
    if not raw:
        return set()
    parts = [p.strip().rstrip("/") for p in str(raw).replace(",", " ").split()]
    out = set()
    for part in parts:
        if not part.startswith(("http://", "https://")):
            log.warning("Ignoring dashboard_allowed_origins entry %r: no scheme", part)
            continue
        if part.count("/") > 2:
            log.warning("Ignoring dashboard_allowed_origins entry %r: has a path", part)
            continue
        out.add(part)
    return out


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def mount(bot: "NanoBot") -> Optional[Dashboard]:
    """Register the dashboard on the bot's shared HTTP server.

    Returns the Dashboard (so the bot can close its client session on shutdown)
    or None when it isn't switched on. The caller still has to `await
    bot.web.restart()` — the same contract `/health` follows, so one rebind
    covers every registration.
    """
    dash = Dashboard(bot)
    if not dash.enabled:
        return None
    missing = dash.missing_setup()
    if missing:
        log.warning(
            "Dashboard port is set but %s %s missing — the site will load and "
            "explain what to configure, but nobody can sign in yet.",
            ", ".join(missing),
            "is" if len(missing) == 1 else "are",
        )
    bot.web.register(
        DASHBOARD_OWNER,
        dash.host,
        dash.port,
        dash.routes(),
        dash.middlewares(),
    )
    log.info("🖥️  Dashboard registered on %s:%d", dash.host, dash.port)
    return dash


__all__ = ["Dashboard", "DASHBOARD_OWNER", "mount"]
