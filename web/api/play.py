"""
web/api/play.py
The economy, played from the browser.

Every handler here is a thin wrapper: parse, call `web/engine/`, shape the
reply. The engine owns the rules — cooldowns, claims, rolls — so a route can
neither invent a mechanic nor forget one.

Three rules the whole family follows.

**Playing is a POST.** A cast changes the world, so it can't be a GET: a
prefetching browser or an over-eager link scanner would otherwise fish on
someone's behalf. Every mutating call also carries the CSRF token, enforced in
the middleware.

**Membership is the gate, not Manage Server.** These are member features. Gating
them on an admin permission would hand the economy to the people least likely to
play it.

**A refusal is a 409 with a sentence.** "You're still on cooldown" is a normal
outcome of asking, not an error — it comes back with `retry_after` so the button
can show a countdown rather than just going red.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from aiohttp import web

from utils import db

from .. import http as H
from ..engine import activities as A
from ..engine import economy as E
from ..engine import fishing as F
from ..engine.fishing import PlayError

if TYPE_CHECKING:
    from ..app import Dashboard


class _RefusalResponse(H.ApiError):
    """A refusal, with whatever the engine attached to it.

    409 rather than 400: nothing about the request was malformed. The world just
    said no, and the client's correct response is to wait or to go and get more
    coins, not to fix the request. `retry_after` and a craft's exact shortfall
    ride along so the button can show a countdown or name the missing material.
    """

    def __init__(self, exc: PlayError) -> None:
        super().__init__(409, exc.code, exc.message)
        self.extra = exc.extra

    def response(self) -> web.Response:
        payload = {"code": self.code, "message": self.message, **(self.extra or {})}
        return web.json_response({"error": payload}, status=self.status)


def _playable(handler: Callable) -> Callable:
    """Wrap a play handler: engine refusals become 409s rather than 500s."""

    async def wrapper(request: web.Request) -> web.StreamResponse:
        try:
            return await handler(request)
        except PlayError as exc:
            raise _RefusalResponse(exc) from None

    wrapper.__name__ = getattr(handler, "__name__", "play")
    return wrapper


def guarded_routes(dash: "Dashboard"):
    """The two decorators every play surface uses: `(play, read)`.

    Shared rather than redefined per module so a new play route can't
    accidentally skip the play switch or turn a refusal into a 500 — the
    decorators *are* the contract.
    """

    def play(handler):
        """Member-gated, play-enabled, refusal-aware — in that order."""

        async def guarded(request: web.Request) -> web.StreamResponse:
            dash.assert_play_enabled()
            return await handler(request)

        guarded.__name__ = getattr(handler, "__name__", "play")
        return H.require_member(_playable(guarded))

    def read(handler):
        """A read-only view — available even when web play is switched off."""
        return H.require_member(_playable(handler))

    return play, read


def routes(dash: "Dashboard") -> list:
    play, read = guarded_routes(dash)

    # ── Fishing ──────────────────────────────────────────────────────────────
    @read
    async def fishing_state(request: web.Request) -> web.Response:
        return H.ok(await F.state(H.user_id_of(request), H.guild_of(request).id))

    @play
    async def fishing_cast(request: web.Request) -> web.Response:
        return H.ok(await F.cast(H.user_id_of(request), H.guild_of(request).id))

    @play
    async def fishing_sell(request: web.Request) -> web.Response:
        body = await H.body_json(request)
        return H.ok(
            await F.sell(
                H.user_id_of(request),
                H.guild_of(request).id,
                str(body.get("target", "all")),
            )
        )

    @play
    async def fishing_travel(request: web.Request) -> web.Response:
        body = await H.body_json(request)
        spot = str(body.get("spot") or "")
        if not spot:
            raise H.bad_request("Pick a spot to travel to.")
        return H.ok(await F.travel(H.user_id_of(request), H.guild_of(request).id, spot))

    @play
    async def fishing_upgrade(request: web.Request) -> web.Response:
        return H.ok(await F.upgrade_rod(H.user_id_of(request), H.guild_of(request).id))

    @play
    async def fishing_buy(request: web.Request) -> web.Response:
        body = await H.body_json(request)
        return H.ok(
            await F.buy(
                H.user_id_of(request),
                H.guild_of(request).id,
                str(body.get("item") or ""),
                _qty(body.get("qty", 1)),
            )
        )

    @play
    async def fishing_arm(request: web.Request) -> web.Response:
        body = await H.body_json(request)
        return H.ok(
            await F.arm(
                H.user_id_of(request),
                str(body.get("item") or ""),
                _qty(body.get("qty", 1)),
            )
        )

    @play
    async def fishing_trap(request: web.Request) -> web.Response:
        return H.ok(await F.trap(H.user_id_of(request), H.guild_of(request).id))

    # ── Activities ───────────────────────────────────────────────────────────
    @read
    async def adventure_state(request: web.Request) -> web.Response:
        return H.ok(await A.state(H.user_id_of(request), H.guild_of(request).id))

    @play
    async def adventure_run(request: web.Request) -> web.Response:
        activity = request.match_info["activity"]
        return H.ok(
            await A.run(H.user_id_of(request), H.guild_of(request).id, activity)
        )

    @play
    async def adventure_collect(request: web.Request) -> web.Response:
        body = await H.body_json(request)
        return H.ok(
            await A.run_many(
                H.user_id_of(request), H.guild_of(request).id, body.get("activity")
            )
        )

    @play
    async def adventure_choose(request: web.Request) -> web.Response:
        body = await H.body_json(request)
        token = str(body.get("token") or "")
        option = str(body.get("option") or "")
        if not token or not option:
            raise H.bad_request("That choice is missing something.")
        return H.ok(await A.choose(H.user_id_of(request), token, option))

    @play
    async def pickaxe_upgrade(request: web.Request) -> web.Response:
        return H.ok(
            await A.upgrade_pickaxe(H.user_id_of(request), H.guild_of(request).id)
        )

    @play
    async def rob(request: web.Request) -> web.Response:
        body = await H.body_json(request)
        target = _snowflake(body.get("member"), "member")
        guild = H.guild_of(request)
        member = guild.get_member(target)
        if member is None:
            raise H.bad_request("They're not in this server.")
        if member.bot:
            raise H.bad_request("You can't rob a bot.")
        return H.ok(await A.rob(H.user_id_of(request), guild.id, target))

    # ── Wallet, inventory, shop ──────────────────────────────────────────────
    @read
    async def wallet(request: web.Request) -> web.Response:
        return H.ok(await E.wallet(H.user_id_of(request), H.guild_of(request).id))

    @play
    async def daily(request: web.Request) -> web.Response:
        return H.ok(await E.claim_daily(H.user_id_of(request), H.guild_of(request).id))

    @play
    async def pay(request: web.Request) -> web.Response:
        body = await H.body_json(request)
        target = _snowflake(body.get("member"), "member")
        guild = H.guild_of(request)
        if guild.get_member(target) is None:
            raise H.bad_request("They're not in this server.")
        return H.ok(
            await E.pay(
                H.user_id_of(request),
                guild.id,
                target,
                _qty(body.get("amount"), hi=10**9),
            )
        )

    @read
    async def inventory(request: web.Request) -> web.Response:
        return H.ok(await E.inventory(H.user_id_of(request), H.guild_of(request).id))

    @play
    async def inventory_use(request: web.Request) -> web.Response:
        body = await H.body_json(request)
        return H.ok(
            await E.use(
                H.user_id_of(request),
                str(body.get("item") or ""),
                _qty(body.get("qty", 1)),
            )
        )

    @play
    async def inventory_open(request: web.Request) -> web.Response:
        """Open a container. Separate from `use` because it is a separate
        thing: arming an item writes an effect, opening one spends two items."""
        body = await H.body_json(request)
        return H.ok(
            await E.open_container(
                H.user_id_of(request),
                str(body.get("item") or ""),
                _qty(body.get("qty", 1)),
            )
        )

    @play
    async def inventory_sell(request: web.Request) -> web.Response:
        body = await H.body_json(request)
        target = str(body.get("item") or "")
        if body.get("bulk"):
            return H.ok(await E.sell_bulk(H.user_id_of(request), target or "all"))
        return H.ok(
            await E.sell(H.user_id_of(request), target, _qty(body.get("qty", 1)))
        )

    @play
    async def inventory_gift(request: web.Request) -> web.Response:
        body = await H.body_json(request)
        target = _snowflake(body.get("member"), "member")
        if H.guild_of(request).get_member(target) is None:
            raise H.bad_request("They're not in this server.")
        return H.ok(
            await E.gift(
                H.user_id_of(request),
                target,
                str(body.get("item") or ""),
                _qty(body.get("qty", 1)),
            )
        )

    @read
    async def shop(request: web.Request) -> web.Response:
        return H.ok(await E.shop(H.user_id_of(request), H.guild_of(request).id))

    @play
    async def shop_buy(request: web.Request) -> web.Response:
        """Buy a server reward.

        Goes through `db.purchase_item` untouched — that accessor is where the
        funds check, the stock decrement, the per-user limit and the cooldown
        all live, along with the compensating writes that make a sold-out race
        safe. Reimplementing any of it here is exactly how a dashboard oversells
        a limited reward.
        """
        guild = H.guild_of(request)
        user_id = H.user_id_of(request)
        item_id = _qty(request.match_info["item_id"], hi=2**31)
        result = await db.purchase_item(guild.id, item_id, user_id)
        if not result.get("ok"):
            raise _RefusalResponse(
                PlayError(
                    _purchase_message(result),
                    code=result.get("reason", "refused"),
                    **(
                        {"retry_after": result["retry_after"]}
                        if result.get("retry_after")
                        else {}
                    ),
                )
            )
        item = result.get("item") or {}
        if item.get("kind") == "role" and item.get("role_id"):
            await _grant_role(guild, user_id, int(item["role_id"]), item_id, item)
        return H.ok({"purchase": result, "balance": await db.get_balance(user_id)})

    async def _grant_role(guild, user_id: int, role_id: int, item_id: int, item: dict):
        """Hand over a role purchase, refunding if Discord refuses.

        The refund is the point: coins were already taken, so a failed grant
        that stayed silent would be a member paying for nothing.
        """
        role = guild.get_role(role_id)
        member = guild.get_member(user_id)
        try:
            if role is None or member is None:
                raise RuntimeError("role or member missing")
            await member.add_roles(role, reason="NanoBot shop purchase (dashboard)")
        except Exception:
            await db.add_coins(user_id, int(item.get("price", 0)))
            if item.get("stock", -1) != -1:
                await db.restock_shop_item(guild.id, item_id, 1)
            raise _RefusalResponse(
                PlayError(
                    "NanoBot couldn't give you that role, so your coins have "
                    "been refunded. Ask an admin to check where NanoBot's role "
                    "sits in the role list.",
                    code="grant_failed",
                )
            ) from None

    # ── Leaderboards ─────────────────────────────────────────────────────────
    @read
    async def leaderboard(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        board = request.match_info["board"]
        scope = request.query.get("scope", "server")
        page = max(1, _qty(request.query.get("page", 1), hi=1000))
        limit = 25
        member_ids = None
        if scope != "global":
            member_ids = [m.id for m in guild.members if not m.bot]
        data = await E.leaderboard(
            board, member_ids=member_ids, limit=limit, offset=(page - 1) * limit
        )
        for row in data["rows"]:
            member = guild.get_member(int(row["user_id"]))
            row["name"] = member.display_name if member else f"User {row['user_id']}"
            row["avatar"] = str(member.display_avatar.url) if member else None
            row["is_me"] = int(row["user_id"]) == H.user_id_of(request)
        return H.ok({**data, "scope": scope, "page": page})

    return [
        web.get("/api/guilds/{guild_id}/fishing", fishing_state),
        web.post("/api/guilds/{guild_id}/fishing/cast", fishing_cast),
        web.post("/api/guilds/{guild_id}/fishing/sell", fishing_sell),
        web.post("/api/guilds/{guild_id}/fishing/travel", fishing_travel),
        web.post("/api/guilds/{guild_id}/fishing/upgrade", fishing_upgrade),
        web.post("/api/guilds/{guild_id}/fishing/buy", fishing_buy),
        web.post("/api/guilds/{guild_id}/fishing/arm", fishing_arm),
        web.post("/api/guilds/{guild_id}/fishing/trap", fishing_trap),
        web.get("/api/guilds/{guild_id}/adventure", adventure_state),
        web.post("/api/guilds/{guild_id}/adventure/run/{activity}", adventure_run),
        web.post("/api/guilds/{guild_id}/adventure/collect", adventure_collect),
        web.post("/api/guilds/{guild_id}/adventure/choose", adventure_choose),
        web.post("/api/guilds/{guild_id}/adventure/pickaxe", pickaxe_upgrade),
        web.post("/api/guilds/{guild_id}/adventure/rob", rob),
        web.get("/api/guilds/{guild_id}/wallet", wallet),
        web.post("/api/guilds/{guild_id}/wallet/daily", daily),
        web.post("/api/guilds/{guild_id}/wallet/pay", pay),
        web.get("/api/guilds/{guild_id}/inventory", inventory),
        web.post("/api/guilds/{guild_id}/inventory/use", inventory_use),
        web.post("/api/guilds/{guild_id}/inventory/open", inventory_open),
        web.post("/api/guilds/{guild_id}/inventory/sell", inventory_sell),
        web.post("/api/guilds/{guild_id}/inventory/gift", inventory_gift),
        web.get("/api/guilds/{guild_id}/shop", shop),
        web.post("/api/guilds/{guild_id}/shop/{item_id}/buy", shop_buy),
        web.get("/api/guilds/{guild_id}/leaderboard/{board}", leaderboard),
    ]


def _qty(value, *, hi: int = 1000) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise H.bad_request("That has to be a whole number.") from None
    if number < 1 or number > hi:
        raise H.bad_request(f"Pick a number between 1 and {hi:,}.")
    return number


def _snowflake(value, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise H.bad_request(f"Pick a {field}.") from None


def _purchase_message(result: dict) -> str:
    """Say why a purchase didn't go through, in the shop's own words."""
    return {
        "missing": "That reward isn't in the shop any more.",
        "disabled": "That reward isn't on sale right now.",
        "out_of_stock": "That one's sold out.",
        "funds": "You don't have enough coins for that yet.",
        "limit": "You've already bought that as many times as you're allowed.",
        "cooldown": "You bought that recently — give it a while.",
    }.get(result.get("reason", ""), "That purchase didn't go through.")
