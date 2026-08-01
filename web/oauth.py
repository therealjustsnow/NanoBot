"""
web/oauth.py
The Discord OAuth2 round trip, and the two API reads the dashboard needs.

Scopes are `identify` and `guilds` — the minimum that answers "who are you?" and
"which servers may you configure?". Nothing here asks for `email`, `guilds.join`
or a bot token: a dashboard that can add you to servers is a dashboard whose
compromise is somebody else's problem too.

The guild list is cached per user for a minute. Discord rate-limits
`/users/@me/guilds` hard (it is one of the strictest routes on the API), and the
server picker is the page people bounce off and back to most, so re-fetching on
every navigation is how a dashboard earns a 429 and shows an empty list at the
worst moment. A minute is short enough that a newly-joined server appears
without the user wondering, and the picker offers a refresh anyway.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import urlencode

import aiohttp

from utils.db._ttl import TTLCache

log = logging.getLogger("NanoBot.dashboard.oauth")

API_BASE = "https://discord.com/api/v10"
AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
TOKEN_URL = f"{API_BASE}/oauth2/token"
SCOPES = "identify guilds"

CDN = "https://cdn.discordapp.com"

# user_id -> the guild list Discord last gave us.
_guild_cache = TTLCache(maxsize=2048, ttl=60.0)


class OAuthError(RuntimeError):
    """A step of the OAuth exchange failed in a way the user should be told about."""


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    """Where to send the browser to start a login."""
    return f"{AUTHORIZE_URL}?" + urlencode(
        {
            "client_id": str(client_id),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPES,
            "state": state,
            # Skip the "authorize?" screen for a user who already has, so
            # re-logging in after a session expires is one redirect and no taps.
            "prompt": "none",
        }
    )


async def exchange_code(
    session: aiohttp.ClientSession,
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> dict[str, Any]:
    """Trade an authorization code for an access token."""
    data = {
        "client_id": str(client_id),
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
    }
    async with session.post(
        TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    ) as resp:
        body = await resp.text()
        if resp.status != 200:
            log.warning("OAuth token exchange failed (%s): %s", resp.status, body[:300])
            raise OAuthError(
                "Discord rejected the login. This usually means the redirect URL "
                "in config.ini doesn't exactly match the one registered in the "
                "developer portal."
            )
        try:
            return await _json(resp, body)
        except ValueError as exc:
            raise OAuthError("Discord returned something that wasn't JSON.") from exc


async def _json(resp: aiohttp.ClientResponse, body: str) -> dict:
    import json

    return json.loads(body)


async def _get(
    session: aiohttp.ClientSession, path: str, access_token: str
) -> Optional[Any]:
    """One authenticated GET against the Discord API, or None on failure."""
    try:
        async with session.get(
            f"{API_BASE}{path}",
            headers={"Authorization": f"Bearer {access_token}"},
        ) as resp:
            if resp.status == 401:
                return None  # token revoked or expired — caller re-authenticates
            if resp.status == 429:
                log.warning("Rate limited by Discord on %s", path)
                return None
            if resp.status != 200:
                log.warning("Discord API %s returned %s", path, resp.status)
                return None
            return await resp.json()
    except (aiohttp.ClientError, TimeoutError) as exc:
        log.warning("Discord API %s failed: %s", path, exc)
        return None


async def fetch_user(
    session: aiohttp.ClientSession, access_token: str
) -> Optional[dict]:
    """The logged-in user's id, name and avatar."""
    data = await _get(session, "/users/@me", access_token)
    if not isinstance(data, dict) or not data.get("id"):
        return None
    return {
        "id": str(data["id"]),
        "username": data.get("username") or "Unknown",
        "global_name": data.get("global_name") or data.get("username") or "Unknown",
        "avatar": data.get("avatar"),
        "discriminator": data.get("discriminator") or "0",
    }


async def fetch_guilds(
    session: aiohttp.ClientSession,
    access_token: str,
    user_id: str,
    *,
    force: bool = False,
) -> Optional[list[dict]]:
    """Every server the user is in, from cache unless `force`.

    None means "we couldn't ask" — distinct from an empty list, which means the
    user genuinely shares no servers. The picker words those two very
    differently, and conflating them is how a rate limit reads as "you've been
    removed from every server you own".
    """
    if not force:
        cached = _guild_cache.get(user_id)
        if cached is not None:
            return cached
    data = await _get(session, "/users/@me/guilds", access_token)
    if not isinstance(data, list):
        return None
    return _guild_cache.put(user_id, data)


def forget_guilds(user_id: str) -> None:
    """Drop a user's cached guild list (on logout, or an explicit refresh)."""
    _guild_cache.put(user_id, None, now=0.0)


def avatar_url(user_id: str, avatar_hash: Optional[str], size: int = 128) -> str:
    """A CDN URL for a user's avatar, falling back to Discord's default set."""
    if avatar_hash:
        ext = "gif" if str(avatar_hash).startswith("a_") else "png"
        return f"{CDN}/avatars/{user_id}/{avatar_hash}.{ext}?size={size}"
    try:
        index = (int(user_id) >> 22) % 6
    except (TypeError, ValueError):
        index = 0
    return f"{CDN}/embed/avatars/{index}.png"


def guild_icon_url(
    guild_id: str, icon_hash: Optional[str], size: int = 128
) -> Optional[str]:
    """A CDN URL for a server icon, or None when it has none."""
    if not icon_hash:
        return None
    ext = "gif" if str(icon_hash).startswith("a_") else "png"
    return f"{CDN}/icons/{guild_id}/{icon_hash}.{ext}?size={size}"
