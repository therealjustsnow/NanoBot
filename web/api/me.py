"""
web/api/me.py
The account: profile, progression, cosmetics, and the rendered cards.

The account is genuinely global — coins, levels, achievements and cosmetics
follow a member between servers — so these endpoints deliberately take no guild
in the path where nothing about the answer depends on one. The two that do
(`profile`, because the card draws the server level beside the global one, and
anything quoting a currency name) say so by taking a guild.

`/api/me/card/{kind}` re-serves the *same renderers* Discord gets — the profile,
wallet, trophy and cookie cards from `utils/`. A browser could draw its own
version, and it would then be a second thing to keep in sync with a card someone
already screenshots. Sending the real PNG means the image on the website is the
image in the chat, which is the whole point of a profile people want to share.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord
from aiohttp import web

from cogs.identity.helpers import unlock_context
from cogs.leveling import level_progress as server_level_progress
from cogs.progression import stats as prog_stats
from utils import cosmetics, db, globalxp
from utils import profile_card, trophy_card, wallet_card

from .. import http as H
from ..engine import economy as E

if TYPE_CHECKING:
    from ..app import Dashboard

log = logging.getLogger("NanoBot.dashboard.me")

# Card renders are Pillow work on a thread. The semaphore is the same idea the
# `/profile` command uses: a dashboard that renders eight cards at once because
# someone opened eight tabs would starve the event loop the gateway runs on.
_render_lock = asyncio.Semaphore(2)

CARD_KINDS = ("profile", "wallet", "trophies")


def routes(dash: "Dashboard") -> list:
    @H.require_member
    async def profile(request: web.Request) -> web.Response:
        """One member's whole account, assembled from one batched stat read."""
        guild: discord.Guild = H.guild_of(request)
        target = request.query.get("member")
        user_id = int(target) if target and target.isdigit() else H.user_id_of(request)
        member = guild.get_member(user_id)

        stats = await prog_stats.compute_stats(user_id)
        gxp = await db.get_global_xp(user_id)
        level, into, need = globalxp.level_progress(gxp)
        server_xp = await db.get_xp(guild.id, user_id)
        s_level, s_into, s_need = server_level_progress(server_xp)
        rank = await db.get_rank(guild.id, user_id)
        equipped = await db.get_equipped(user_id)
        unlocked = await db.get_unlocked_cosmetics(user_id)
        progression = await db.get_progression(user_id)
        social = await db.get_social(user_id)

        return H.ok(
            {
                "member": {
                    "id": str(user_id),
                    "name": member.display_name if member else f"User {user_id}",
                    "avatar": str(member.display_avatar.url) if member else None,
                    "is_me": user_id == H.user_id_of(request),
                },
                "global_level": {
                    "level": level,
                    "xp": gxp,
                    "into": into,
                    "need": need,
                    "scope": "Every server",
                },
                "server_level": {
                    "level": s_level,
                    "xp": server_xp,
                    "into": s_into,
                    "need": s_need,
                    "rank": rank[0] if rank else None,
                    "scope": guild.name,
                },
                "wallet": await E.wallet(user_id, guild.id),
                "stats": stats,
                "progression": {
                    "prestige": progression.get("prestige", 0),
                    "title": progression.get("selected_title"),
                    "achievements": len(await db.get_earned_achievements(user_id)),
                },
                "social": social,
                "cosmetics": {
                    "equipped": equipped,
                    "unlocked": sorted(unlocked),
                    "slots": {
                        key: {
                            "label": spec.label,
                            "max": spec.max_equipped,
                            "category": spec.category,
                            "description": spec.description,
                        }
                        for key, spec in cosmetics.SLOTS.items()
                    },
                },
                "cards": [
                    {
                        "kind": kind,
                        "url": f"/api/guilds/{guild.id}/me/card/{kind}?member={user_id}",
                    }
                    for kind in CARD_KINDS
                ],
            }
        )

    @H.require_member
    async def card(request: web.Request) -> web.StreamResponse:
        """Render one of the account cards as a real image.

        Every failure path degrades rather than 500s — the same contract the
        `/profile` command holds. A card that can't be drawn should leave the
        page looking incomplete, not broken.
        """
        kind = request.match_info["kind"]
        if kind not in CARD_KINDS:
            raise H.not_found("No such card.")
        guild: discord.Guild = H.guild_of(request)
        target = request.query.get("member")
        viewer_id = H.user_id_of(request)
        user_id = int(target) if target and target.isdigit() else viewer_id

        member = guild.get_member(user_id)
        viewer = guild.get_member(viewer_id)
        if member is None or viewer is None:
            raise H.not_found("That member isn't in this server.")
        if member.bot:
            raise H.bad_request("Bots don't have cards.")

        # A staged loadout from the wardrobe: render the card as it *would*
        # look, without writing anything. Only ever honoured on your own card,
        # and every key is re-checked against what you own — a preview is free,
        # wearing something is not.
        preview = None
        if user_id == viewer_id and request.query.get("loadout"):
            preview = await _preview_loadout(user_id, request.query["loadout"])

        try:
            async with _render_lock:
                image = await _render_card(dash, kind, guild, member, viewer, preview)
        except H.ApiError:
            raise
        except Exception:
            log.exception("Card render failed (%s, user %s)", kind, user_id)
            raise H.ApiError(
                503, "render_failed", "That card couldn't be drawn just now."
            ) from None

        return web.Response(
            body=image,
            content_type=f"image/{profile_card.IMAGE_EXT.lstrip('.')}",
            headers={"Cache-Control": "private, max-age=30"},
        )

    @H.require_session
    async def cosmetics_list(request: web.Request) -> web.Response:
        """The whole catalogue, marked with what this account can do about each.

        Locked entries are listed *with their unlock line*, not hidden — the
        picker doubles as the discovery UI in Discord for exactly this reason,
        and a wardrobe that only shows what you already own answers no
        questions.
        """
        user_id = H.user_id_of(request)
        unlocked = await db.get_unlocked_cosmetics(user_id)
        equipped = await db.get_equipped(user_id)
        stats = await prog_stats.compute_stats(user_id)
        progression = await db.get_progression(user_id)
        earned = await db.get_earned_achievements(user_id)
        context = unlock_context(
            global_level=globalxp.level_from_xp(await db.get_global_xp(user_id)),
            prestige=progression["prestige"],
            achievements=earned.keys(),
            stats=stats,
        )
        worn = _worn_keys(equipped)
        rows = []
        for key, spec in cosmetics.COSMETICS.items():
            owned = key in unlocked
            slot = cosmetics.SLOTS.get(spec.slot)
            rows.append(
                {
                    "key": key,
                    "name": spec.name,
                    "slot": spec.slot,
                    "slot_label": slot.label if slot else spec.slot,
                    "category": slot.category if slot else "profile",
                    "description": spec.description,
                    "price": getattr(spec, "price", 0),
                    "for_sale": (spec.unlock or {}).get("kind") == "purchase",
                    "owned": owned,
                    "worn": key in worn,
                    "unlock": cosmetics.describe_unlock(spec),
                    # "you've qualified — open your card and it's yours" is a
                    # different state from "owned" and from "locked", and it is
                    # the one worth surfacing.
                    "earned_not_claimed": not owned
                    and cosmetics.is_unlocked(spec, context),
                }
            )
        rows.sort(key=lambda r: (r["slot"], not r["owned"], r["name"]))
        return H.ok({"cosmetics": rows, "equipped": equipped})

    @H.require_session
    async def me_summary(request: web.Request) -> web.Response:
        """The signed-in account at a glance, with no server in the picture.

        This is what the home screen paints before a server has been picked, so
        it deliberately answers with only things that are true everywhere.
        """
        user_id = H.user_id_of(request)
        gxp = await db.get_global_xp(user_id)
        level, into, need = globalxp.level_progress(gxp)
        return H.ok(
            {
                "level": level,
                "xp": gxp,
                "into": into,
                "need": need,
                "balance": await db.get_balance(user_id),
                "rank": await db.get_econ_rank(user_id),
                "achievements": len(await db.get_earned_achievements(user_id)),
                "prestige": (await db.get_progression(user_id)).get("prestige", 0),
                "items": sum(r["qty"] for r in await db.get_inventory(user_id)),
            }
        )

    return [
        web.get("/api/me", me_summary),
        web.get("/api/me/cosmetics", cosmetics_list),
        web.get("/api/guilds/{guild_id}/me/profile", profile),
        web.get("/api/guilds/{guild_id}/me/card/{kind}", card),
    ]


