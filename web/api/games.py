"""
web/api/games.py
Casino, crafting and progression — the play surfaces Phase 1 left in Discord.

Same shape as `play.py` and the same guards: a member gate (these are member
features), the play switch, and engine refusals surfaced as 409s carrying
whatever the engine attached — `retry_after` on a cooldown, the exact shortfall
on a failed craft. The frontend renders the server's sentence either way.

Reads stay available when web play is switched off, so a read-only instance is
still a place to look at your achievements and browse recipes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiohttp import web

from .. import http as H
from ..engine import casino as C
from ..engine import crafting as Cr
from ..engine import identity as I
from ..engine import progression as P
from .play import guarded_routes

if TYPE_CHECKING:
    from ..app import Dashboard


def routes(dash: "Dashboard") -> list:
    play, read = guarded_routes(dash)

    # ── Casino ───────────────────────────────────────────────────────────────
    @read
    async def casino_state(request: web.Request) -> web.Response:
        return H.ok(await C.state(H.user_id_of(request), H.guild_of(request).id))

    @play
    async def casino_play(request: web.Request) -> web.Response:
        """One endpoint for the four instant games, one body shape.

        Kept together rather than split four ways because they differ only in
        which extra field they read — and a single `/casino/play` is what lets
        the frontend's bet control be one component.
        """
        game = request.match_info["game"]
        if game not in C.GAMES:
            raise H.not_found("No such game.")
        body = await H.body_json(request)
        user_id = H.user_id_of(request)
        guild_id = H.guild_of(request).id
        bet = _bet(body.get("bet"))

        if game == "flip":
            return H.ok(
                await C.flip(user_id, guild_id, bet, str(body.get("side") or ""))
            )
        if game == "dice":
            return H.ok(await C.dice(user_id, guild_id, bet))
        if game == "slots":
            return H.ok(await C.slots(user_id, guild_id, bet))
        if game == "roulette":
            return H.ok(
                await C.roulette(user_id, guild_id, bet, str(body.get("space") or ""))
            )
        return H.ok(await C.blackjack(user_id, guild_id, bet))

    @play
    async def blackjack_action(request: web.Request) -> web.Response:
        body = await H.body_json(request)
        try:
            hand_id = int(request.match_info["hand_id"])
        except (TypeError, ValueError):
            raise H.bad_request("That isn't a hand.") from None
        return H.ok(
            await C.blackjack_action(
                H.user_id_of(request),
                H.guild_of(request).id,
                hand_id,
                str(body.get("action") or ""),
            )
        )

    # ── Crafting ─────────────────────────────────────────────────────────────
    @read
    async def crafting_state(request: web.Request) -> web.Response:
        return H.ok(await Cr.state(H.user_id_of(request), H.guild_of(request).id))

    @play
    async def craft(request: web.Request) -> web.Response:
        body = await H.body_json(request)
        return H.ok(
            await Cr.craft(
                H.user_id_of(request),
                H.guild_of(request).id,
                str(body.get("recipe") or ""),
                _qty(body.get("qty", 1)),
            )
        )

    # ── Progression ──────────────────────────────────────────────────────────
    @read
    async def progression_state(request: web.Request) -> web.Response:
        """Someone's progression. Only your own is ever *evaluated*."""
        guild = H.guild_of(request)
        viewer = H.user_id_of(request)
        target = request.query.get("member")
        user_id = int(target) if target and target.isdigit() else viewer
        if guild.get_member(user_id) is None:
            raise H.not_found("That member isn't in this server.")
        return H.ok(await P.state(user_id, guild.id, viewer_id=viewer))

    @play
    async def set_title(request: web.Request) -> web.Response:
        body = await H.body_json(request)
        return H.ok(
            await P.set_title(H.user_id_of(request), str(body.get("title") or ""))
        )

    @play
    async def prestige(request: web.Request) -> web.Response:
        return H.ok(await P.prestige(H.user_id_of(request), H.guild_of(request).id))

    # ── Wardrobe ─────────────────────────────────────────────────────────────
    @read
    async def wardrobe(request: web.Request) -> web.Response:
        return H.ok(await I.state(H.user_id_of(request), H.guild_of(request).id))

    @play
    async def equip(request: web.Request) -> web.Response:
        """Apply a whole loadout at once — the thing `/profile equip` does in
        one command, done as one request."""
        body = await H.body_json(request)
        return H.ok(await I.equip(H.user_id_of(request), body.get("loadout") or {}))

    @play
    async def unlock_cosmetic(request: web.Request) -> web.Response:
        body = await H.body_json(request)
        return H.ok(
            await I.unlock(
                H.user_id_of(request),
                H.guild_of(request).id,
                str(body.get("key") or ""),
            )
        )

    @read
    async def casino_board(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        scope = request.query.get("scope", "server")
        page = max(1, _qty(request.query.get("page", 1), hi=1000))
        member_ids = (
            None if scope == "global" else [m.id for m in guild.members if not m.bot]
        )
        data = await C.leaderboard(
            member_ids=member_ids, limit=25, offset=(page - 1) * 25
        )
        for row in data["rows"]:
            member = guild.get_member(int(row["user_id"]))
            row["name"] = member.display_name if member else f"User {row['user_id']}"
            row["avatar"] = str(member.display_avatar.url) if member else None
            row["is_me"] = int(row["user_id"]) == H.user_id_of(request)
        return H.ok({**data, "scope": scope, "page": page})

    return [
        web.get("/api/guilds/{guild_id}/casino", casino_state),
        web.post("/api/guilds/{guild_id}/casino/play/{game}", casino_play),
        web.post("/api/guilds/{guild_id}/casino/blackjack/{hand_id}", blackjack_action),
        web.get("/api/guilds/{guild_id}/casino/leaderboard", casino_board),
        web.get("/api/guilds/{guild_id}/crafting", crafting_state),
        web.post("/api/guilds/{guild_id}/crafting/make", craft),
        web.get("/api/guilds/{guild_id}/progression", progression_state),
        web.post("/api/guilds/{guild_id}/progression/title", set_title),
        web.post("/api/guilds/{guild_id}/progression/prestige", prestige),
        web.get("/api/guilds/{guild_id}/wardrobe", wardrobe),
        web.post("/api/guilds/{guild_id}/wardrobe/equip", equip),
        web.post("/api/guilds/{guild_id}/wardrobe/unlock", unlock_cosmetic),
    ]


def _bet(value) -> int:
    try:
        bet = int(value)
    except (TypeError, ValueError):
        raise H.bad_request("How many coins are you betting?") from None
    if bet <= 0:
        raise H.bad_request("Bet at least one coin.")
    if bet > 10**9:
        raise H.bad_request("That's not a real bet.")
    return bet


def _qty(value, *, hi: int = 1000) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise H.bad_request("That has to be a whole number.") from None
    if number < 1 or number > hi:
        raise H.bad_request(f"Pick a number between 1 and {hi:,}.")
    return number
