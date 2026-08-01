"""
web/http.py
Request plumbing: JSON replies, error shapes, and the two guards every route uses.

Two ideas the rest of the API leans on.

**One error shape.** Every failure is `{"error": {"code", "message", "hint"}}`
with a machine-readable code and a sentence a human can act on. The frontend
renders `message` verbatim in a toast, so a message here is user-facing copy,
not a log line — "You need Manage Server in this server to change that", never
"403 forbidden".

**Guards compose, they don't repeat.** `require_session` and `require_manager`
are decorators rather than four lines at the top of thirty handlers, because a
handler that forgets the check is indistinguishable from one that doesn't need
it, and that is exactly the bug you never notice until someone finds it.
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Awaitable, Callable, Optional

from aiohttp import web

from . import permissions as perms
from . import security

log = logging.getLogger("NanoBot.dashboard")

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]

# Set on the *request* by the session middleware. Typed `RequestKey`s rather
# than plain strings: aiohttp warns about untyped keys, and a typed one can't
# collide with another feature sharing the same port (the vote webhook does).
SESSION_KEY: "web.RequestKey" = web.RequestKey("nanobot_session", object)
DASHBOARD_KEY: "web.RequestKey" = web.RequestKey("nanobot_dashboard", object)
GUILD_KEY: "web.RequestKey" = web.RequestKey("nanobot_guild", object)


class ApiError(Exception):
    """An error with a status, a code, and something worth saying to the user."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        hint: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.hint = hint

    def response(self) -> web.Response:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.hint:
            payload["hint"] = self.hint
        return web.json_response({"error": payload}, status=self.status)


def bad_request(message: str, *, hint: str | None = None) -> ApiError:
    return ApiError(400, "bad_request", message, hint)


def unauthorized(message: str = "Sign in with Discord to continue.") -> ApiError:
    return ApiError(401, "unauthorized", message)


def forbidden(message: str, *, hint: str | None = None) -> ApiError:
    return ApiError(403, "forbidden", message, hint)


def not_found(message: str = "That doesn't exist.") -> ApiError:
    return ApiError(404, "not_found", message)


def conflict(message: str, *, hint: str | None = None) -> ApiError:
    return ApiError(409, "conflict", message, hint)


def too_many(message: str, *, hint: str | None = None) -> ApiError:
    return ApiError(429, "rate_limited", message, hint)


def ok(payload: Any = None, **extra) -> web.Response:
    """A successful JSON reply."""
    body: dict[str, Any] = {"ok": True}
    if isinstance(payload, dict):
        body.update(payload)
    elif payload is not None:
        body["data"] = payload
    body.update(extra)
    return web.json_response(body)


# ── request accessors ─────────────────────────────────────────────────────────
def dashboard(request: web.Request):
    """The Dashboard instance serving this request.

    Stashed on the request, not the application: `HttpServer` builds one aiohttp
    app per (host, port) and merges every feature's routes into it, so the app
    object isn't ours to hang state on.
    """
    return request[DASHBOARD_KEY]


def session_of(request: web.Request) -> Optional[dict]:
    return request.get(SESSION_KEY)


def guild_of(request: web.Request):
    """The guild a `require_manager`/`require_member` handler is acting on.

    Read back from the request rather than resolved again, so a handler can
    never act on a *different* guild than the one it was authorised for.
    """
    return request[GUILD_KEY]


def user_id_of(request: web.Request) -> int:
    session = session_of(request)
    if not session:
        raise unauthorized()
    try:
        return int(session["uid"])
    except (KeyError, TypeError, ValueError):
        raise unauthorized() from None


async def body_json(request: web.Request) -> dict:
    """Parse a JSON body, refusing anything that isn't an object.

    A list or a bare string reaching a handler that expects `.get()` is an
    AttributeError deep in business logic; caught here it's a 400 with a
    sentence.
    """
    if not request.can_read_body:
        return {}
    try:
        data = await request.json()
    except Exception:
        raise bad_request("That request body wasn't valid JSON.") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise bad_request("Expected a JSON object.")
    return data


# ── guards ────────────────────────────────────────────────────────────────────
def require_session(handler: Handler) -> Handler:
    """Refuse anyone without a valid session."""

    @functools.wraps(handler)
    async def wrapper(request: web.Request) -> web.StreamResponse:
        if not session_of(request):
            raise unauthorized()
        return await handler(request)

    return wrapper


def require_manager(handler: Handler) -> Handler:
    """Refuse anyone without Manage Server in the guild named by the path.

    Resolves the guild from `match_info["guild_id"]` and stashes the resolved
    `discord.Guild` on the request, so the handler never re-does the lookup and
    can never accidentally act on a *different* guild than the one it checked.
    """

    @functools.wraps(handler)
    async def wrapper(request: web.Request) -> web.StreamResponse:
        if not session_of(request):
            raise unauthorized()
        dash = dashboard(request)
        guild = await dash.resolve_guild(request)
        await dash.assert_manager(request, guild)
        request[GUILD_KEY] = guild
        return await handler(request)

    return wrapper


def require_member(handler: Handler) -> Handler:
    """Like :func:`require_manager`, but only requires being *in* the server.

    The economy is played by members, not admins — a play route gated on Manage
    Server would lock the feature to the people least likely to use it.
    """

    @functools.wraps(handler)
    async def wrapper(request: web.Request) -> web.StreamResponse:
        if not session_of(request):
            raise unauthorized()
        dash = dashboard(request)
        guild = await dash.resolve_guild(request)
        await dash.assert_member(request, guild)
        request[GUILD_KEY] = guild
        return await handler(request)

    return wrapper