def _worn_keys(equipped: dict) -> set[str]:
    """Flatten the equipped map, whose badge slot holds a list."""
    worn: set[str] = set()
    for value in (equipped or {}).values():
        if isinstance(value, (list, tuple, set)):
            worn.update(str(v) for v in value)
        elif value:
            worn.add(str(value))
    return worn


class _CardContext:
    """The two attributes the cogs' card builders actually read off a Context.

    Those builders (`Identity._collect`, `Economy._wallet_data`,
    `Progression._case_data`) take a `commands.Context` but touch only
    `ctx.guild` and `ctx.author` — `ctx.author` because a card evaluates and
    lazily awards cosmetics *only when you are looking at your own*. Passing a
    stand-in rather than reimplementing the assembly is what makes the image on
    the website byte-identical to the one in the chat, and it is the same
    duck-typing the repo's own view tests use where dpytest can't build the
    real object.
    """

    __slots__ = ("guild", "author")

    def __init__(self, guild, author) -> None:
        self.guild = guild
        self.author = author


async def _preview_loadout(user_id: int, raw: str) -> dict[str, list[str]]:
    """Parse `slot:key+key|slot:key` into a loadout of things this account owns.

    Anything unknown, unowned, in the wrong slot or past the slot's limit is
    dropped silently: a preview should show the closest legal thing rather than
    refuse, and the *equip* endpoint is where a bad pick is reported.
    """
    owned = set(await db.get_unlocked_cosmetics(user_id))
    out: dict[str, list[str]] = {}
    for chunk in raw.split("|")[:12]:
        slot, _, keys = chunk.partition(":")
        spec = cosmetics.SLOTS.get(slot)
        if spec is None:
            continue
        picked = []
        for key in keys.split("+"):
            item = cosmetics.get(key.strip())
            if (
                item
                and item.slot == slot
                and item.key in owned
                and item.key not in picked
            ):
                picked.append(item.key)
            if len(picked) >= spec.max_equipped:
                break
        out[slot] = picked
    return out


