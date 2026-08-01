"""
web/security.py
Signed sessions, CSRF tokens, and the small crypto the dashboard leans on.

Deliberately dependency-free and Discord-free: everything here is stdlib
(hmac/hashlib/secrets/base64) over plain dicts, so it is unit-testable without a
gateway, a database, or a running HTTP server — the same split the rest of the
codebase uses for its pure helpers.

Three things live here:

*   **Sessions.** A session is a JSON payload signed with HMAC-SHA256 and handed
    to the browser as one cookie. Nothing secret is stored in it — the user's
    Discord id, their display name/avatar, their OAuth access token (needed to
    re-read their guild list) and an expiry. Signing rather than storing means a
    restart doesn't log everyone out, which matters for a bot that restarts on
    `!upgrade`.

*   **CSRF.** The cookie is `SameSite=Lax`, which stops a cross-site *form* post
    but is not on its own a policy. Every mutating request also has to echo a
    token bound to the session (`csrf_for`), the double-submit pattern — the
    token is derived from the session id under a separate HMAC key, so it can be
    verified without server-side state.

*   **Constant-time comparison** everywhere a secret is checked, because
    `==` on a token leaks its prefix through timing.

Payloads are base64url with the padding stripped, so a session survives a
cookie jar without escaping, and `verify()` returns ``None`` for *every* failure
mode — bad signature, wrong version, expired, truncated, not JSON. A caller that
only checks for None cannot accidentally trust a malformed session.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Optional

# Bumped when the payload's shape changes in a way older sessions can't satisfy.
# A mismatch is treated as "not logged in" rather than an error, so a deploy
# that changes the shape signs everyone out instead of crashing on their cookie.
SESSION_VERSION = 1

SESSION_COOKIE = "nanobot_session"
CSRF_HEADER = "X-NanoBot-CSRF"

# Domain separation: the same secret drives session signing and CSRF tokens, so
# each use mixes in its own label. Without this, a CSRF token would be a valid
# session signature for the right input.
_SESSION_LABEL = b"nanobot.session.v1"
_CSRF_LABEL = b"nanobot.csrf.v1"


# ── encoding ──────────────────────────────────────────────────────────────────
def b64e(raw: bytes) -> str:
    """URL-safe base64 with the padding stripped."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64d(text: str) -> bytes:
    """Inverse of :func:`b64e`. Raises ValueError on anything malformed."""
    pad = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + pad)
    except Exception as exc:  # binascii.Error and friends
        raise ValueError("not valid base64url") from exc


def constant_time(a: str, b: str) -> bool:
    """Compare two strings without leaking how much of the prefix matched."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def _mac(secret: str, label: bytes, payload: bytes) -> str:
    key = hashlib.sha256(label + secret.encode("utf-8")).digest()
    return b64e(hmac.new(key, payload, hashlib.sha256).digest())


# ── sessions ──────────────────────────────────────────────────────────────────
def new_secret() -> str:
    """A fresh signing secret, for when the operator hasn't configured one."""
    return secrets.token_urlsafe(48)


def sign_session(data: dict[str, Any], secret: str, ttl_seconds: int) -> str:
    """Serialize + sign a session payload into one cookie-safe string.

    The expiry lives *inside* the signed payload as well as on the cookie:
    a cookie's own `Max-Age` is a client-side courtesy and a hostile client
    simply keeps sending an expired one.
    """
    now = int(time.time())
    payload = dict(data)
    payload["v"] = SESSION_VERSION
    payload["iat"] = now
    payload["exp"] = now + int(ttl_seconds)
    payload.setdefault("sid", secrets.token_urlsafe(12))
    body = b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    return f"{body}.{_mac(secret, _SESSION_LABEL, body.encode('ascii'))}"


def verify_session(
    token: str, secret: str, now: Optional[float] = None
) -> Optional[dict]:
    """Return the payload of a valid, unexpired session — or None.

    Every failure mode collapses to None on purpose. Callers branch on
    "is there a session?", never on "why isn't there one?", so there is no path
    where a partially-trusted payload escapes this function.
    """
    if not token or not secret or token.count(".") != 1:
        return None
    body, sig = token.split(".", 1)
    if not constant_time(sig, _mac(secret, _SESSION_LABEL, body.encode("ascii"))):
        return None
    try:
        payload = json.loads(b64d(body))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("v") != SESSION_VERSION:
        return None
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        return None
    if (time.time() if now is None else now) >= exp:
        return None
    return payload


# ── CSRF ──────────────────────────────────────────────────────────────────────
def csrf_for(session: dict, secret: str) -> str:
    """The CSRF token belonging to one session.

    Derived rather than stored, so it needs no server-side table and survives a
    restart exactly as long as the session that owns it does.
    """
    sid = str(session.get("sid") or "")
    return _mac(secret, _CSRF_LABEL, sid.encode("utf-8"))


def csrf_ok(session: Optional[dict], secret: str, presented: Optional[str]) -> bool:
    """Whether `presented` is the token bound to `session`."""
    if not session or not presented:
        return False
    return constant_time(presented, csrf_for(session, secret))


# ── OAuth state ───────────────────────────────────────────────────────────────
def new_state() -> str:
    """An unguessable `state` value for the OAuth round trip."""
    return secrets.token_urlsafe(24)


def sign_state(state: str, secret: str, ttl_seconds: int = 600) -> str:
    """Sign an OAuth state so the callback can verify it without a store.

    The alternative — remembering issued states in a dict — leaks memory on
    every abandoned login and forgets them all on restart, turning a redeploy
    mid-login into a confusing failure.
    """
    exp = int(time.time()) + int(ttl_seconds)
    body = b64e(f"{state}:{exp}".encode("utf-8"))
    return f"{body}.{_mac(secret, _CSRF_LABEL, body.encode('ascii'))}"


def verify_state(token: str, secret: str, now: Optional[float] = None) -> Optional[str]:
    """Return the original state string, or None if it's invalid or expired."""
    if not token or token.count(".") != 1:
        return None
    body, sig = token.split(".", 1)
    if not constant_time(sig, _mac(secret, _CSRF_LABEL, body.encode("ascii"))):
        return None
    try:
        raw = b64d(body).decode("utf-8")
        state, exp_text = raw.rsplit(":", 1)
        exp = int(exp_text)
    except (ValueError, UnicodeDecodeError):
        return None
    if (time.time() if now is None else now) >= exp:
        return None
    return state
