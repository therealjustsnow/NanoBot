"""
web/api/settings.py
One endpoint family per configurable feature.

Every feature answers the same two verbs — `GET` returns the current settings
*and the metadata to render them*, `PATCH` merges a partial update — which is
what lets the frontend have one settings-screen component rather than eleven.

The metadata is the interesting half. A `GET` doesn't just say
`{"caps": {"percent": 70}}`; it says what the field means, what values are
legal, and what the rule would *do*, so the form can be generated with plain
language and validated before anything is sent. That is the difference between
a settings page and a database editor, and it is why the automod screen can
offer a rule builder instead of a JSON field.

**Validation lives here, not in the browser.** Everything a `PATCH` accepts is
re-checked server-side: a client-side check is a courtesy to the user, never a
guarantee about the database.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Optional

import discord
from aiohttp import web

from cogs.auditlog import ALL_EVENTS, EVENT_LABELS
from cogs.automod.constants import ACTION_LABELS, RULE_LABELS
from utils import db

from .. import http as H

if TYPE_CHECKING:
    from ..app import Dashboard


# ══════════════════════════════════════════════════════════════════════════════
#  Field coercion
#
#  Small, explicit, and shared. A settings PATCH is the one place a hostile or
#  simply buggy client reaches straight into a config table, so every value it
#  carries is narrowed to a type and a range before it gets anywhere near SQL.
# ══════════════════════════════════════════════════════════════════════════════
def _bool(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false", "on", "off"):
        return value.lower() in ("true", "on")
    raise H.bad_request(f"“{field}” has to be true or false.")


def _int(value: Any, field: str, *, lo: int, hi: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise H.bad_request(f"“{field}” has to be a whole number.") from None
    if not lo <= number <= hi:
        raise H.bad_request(f"“{field}” has to be between {lo:,} and {hi:,}.")
    return number


def _text(value: Any, field: str, *, limit: int, allow_empty: bool = True) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise H.bad_request(f"“{field}” has to be text.")
    text = value.strip()
    if not text and not allow_empty:
        raise H.bad_request(f"“{field}” can't be empty.")
    if len(text) > limit:
        raise H.bad_request(f"“{field}” is too long — {limit:,} characters at most.")
    return text


def _channel(value: Any, guild: discord.Guild, field: str) -> Optional[int]:
    """Resolve a channel id, refusing one the bot can't post in.

    Saving a channel the bot can't see produces a setting that looks correct
    forever and never fires once. Refusing it here is the one moment anybody is
    looking at the screen.
    """
    if value in (None, "", 0, "0"):
        return None
    try:
        cid = int(value)
    except (TypeError, ValueError):
        raise H.bad_request(f"“{field}” has to be a channel.") from None
    # Checked against `guild.text_channels` rather than with `isinstance`,
    # because that list is exactly the set the picker offered — so a value the
    # form could produce is a value this accepts, and a voice or forum channel
    # id typed in by hand still isn't.
    channel = next((c for c in guild.text_channels if c.id == cid), None)
    if channel is None:
        raise H.bad_request("That channel isn't in this server any more.")
    me = guild.me
    perms = channel.permissions_for(me) if me else None
    if not perms or not (perms.view_channel and perms.send_messages):
        raise H.bad_request(
            f"NanoBot can't post in #{channel.name}.",
            hint="Give it View Channel and Send Messages there, or pick another "
            "channel.",
        )
    return cid


def _colour(value: Any, field: str) -> Optional[str]:
    if value in (None, ""):
        return None
    text = str(value).strip().lstrip("#")
    if len(text) != 6 or any(c not in "0123456789abcdefABCDEF" for c in text):
        raise H.bad_request(f"“{field}” has to be a colour like #5865F2.")
    return f"#{text.lower()}"


def _apply(body: dict, spec: dict[str, Callable[[Any], Any]]) -> dict:
    """Coerce only the keys actually present — a PATCH is a partial update."""
    out = {}
    for key, coerce in spec.items():
        if key in body:
            out[key] = coerce(body[key])
    return out


# ══════════════════════════════════════════════════════════════════════════════
#  Feature metadata — what the form should look like
# ══════════════════════════════════════════════════════════════════════════════
# One entry per automod rule: the plain-language description, the fields it
# takes, and an example of what would trip it. The example is the reason this
# table exists — "caps: percent 70" tells an admin nothing until they see a
# message that would be deleted.
AUTOMOD_RULES: dict[str, dict] = {
    "spam": {
        "label": RULE_LABELS["spam"],
        "summary": "Catches someone posting many messages in a few seconds.",
        "fields": [
            {
                "key": "count",
                "label": "Messages",
                "type": "int",
                "min": 2,
                "max": 30,
                "default": 5,
            },
            {
                "key": "seconds",
                "label": "Within (seconds)",
                "type": "int",
                "min": 1,
                "max": 60,
                "default": 5,
            },
        ],
        "example": "5 messages in 5 seconds",
    },
    "invites": {
        "label": RULE_LABELS["invites"],
        "summary": "Deletes Discord invite links to other servers.",
        "fields": [],
        "example": "discord.gg/example",
    },
    "links": {
        "label": RULE_LABELS["links"],
        "summary": "Deletes any external link. Strict — most servers want "
        "invites only.",
        "fields": [],
        "example": "https://example.com",
    },
    "caps": {
        "label": RULE_LABELS["caps"],
        "summary": "Catches shouting: a message that's mostly capitals.",
        "fields": [
            {
                "key": "percent",
                "label": "Capitals (%)",
                "type": "int",
                "min": 30,
                "max": 100,
                "default": 70,
            },
            {
                "key": "min_length",
                "label": "Ignore messages shorter than",
                "type": "int",
                "min": 4,
                "max": 200,
                "default": 10,
            },
        ],
        "example": "THIS IS COMPLETELY UNREASONABLE",
    },
    "mentions": {
        "label": RULE_LABELS["mentions"],
        "summary": "Catches one message pinging a lot of people at once.",
        "fields": [
            {
                "key": "limit",
                "label": "Mentions allowed",
                "type": "int",
                "min": 2,
                "max": 50,
                "default": 5,
            },
        ],
        "example": "@a @b @c @d @e @f",
    },
    "badwords": {
        "label": RULE_LABELS["badwords"],
        "summary": "Deletes messages containing words on your list.",
        "fields": [],
        "list": "badwords",
        "example": "a message containing a listed word",
    },
    "regex": {
        "label": RULE_LABELS["regex"],
        "summary": "For patterns a word list can't express. Powerful, and the "
        "easiest one to get wrong.",
        "fields": [],
        "list": "regex",
        "advanced": True,
        "example": r"\b\d{16}\b — a card-shaped number",
    },
    "attachment_word": {
        "label": RULE_LABELS["attachment_word"],
        "summary": "Only fires when a listed word arrives *with* a file — for "
        "catching a specific kind of post rather than a word.",
        "fields": [],
        "list": "attachment_words",
        "example": "“free nitro” plus an image",
    },
}

WELCOME_VARIABLES = [
    {"token": "{user}", "means": "Mentions the member", "sample": "@newcomer"},
    {"token": "{username}", "means": "Their name", "sample": "newcomer"},
    {"token": "{server}", "means": "This server's name", "sample": "Your Server"},
    {
        "token": "{membercount}",
        "means": "How many members there are now",
        "sample": "1,204",
    },
]


def routes(dash: "Dashboard") -> list:
    # ── AutoMod ──────────────────────────────────────────────────────────────
    @H.require_manager
    async def get_automod(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        cfg = await db.get_automod_config(guild.id) or {
            "enabled": False,
            "rules": {},
            "ignore_channels": [],
            "ignore_roles": [],
            "timeout_seconds": 600,
            "log_channel_id": None,
        }
        return H.ok(
            {
                "config": cfg,
                "meta": {
                    "rules": AUTOMOD_RULES,
                    "actions": [
                        {"key": key, "label": label}
                        for key, label in ACTION_LABELS.items()
                    ],
                },
                "lists": {
                    "badwords": await db.get_automod_badwords(guild.id),
                    "regex": await db.get_automod_regex_patterns(guild.id),
                    "attachment_words": await db.get_automod_attachment_words(guild.id),
                },
            }
        )

    @H.require_manager
    async def patch_automod(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        body = await H.body_json(request)
        if "enabled" in body:
            await db.set_automod_enabled(guild.id, _bool(body["enabled"], "enabled"))
        if "timeout_seconds" in body:
            await db.set_automod_timeout_seconds(
                guild.id, _int(body["timeout_seconds"], "timeout", lo=60, hi=2419200)
            )
        if "log_channel_id" in body:
            await db.set_automod_log_channel(
                guild.id, _channel(body["log_channel_id"], guild, "log channel")
            )
        for rule, patch in (body.get("rules") or {}).items():
            if rule not in AUTOMOD_RULES:
                raise H.bad_request(f"There's no “{rule}” rule.")
            await db.set_automod_rule(guild.id, rule, **_rule_patch(rule, patch))
        return H.ok({"config": await db.get_automod_config(guild.id)})

    @H.require_manager
    async def patch_automod_list(request: web.Request) -> web.Response:
        """Add to or remove from one of the three word/pattern lists."""
        guild = H.guild_of(request)
        which = request.match_info["list"]
        body = await H.body_json(request)
        value = _text(body.get("value"), "value", limit=200, allow_empty=False)
        action = body.get("action", "add")
        if action not in ("add", "remove"):
            raise H.bad_request("Action has to be add or remove.")

        table = {
            "badwords": (
                db.add_automod_badword,
                db.remove_automod_badword,
                db.get_automod_badwords,
            ),
            "regex": (
                db.add_automod_regex,
                db.remove_automod_regex,
                db.get_automod_regex_patterns,
            ),
            "attachment_words": (
                db.add_automod_attachment_word,
                db.remove_automod_attachment_word,
                db.get_automod_attachment_words,
            ),
        }.get(which)
        if not table:
            raise H.not_found("No such list.")
        add, remove, read = table
        if which == "regex" and action == "add":
            _check_regex(value)
        await (add if action == "add" else remove)(guild.id, value)
        return H.ok({"values": await read(guild.id)})

    # ── Welcome / leave ──────────────────────────────────────────────────────
    def _greeting(kind: str):
        getter = db.get_welcome_config if kind == "welcome" else db.get_leave_config
        setter = db.set_welcome_config if kind == "welcome" else db.set_leave_config
        return getter, setter

    @H.require_manager
    async def get_greeting(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        kind = request.match_info["kind"]
        if kind not in ("welcome", "leave"):
            raise H.not_found("No such message.")
        getter, _ = _greeting(kind)
        cfg = await getter(guild.id) or {
            "enabled": False,
            "channel_id": None,
            "title": None,
            "content": None,
            "image_url": None,
            "dm": False,
            "footer_text": None,
            "thumbnail": True,
            "color": None,
            "image_text": None,
        }
        return H.ok(
            {
                "config": cfg,
                "meta": {
                    "variables": WELCOME_VARIABLES,
                    "limits": {"title": 256, "content": 2048, "footer_text": 2048},
                },
            }
        )

    @H.require_manager
    async def patch_greeting(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        kind = request.match_info["kind"]
        if kind not in ("welcome", "leave"):
            raise H.not_found("No such message.")
        getter, setter = _greeting(kind)
        body = await H.body_json(request)
        patch = _apply(
            body,
            {
                "enabled": lambda v: _bool(v, "enabled"),
                "dm": lambda v: _bool(v, "DM instead"),
                "thumbnail": lambda v: _bool(v, "avatar thumbnail"),
                "channel_id": lambda v: _channel(v, guild, "channel"),
                "title": lambda v: _text(v, "title", limit=256) or None,
                "content": lambda v: _text(v, "message", limit=2048) or None,
                "footer_text": lambda v: _text(v, "footer", limit=2048) or None,
                "image_url": lambda v: _url(v, "image URL"),
                "image_text": lambda v: _text(v, "image text", limit=200) or None,
                "color": lambda v: _colour(v, "colour"),
            },
        )
        # A greeting switched on with nowhere to go is the single most common
        # "it doesn't work" report, so it's refused rather than saved.
        current = await getter(guild.id) or {}
        merged = {**current, **patch}
        if (
            merged.get("enabled")
            and not merged.get("channel_id")
            and not merged.get("dm")
        ):
            raise H.bad_request(
                "Pick a channel (or switch on “send as a DM”) before turning "
                "this on — otherwise it has nowhere to send the message."
            )
        await setter(guild.id, **merged)
        return H.ok({"config": await getter(guild.id)})

    # ── Logging ──────────────────────────────────────────────────────────────
    @H.require_manager
    async def get_logging(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        cfg = await db.get_auditlog_config(guild.id) or {
            "enabled": False,
            "channel_id": None,
            "events": list(ALL_EVENTS),
        }
        return H.ok(
            {
                "config": cfg,
                "meta": {"groups": _LOG_GROUPS, "labels": EVENT_LABELS},
            }
        )

    @H.require_manager
    async def patch_logging(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        body = await H.body_json(request)
        if "channel_id" in body:
            cid = _channel(body["channel_id"], guild, "log channel")
            if cid is None:
                raise H.bad_request("Logging needs a channel to write to.")
            await db.set_auditlog_channel(guild.id, cid)
        if "events" in body:
            events = body["events"]
            if not isinstance(events, list):
                raise H.bad_request("Events has to be a list.")
            unknown = [e for e in events if e not in ALL_EVENTS]
            if unknown:
                raise H.bad_request(f"Unknown event: {unknown[0]}")
            await db.set_auditlog_events(guild.id, set(events))
        if "enabled" in body:
            enabled = _bool(body["enabled"], "enabled")
            cfg = await db.get_auditlog_config(guild.id)
            if enabled and not (cfg and cfg.get("channel_id")):
                raise H.bad_request("Pick a log channel first.")
            await db.set_auditlog_enabled(guild.id, enabled)
        return H.ok({"config": await db.get_auditlog_config(guild.id)})

    # ── Leveling ─────────────────────────────────────────────────────────────
    @H.require_manager
    async def get_leveling(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        cfg = await db.get_level_config(guild.id)
        rewards = await db.get_level_rewards(guild.id)
        return H.ok(
            {
                "config": cfg,
                "rewards": [
                    {"level": level, "role_id": str(role_id)}
                    for level, role_id in sorted(rewards.items())
                ],
                "ignored": [
                    str(c) for c in await db.get_level_ignored_channels(guild.id)
                ],
                "meta": {
                    "note": "This is the *server* level, earned from messages "
                    "here. It is separate from the account-wide level a member "
                    "carries between servers — that one isn't configurable, by "
                    "design.",
                },
            }
        )

    @H.require_manager
    async def patch_leveling(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        body = await H.body_json(request)
        patch = _apply(
            body,
            {
                "enabled": lambda v: _bool(v, "enabled"),
                "announce": lambda v: _bool(v, "announce level-ups"),
                "global_announce": lambda v: _bool(v, "announce global level-ups"),
                "xp_min": lambda v: _int(v, "minimum XP", lo=1, hi=500),
                "xp_max": lambda v: _int(v, "maximum XP", lo=1, hi=500),
                "cooldown": lambda v: _int(v, "XP cooldown", lo=0, hi=3600),
                "announce_channel": lambda v: _channel(v, guild, "announce channel"),
                "global_announce_channel": lambda v: _channel(
                    v, guild, "global announce channel"
                ),
            },
        )
        current = await db.get_level_config(guild.id)
        merged = {**current, **patch}
        if merged["xp_min"] > merged["xp_max"]:
            raise H.bad_request("Minimum XP can't be more than the maximum.")
        await db.set_level_config(guild.id, **patch)
        return H.ok({"config": await db.get_level_config(guild.id)})

    @H.require_manager
    async def patch_level_reward(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        body = await H.body_json(request)
        level = _int(body.get("level"), "level", lo=1, hi=1000)
        if body.get("action") == "remove":
            await db.remove_level_reward(guild.id, level)
        else:
            role = _role(body.get("role_id"), guild, assignable=True)
            await db.add_level_reward(guild.id, level, role.id)
        rewards = await db.get_level_rewards(guild.id)
        return H.ok(
            {
                "rewards": [
                    {"level": lv, "role_id": str(rid)}
                    for lv, rid in sorted(rewards.items())
                ]
            }
        )

    # ── Economy, fishing, activities, casino ─────────────────────────────────
    @H.require_manager
    async def get_economy(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        econ = await db.get_econ_config(guild.id)
        rewards = await db.get_reward_amounts()
        return H.ok(
            {
                "config": econ,
                "shop": await db.list_shop_items(guild.id),
                "pending": await db.list_pending_purchases(guild.id),
                # Read-only and labelled as such. These mint into a global
                # wallet, so they belong to the bot owner — showing them without
                # explaining that is how an admin concludes the dashboard is
                # broken when the field won't save.
                "bot_wide": {
                    "rewards": rewards,
                    "cooldowns": await db.get_activity_cooldowns(),
                    "editable": False,
                    "why": "These pay into an account-wide wallet that follows "
                    "members between servers, so only the bot owner can change "
                    "them (with !econ and !cooldown). Your shop's prices are "
                    "yours — a purchase only ever spends coins here.",
                },
            }
        )

    @H.require_manager
    async def patch_economy(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        body = await H.body_json(request)
        patch = _apply(
            body,
            {
                "currency_name": lambda v: _text(
                    v, "currency name", limit=32, allow_empty=False
                ),
                "currency_emoji": lambda v: _text(
                    v, "currency emoji", limit=32, allow_empty=False
                ),
                "raid_min": lambda v: _int(v, "smallest raid party", lo=2, hi=25),
                "raid_max": lambda v: _int(v, "largest raid party", lo=2, hi=25),
            },
        )
        merged = {**await db.get_econ_config(guild.id), **patch}
        if merged["raid_min"] > merged["raid_max"]:
            raise H.bad_request(
                "The smallest raid party can't be bigger than the largest."
            )
        await db.set_econ_config(guild.id, **patch)
        return H.ok({"config": await db.get_econ_config(guild.id)})

    @H.require_manager
    async def get_games(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        return H.ok(
            {
                "fishing": await db.get_fishing_config(guild.id),
                "activities": await db.get_activities_config(guild.id),
                "casino": await db.get_casino_config(guild.id),
                "cooldowns": await db.get_activity_cooldowns(),
            }
        )

    @H.require_manager
    async def patch_games(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        body = await H.body_json(request)
        if "fishing" in body:
            patch = _apply(
                body["fishing"], {"enabled": lambda v: _bool(v, "fishing enabled")}
            )
            if patch:
                await db.set_fishing_config(guild.id, **patch)
        if "activities" in body:
            patch = _apply(
                body["activities"],
                {
                    f"{a}_enabled": (lambda a: lambda v: _bool(v, f"{a} enabled"))(a)
                    for a in ("work", "mine", "hunt", "explore", "rob")
                },
            )
            if patch:
                await db.set_activities_config(guild.id, **patch)
        if "casino" in body:
            patch = _apply(
                body["casino"],
                {
                    "enabled": lambda v: _bool(v, "casino enabled"),
                    "min_bet": lambda v: _int(v, "minimum bet", lo=1, hi=1_000_000),
                    "max_bet": lambda v: _int(v, "maximum bet", lo=1, hi=10_000_000),
                },
            )
            merged = {**await db.get_casino_config(guild.id), **patch}
            if merged["min_bet"] > merged["max_bet"]:
                raise H.bad_request("The minimum bet can't be more than the maximum.")
            if patch:
                await db.set_casino_config(guild.id, **patch)
        return await get_games(request)

    # ── The server shop ──────────────────────────────────────────────────────
    @H.require_manager
    async def post_shop_item(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        body = await H.body_json(request)
        name = _text(body.get("name"), "name", limit=100, allow_empty=False)
        price = _int(body.get("price"), "price", lo=1, hi=1_000_000_000)
        kind = body.get("kind", "custom")
        if kind not in ("role", "custom"):
            raise H.bad_request("An item is either a role or a custom reward.")
        role_id = None
        if kind == "role":
            role_id = _role(body.get("role_id"), guild, assignable=True).id
        # The sentinels are the accessor's own: -1 stock means unlimited, and 0
        # means no limit / no cooldown. Passing None instead would write a NULL
        # into a NOT NULL column.
        item_id = await db.add_shop_item(
            guild.id,
            name=name,
            price=price,
            kind=kind,
            role_id=role_id,
            description=_text(body.get("description"), "description", limit=500),
            stock=(
                _int(body["stock"], "stock", lo=0, hi=1_000_000)
                if body.get("stock") not in (None, "")
                else -1
            ),
            per_user_limit=(
                _int(body["per_user_limit"], "per-member limit", lo=1, hi=1000)
                if body.get("per_user_limit") not in (None, "")
                else 0
            ),
            cooldown=(
                _int(body["cooldown"], "cooldown", lo=0, hi=31_536_000)
                if body.get("cooldown") not in (None, "")
                else 0
            ),
        )
        if item_id is None:
            raise H.conflict(f"There's already an item called “{name}”.")
        return H.ok({"shop": await db.list_shop_items(guild.id)})

    @H.require_manager
    async def delete_shop_item(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        item_id = _int(request.match_info["item_id"], "item", lo=1, hi=2**31)
        if not await db.remove_shop_item(guild.id, item_id):
            raise H.not_found("That item isn't in the shop.")
        return H.ok({"shop": await db.list_shop_items(guild.id)})

    @H.require_manager
    async def fulfill_purchase(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        purchase_id = _int(
            request.match_info["purchase_id"], "purchase", lo=1, hi=2**31
        )
        if not await db.fulfill_purchase(guild.id, purchase_id):
            raise H.conflict("That reward was already handed over.")
        return H.ok({"pending": await db.list_pending_purchases(guild.id)})

    return [
        web.get("/api/guilds/{guild_id}/settings/automod", get_automod),
        web.patch("/api/guilds/{guild_id}/settings/automod", patch_automod),
        web.patch(
            "/api/guilds/{guild_id}/settings/automod/lists/{list}", patch_automod_list
        ),
        web.get("/api/guilds/{guild_id}/settings/greeting/{kind}", get_greeting),
        web.patch("/api/guilds/{guild_id}/settings/greeting/{kind}", patch_greeting),
        web.get("/api/guilds/{guild_id}/settings/logging", get_logging),
        web.patch("/api/guilds/{guild_id}/settings/logging", patch_logging),
        web.get("/api/guilds/{guild_id}/settings/leveling", get_leveling),
        web.patch("/api/guilds/{guild_id}/settings/leveling", patch_leveling),
        web.patch(
            "/api/guilds/{guild_id}/settings/leveling/rewards", patch_level_reward
        ),
        web.get("/api/guilds/{guild_id}/settings/economy", get_economy),
        web.patch("/api/guilds/{guild_id}/settings/economy", patch_economy),
        web.get("/api/guilds/{guild_id}/settings/games", get_games),
        web.patch("/api/guilds/{guild_id}/settings/games", patch_games),
        web.post("/api/guilds/{guild_id}/shop/items", post_shop_item),
        web.delete("/api/guilds/{guild_id}/shop/items/{item_id}", delete_shop_item),
        web.post("/api/guilds/{guild_id}/shop/pending/{purchase_id}", fulfill_purchase),
    ]


# ── helpers used by more than one handler ─────────────────────────────────────
_LOG_GROUPS = [
    {
        "key": "messages",
        "label": "Messages",
        "blurb": "Edits and deletions. The busiest category by far — switch it "
        "off first if your log channel is unreadable.",
        "events": ["msg_delete", "msg_edit"],
    },
    {
        "key": "members",
        "label": "Members",
        "blurb": "Joins, leaves, nicknames and role changes.",
        "events": ["member_join", "member_leave", "nick_change", "role_update"],
    },
    {
        "key": "moderation",
        "label": "Moderation",
        "blurb": "Bans, unbans, and anything AutoMod acted on.",
        "events": ["member_ban", "member_unban", "automod_action"],
    },
    {
        "key": "server",
        "label": "Server structure",
        "blurb": "Channels and roles being created or deleted. Quiet, and the "
        "most useful category when something goes wrong.",
        "events": ["channel_create", "channel_delete", "role_create", "role_delete"],
    },
]


def _rule_patch(rule: str, patch: Any) -> dict:
    """Narrow one automod rule's fields to their declared types and ranges."""
    if not isinstance(patch, dict):
        raise H.bad_request(f"“{rule}” has to be an object.")
    spec = AUTOMOD_RULES[rule]
    out: dict[str, Any] = {}
    if "enabled" in patch:
        out["enabled"] = _bool(patch["enabled"], f"{rule} enabled")
    if "action" in patch:
        action = patch["action"]
        if action not in ACTION_LABELS:
            raise H.bad_request(f"“{action}” isn't one of the actions.")
        out["action"] = action
    if "dm_message" in patch:
        out["dm_message"] = _text(patch["dm_message"], "DM message", limit=500) or None
    for field in spec["fields"]:
        key = field["key"]
        if key in patch:
            out[key] = _int(
                patch[key], field["label"], lo=field["min"], hi=field["max"]
            )
    return out