async def _render_card(
    dash: "Dashboard", kind: str, guild, member, viewer, preview=None
) -> bytes:
    """Build and draw one card, through the cog that owns it."""
    cog_name, builder = {
        "profile": ("Identity", "_collect"),
        "wallet": ("Economy", "_wallet_data"),
        "trophies": ("Progression", "_case_data"),
    }[kind]
    cog = dash.bot.get_cog(cog_name)
    if cog is None:
        raise H.ApiError(
            503,
            "cog_unavailable",
            f"The {cog_name} feature isn't loaded on this bot.",
        )

    ctx = _CardContext(guild, viewer)
    if kind == "profile":
        data, _notes = await cog._collect(ctx, member)
        if preview is not None:
            _apply_preview(data, preview)
        data["avatar"] = await cog._avatar_bytes(member)
        return await asyncio.to_thread(profile_card.render_card, data)
    if kind == "wallet":
        data = await cog._wallet_data(ctx, member, await cog._cfg(guild.id))
        data["avatar"] = await cog._avatar_bytes(member)
        return await asyncio.to_thread(wallet_card.render_wallet_card, data)
    data = await cog._case_data(member)
    return await asyncio.to_thread(trophy_card.render_trophy_case, data)


def _apply_preview(data: dict, loadout: dict[str, list[str]]) -> None:
    """Swap a staged loadout into the card data the cog just assembled.

    Overriding the four cosmetic keys after `_collect` rather than teaching the
    cog about previews: the cog's job is "what is this member wearing", and a
    hypothetical is the dashboard's problem, not its.
    """
    for slot in ("banner", "border", "nameplate"):
        if slot in loadout:
            keys = loadout[slot]
            data[slot] = cosmetics.get(keys[0]) if keys else None
    if "badge" in loadout:
        data["badges"] = [
            spec for spec in (cosmetics.get(k) for k in loadout["badge"]) if spec
        ]