# ── middleware ────────────────────────────────────────────────────────────────
@web.middleware
async def error_middleware(
    request: web.Request, handler: Handler
) -> web.StreamResponse:
    """Turn every exception into the one error shape.

    An unexpected exception becomes a generic 500 — the detail goes to the log,
    never to the browser, because a stack trace in an API response is a map of
    the codebase for anyone who can reach the port.
    """
    try:
        return await handler(request)
    except ApiError as exc:
        return exc.response()
    except web.HTTPException as exc:
        if exc.status >= 400 and request.path.startswith("/api/"):
            return ApiError(
                exc.status,
                "http_error",
                exc.reason or "Something went wrong.",
            ).response()
        raise
    except Exception:
        log.exception("Unhandled error serving %s %s", request.method, request.path)
        return ApiError(
            500,
            "internal_error",
            "Something broke on our side. It's been logged.",
        ).response()


@web.middleware
async def security_headers(
    request: web.Request, handler: Handler
) -> web.StreamResponse:
    """Headers that cost nothing and close whole classes of bug.

    The CSP is strict on purpose: no inline script and no external script host,
    which is why the frontend ships as ES modules rather than a bundle with an
    inline boot snippet. `img-src` allows Discord's CDN, since every avatar and
    server icon on the page comes from there.
    """
    response = await handler(request)
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("Referrer-Policy", "no-referrer")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault(
        "Permissions-Policy", "geolocation=(), microphone=(), camera=(), payment=()"
    )
    if not request.path.startswith("/api/"):
        headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https://cdn.discordapp.com; "
            "connect-src 'self'; "
            "font-src 'self'; "
            "form-action 'self' https://discord.com; "
            "frame-ancestors 'none'; "
            "base-uri 'none'",
        )
    return response


def cors_middleware(dash) -> Any:
    """Answer cross-origin requests from the configured allow-list, and no one else.

    This exists for one deployment shape: the frontend served from somewhere
    else (GitHub Pages, a CDN, a `python -m http.server` on a laptop) talking to
    a NanoBot API on its own host. Same-origin hosting — the default — never
    reaches any of this, because a same-origin request carries no `Origin` the
    browser will police.

    Three things make it safe to send credentials across:

    * the allow-list is exact and configured (`dashboard_allowed_origins`),
      never `*` and never reflected blindly — an echoing allow-list with
      credentials on is the same thing as no allow-list at all;
    * `Vary: Origin` so a cache can't serve one site's allowance to another;
    * the CSRF check in `session_middleware` still runs. CORS decides who may
      *read* the answer; it does not decide who may cause the write, and a
      cross-origin form post never needed permission to be sent.

    A preflight is answered here and goes no further: it asks about a request
    that hasn't happened, so running it through the session and CSRF checks
    would refuse a question rather than an action.
    """

    @web.middleware
    async def middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
        origin = request.headers.get("Origin")
        allowed = bool(origin) and origin in dash.allowed_origins

        if request.method == "OPTIONS" and "Access-Control-Request-Method" in (
            request.headers
        ):
            response: web.StreamResponse = web.Response(status=204 if allowed else 403)
        else:
            response = await handler(request)

        if allowed:
            headers = response.headers
            headers["Access-Control-Allow-Origin"] = origin
            headers["Access-Control-Allow-Credentials"] = "true"
            headers["Access-Control-Allow-Headers"] = (
                f"content-type, {security.CSRF_HEADER.lower()}"
            )
            headers["Access-Control-Allow-Methods"] = (
                "GET, POST, PATCH, PUT, DELETE, OPTIONS"
            )
            headers["Access-Control-Max-Age"] = "600"
        if origin:
            response.headers.add("Vary", "Origin")
        return response

    return middleware


def session_middleware(dash) -> Any:
    """Attach the verified session (if any) and enforce CSRF on writes.

    Both halves belong together: CSRF is only meaningful against a session, and
    a session that skipped the CSRF check on a POST is a session that can be
    driven from another origin.
    """

    @web.middleware
    async def middleware(request: web.Request, handler: Handler) -> web.StreamResponse:
        request[DASHBOARD_KEY] = dash
        raw = request.cookies.get(security.SESSION_COOKIE)
        session = security.verify_session(raw, dash.secret) if raw else None
        request[SESSION_KEY] = session

        if request.method not in ("GET", "HEAD", "OPTIONS") and request.path.startswith(
            "/api/"
        ):
            presented = request.headers.get(security.CSRF_HEADER)
            if not security.csrf_ok(session, dash.secret, presented):
                raise forbidden(
                    "Your session expired while you were away. Reload the page "
                    "and try again.",
                    hint="csrf",
                )
        return await handler(request)

    return middleware


__all__ = [
    "ApiError",
    "bad_request",
    "body_json",
    "conflict",
    "cors_middleware",
    "dashboard",
    "error_middleware",
    "forbidden",
    "guild_of",
    "not_found",
    "ok",
    "perms",
    "require_manager",
    "require_member",
    "require_session",
    "security_headers",
    "session_middleware",
    "session_of",
    "too_many",
    "unauthorized",
    "user_id_of",
]