def _check_regex(pattern: str) -> None:
    """Refuse a pattern that won't compile, or that looks like a ReDoS.

    The bot already runs user regex behind a timeout and a length cap; this is
    the earlier, friendlier half — telling someone their pattern is broken while
    they're looking at it, rather than letting it fail silently on live traffic.
    """
    import re

    from cogs.automod.constants import _REDOS_RE

    try:
        re.compile(pattern)
    except re.error as exc:
        raise H.bad_request(f"That pattern isn't valid: {exc}") from None
    if _REDOS_RE.search(pattern):
        raise H.bad_request(
            "That pattern has a nested repetition that can hang the matcher on "
            "the right input. Rewrite it without a quantifier inside a "
            "quantified group.",
            hint="catastrophic-backtracking",
        )


def _role(value: Any, guild: discord.Guild, *, assignable: bool = False):
    """Resolve a role id, refusing one the bot couldn't hand out."""
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
            raise H.bad_request(
                "NanoBot needs the Manage Roles permission to hand out roles."
            )
        if role.managed or role >= me.top_role:
            raise H.bad_request(
                f"NanoBot can't assign @{role.name} — it sits at or above "
                "NanoBot's own highest role.",
                hint="Drag NanoBot's role above it in Server Settings → Roles.",
            )
    return role


def _url(value: Any, field: str) -> Optional[str]:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text.startswith(("http://", "https://")):
        raise H.bad_request(f"“{field}” has to start with http:// or https://.")
    if len(text) > 2000:
        raise H.bad_request(f"“{field}” is too long.")
    return text
