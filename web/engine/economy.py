"""
web/engine/economy.py
Wallet, daily, inventory and the shop — without an embed in sight.

`_cfg` here is the same merge the cog does: the guild's currency name/emoji and
its shop, with the bot-wide reward amounts folded in. The split matters and the
dashboard has to respect it, because it is the one place a member can see both
kinds of setting at once and mistake one for the other — a shop price belongs to
the server, and the size of a `/daily` does not.
"""

from __future__ import annotations

import random
import time
from typing import Optional

from cogs.economy.constants import DAILY_STREAK_CAP_DAYS, REWARD_DEFAULTS
from cogs.economy.helpers import _rank_title, compute_daily, seconds_to_afford
from cogs.inventory.helpers import chest_payout
from utils import consumables, db, globalxp
from utils import helpers as h
from utils import items as item_catalog

from .fishing import PlayError

# Same map the cog keeps: the config key a call site reads → the bot-wide
# faucet it actually comes from. Duplicated here rather than reached for through
# the cog class because a `_cfg` that quietly disagreed would mean the website
# paid a different `/daily` than Discord did.
_REWARD_KEYS = {
    "daily_amount": "daily",
    "streak_bonus": "streak_bonus",
    "coop_reward": "coop",
    "raid_reward": "raid",
}


async def _cfg(guild_id: int) -> dict:
    """The guild's economy row with the owner-set reward amounts merged in.

    The guild row holds only what affects its own members — currency name/emoji,
    raid party size, its shop. The reward *amounts* mint into a global wallet,
    so they are bot-wide and owner-set, and folding them in here is what lets
    every call site keep reading one dict.
    """
    cfg = dict(await db.get_econ_config(guild_id))
    overrides = await db.get_reward_amounts()
    for cfg_key, reward_key in _REWARD_KEYS.items():
        cfg[cfg_key] = overrides.get(reward_key, REWARD_DEFAULTS[reward_key])
    return cfg


async def wallet(user_id: int, guild_id: int) -> dict:
    """Everything the wallet card shows, plus what `/daily` would say."""
    cfg = await _cfg(guild_id)
    balance = await db.get_balance(user_id)
    last_daily, streak = await db.get_daily_state(user_id)
    contribution = await db.get_contribution(user_id)
    # Both accessors answer (position, value) — the payload wants the position.
    # Handing the pair over serialised it as a JSON array, which rendered as
    # "#0 richest" on the home card and crashed `_rank_title` outright.
    coin_res = await db.get_econ_rank(user_id)
    coin_rank = coin_res[0] if coin_res else None
    contrib_res = await db.get_contrib_rank(user_id)
    contrib_rank = contrib_res[0] if contrib_res else None
    preview = compute_daily(
        time.time(), last_daily, streak, cfg["daily_amount"], cfg["streak_bonus"]
    )
    return {
        "currency": {"name": cfg["currency_name"], "emoji": cfg["currency_emoji"]},
        "balance": balance,
        "rank": coin_rank,
        "of": await db.count_econ(),
        "contribution": {
            "points": contribution,
            "rank": contrib_rank,
            "of": await db.count_contrib(),
            "title": _rank_title(contrib_rank) if contrib_rank else None,
        },
        "streak": {
            "days": streak,
            "cap": DAILY_STREAK_CAP_DAYS,
            "capped": max(0, streak - 1) >= DAILY_STREAK_CAP_DAYS,
        },
        "daily": {
            "ready": bool(preview["ok"]),
            "ready_in": 0 if preview["ok"] else preview["retry_after"],
        },
    }


async def claim_daily(user_id: int, guild_id: int) -> dict:
    """Claim the daily — a roll, not a number.

    The band is reported alongside the coins for the reason the embed gives it a
    line of its own: a fixed payout is knowable after two claims, and what makes
    a good day feel like one is being *told* it was, not just seeing a bigger
    number.
    """
    cfg = await _cfg(guild_id)
    last_daily, streak = await db.get_daily_state(user_id)
    res = compute_daily(
        time.time(),
        last_daily,
        streak,
        cfg["daily_amount"],
        cfg["streak_bonus"],
        random.random(),
        random.random(),
    )
    if not res["ok"]:
        raise PlayError(
            f"You've already claimed today. Come back in "
            f"{h.fmt_duration(res['retry_after'])}.",
            code="cooldown",
            retry_after=res["retry_after"],
        )

    balance = await db.add_coins(user_id, res["total"])
    await db.set_daily_state(user_id, time.time(), res["streak"])
    await globalxp.award(user_id, "daily")
    band = res["band"]
    return {
        "coins": res["total"],
        "base": res["base_coins"],
        "streak_bonus": res["streak_bonus"],
        "band": {
            "key": band["key"],
            "label": band["label"],
            "emoji": band.get("emoji", "🪙"),
        },
        "streak": res["streak"],
        "capped": res["streak"] - 1 >= DAILY_STREAK_CAP_DAYS,
        "balance": balance,
    }


async def pay(user_id: int, guild_id: int, target_id: int, amount: int) -> dict:
    """Send coins to another member."""
    amount = int(amount)
    if amount <= 0:
        raise PlayError("Send at least one coin.", code="bad_amount")
    if int(target_id) == int(user_id):
        raise PlayError("You can't pay yourself.", code="bad_target")
    if not await db.transfer_coins(user_id, target_id, amount):
        raise PlayError("You don't have that many coins.", code="broke")
    return {"sent": amount, "balance": await db.get_balance(user_id)}


# ══════════════════════════════════════════════════════════════════════════════
#  Inventory
# ══════════════════════════════════════════════════════════════════════════════
# Containers: what opening one consumes besides itself. A registry rather than a
# branch, the way the item catalogue itself is one, so a second container is an
# entry instead of another special case. The *rule* it describes is the cog's —
# `Inventory._open_chests` consumes a chest and a key and pays `chest_payout`.
CONTAINERS: dict[str, dict] = {
    "treasure_chest": {
        "opens_with": "treasure_key",
        "verb": "Open",
        "pays": "coins",
    },
}


_SOURCE_CACHE: dict[int, dict[str, list[str]]] = {}


def _sources() -> dict[str, list[str]]:
    """Where each item comes from, derived from the tables that already say so.

    "How do I get more of these" is the question an inventory raises and can't
    answer, and it is the one thing worth adding that Discord's own list doesn't
    have. Derived rather than written down: the drop tables, the recipe registry
    and the shop's own price field already state it, and a hand-kept list would
    be wrong the first time a table moved.

    Deliberately nothing is invented. An item no table claims gets no line at
    all, which reads as "we're not saying" rather than as a wrong answer.

    Memoised on the *size of the catalogue* rather than outright: a cog whose
    items register after the first call would otherwise leave a permanently
    incomplete map, which is the kind of bug that only shows up in whichever
    load order production happens to use.
    """
    from cogs.activities.constants import EXPLORE_OUTCOMES, HUNT_CATCHES, ORES
    from cogs.crafting.recipes import RECIPES

    cached = _SOURCE_CACHE.get(len(item_catalog.ITEMS))
    if cached is not None:
        return cached

    out: dict[str, list[str]] = {}

    def add(item_key: str, where: str) -> None:
        entries = out.setdefault(item_key, [])
        if where not in entries:
            entries.append(where)

    for ore in ORES:
        add(ore, "Mining")
    for catch in HUNT_CATCHES:
        add(catch, "Hunting")
    for outcome, _weight in EXPLORE_OUTCOMES:
        if item_catalog.get(outcome):
            add(outcome, "Exploring")
    for recipe in RECIPES.values():
        add(recipe.output_item, "Crafting")
    for item_key, spec in item_catalog.ITEMS.items():
        if spec.price:
            add(item_key, "The tackle shop")

    _SOURCE_CACHE.clear()
    _SOURCE_CACHE[len(item_catalog.ITEMS)] = out
    return out


def item_payload(key: str, qty: int = 0) -> dict:
    spec = item_catalog.get(key)
    if not spec:
        return {
            "key": key,
            "name": key,
            "emoji": "📦",
            "category": "misc",
            "description": "",
            "value": 0,
            "qty": qty,
            "usable": False,
            "container": None,
        }
    return {
        "key": key,
        "name": spec.name,
        "emoji": spec.emoji,
        "category": spec.category,
        "description": spec.description,
        "value": spec.value,
        "price": spec.price,
        "qty": qty,
        "usable": bool(spec.effect),
        "effect": dict(spec.effect) if spec.effect else None,
        # A container is opened rather than armed, and it needs something else
        # in the bag to open with. The page has to know that *before* the tap,
        # because "you need a key" discovered by failing is the one thing a
        # screen can improve on over a command.
        "container": CONTAINERS.get(key),
        "sources": _sources().get(key, []),
    }


async def inventory(user_id: int, guild_id: int) -> dict:
    """Every stack a member owns, grouped the way the catalogue groups them."""
    cfg = await _cfg(guild_id)
    stacks = await db.get_inventory(user_id)
    rows = [item_payload(s["item_key"], s["qty"]) for s in stacks]
    order = {cat: i for i, cat in enumerate(item_catalog.CATEGORY_ORDER)}
    rows.sort(key=lambda r: (order.get(r["category"], 99), r["name"]))
    effects = await db.get_active_effects(user_id)
    now = time.time()
    return {
        "currency": {"name": cfg["currency_name"], "emoji": cfg["currency_emoji"]},
        "balance": await db.get_balance(user_id),
        "items": rows,
        "categories": [
            c
            for c in item_catalog.CATEGORY_ORDER
            if any(r["category"] == c for r in rows)
        ],
        "totals": {
            "stacks": len(rows),
            "items": sum(r["qty"] for r in rows),
            "value": sum(r["qty"] * r["value"] for r in rows),
        },
        "effects": [
            {
                "key": key,
                "magnitude": eff.get("magnitude"),
                "uses_left": eff.get("uses_left"),
                "expires_in": (
                    max(0, int(eff["expires_at"] - now))
                    if eff.get("expires_at")
                    else None
                ),
            }
            for key, eff in effects.items()
        ],
    }


async def use(user_id: int, item_key: str, qty: int = 1) -> dict:
    """Arm an item, through the same shared layer `/inventory use` goes through."""
    result = await consumables.use_item(user_id, item_key, int(qty))
    if not result.ok:
        raise PlayError(
            {
                "unknown": "You don't own that.",
                "not_usable": "That isn't something you can use.",
                "short": f"You only have {result.have}.",
            }.get(result.reason, "That didn't work."),
            code=result.reason,
        )
    return {
        "item": item_payload(item_key),
        "qty": result.qty,
        "uses": result.uses,
        "duration": result.duration,
    }


async def open_container(user_id: int, item_key: str, qty: int = 1) -> dict:
    """Open a container, consuming it and the thing it opens with.

    The order is the cog's, and it is the order that matters: take the container
    first, then the key, and **put the container back** if the key turns out not
    to be there. Both are atomic conditional consumes, so a second tab racing
    this one loses rather than opening the same chest twice — and the worst
    outcome of a crash between the two is a member keeping their chest.
    """
    spec = CONTAINERS.get(item_key)
    if spec is None:
        raise PlayError("That isn't something you can open.", code="not_container")
    key_item = spec["opens_with"]

    have = await db.get_item_qty(user_id, item_key)
    keys = await db.get_item_qty(user_id, key_item)
    qty = min(max(1, int(qty)), have, keys)
    if qty <= 0:
        need = item_payload(key_item)
        raise PlayError(
            f"Opening this takes one {need['name']} as well, and you have " f"{keys}.",
            code="no_key",
            needs=key_item,
        )

    if not await db.try_consume_item(user_id, item_key, qty):
        raise PlayError("Those just vanished — try again.", code="short")
    if not await db.try_consume_item(user_id, key_item, qty):
        await db.add_item(user_id, item_key, qty)
        raise PlayError("Your keys just vanished — try again.", code="short")

    coins = sum(chest_payout(random.random()) for _ in range(qty))
    balance = await db.add_coins(user_id, coins)
    return {
        "item": item_payload(item_key),
        "qty": qty,
        "coins": coins,
        "balance": balance,
    }


async def sell(user_id: int, item_key: str, qty: int = 1) -> dict:
    """Sell part of a stack for coins."""
    spec = item_catalog.find(item_key)
    if not spec:
        raise PlayError("No such item.", code="unknown")
    if spec.value <= 0:
        raise PlayError(f"{spec.name} can't be sold.", code="not_sellable")
    qty = max(1, int(qty))
    if not await db.try_consume_item(user_id, spec.key, qty):
        have = await db.get_item_qty(user_id, spec.key)
        raise PlayError(f"You only have {have}.", code="short")
    total = spec.value * qty
    balance = await db.add_coins(user_id, total)
    return {
        "item": item_payload(spec.key),
        "qty": qty,
        "coins": total,
        "balance": balance,
    }


async def sell_bulk(user_id: int, target: str = "all") -> dict:
    """Sell every sellable stack, or every one in a category.

    Mirrors `/inventory sell`'s bulk path including the part that matters: each
    stack is spent through the same atomic `try_consume_item`, so a stack
    drained since the page was drawn is skipped rather than paid for.
    """
    target = (target or "all").strip().lower()
    category = None
    if target not in ("all", "everything", "*"):
        category = target[4:] if target.startswith("cat:") else target
        if category not in item_catalog.CATEGORY_ORDER:
            raise PlayError(f"No category called “{target}”.", code="unknown_category")

    sold: list[dict] = []
    total = 0
    for stack in await db.get_inventory(user_id):
        spec = item_catalog.get(stack["item_key"])
        if not spec or spec.value <= 0:
            continue
        if category and spec.category != category:
            continue
        qty = stack["qty"]
        if not await db.try_consume_item(user_id, spec.key, qty):
            continue  # drained between the read and here — skip, never pay
        value = spec.value * qty
        total += value
        sold.append({**item_payload(spec.key, qty), "coins": value})

    if not sold:
        raise PlayError("Nothing there is worth selling.", code="empty")
    balance = await db.add_coins(user_id, total)
    return {"sold": sold, "coins": total, "balance": balance}


async def gift(user_id: int, target_id: int, item_key: str, qty: int = 1) -> dict:
    """Hand an item to another member, atomically."""
    if int(target_id) == int(user_id):
        raise PlayError("You already have it.", code="bad_target")
    spec = item_catalog.find(item_key)
    if not spec:
        raise PlayError("No such item.", code="unknown")
    qty = max(1, int(qty))
    if not await db.transfer_item(user_id, target_id, spec.key, qty):
        have = await db.get_item_qty(user_id, spec.key)
        raise PlayError(f"You only have {have}.", code="short")
    return {"item": item_payload(spec.key), "qty": qty}


# ══════════════════════════════════════════════════════════════════════════════
#  The server shop
# ══════════════════════════════════════════════════════════════════════════════
async def shop(user_id: int, guild_id: int) -> dict:
    """This server's rewards, priced in time as well as coins.

    The time-to-earn quote is the same `seconds_to_afford` the Discord shop
    uses, which deliberately assumes the starter spot at zero luck — "how long
    for someone who has nothing". Every upgrade makes the real answer shorter,
    which is the right direction for a floor to be wrong in.
    """
    cfg = await _cfg(guild_id)
    balance = await db.get_balance(user_id)
    items = await db.list_shop_items(guild_id)
    rows = []
    for item in items:
        rows.append(
            {
                **item,
                "affordable": balance >= item["price"],
                "seconds_to_afford": seconds_to_afford(item["price"]),
                "purchased": await db.count_user_purchases(
                    guild_id, item["id"], user_id
                ),
            }
        )
    return {
        "currency": {"name": cfg["currency_name"], "emoji": cfg["currency_emoji"]},
        "balance": balance,
        "items": rows,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Leaderboards
# ══════════════════════════════════════════════════════════════════════════════
BOARDS = {
    "coins": {
        "label": "Wealth",
        "emoji": "🪙",
        "global": db.get_econ_leaderboard,
        "scoped": db.get_econ_leaderboard_for,
        "value": "coins",
    },
    "contribution": {
        "label": "Contribution",
        "emoji": "🤝",
        "global": db.get_contrib_leaderboard,
        "scoped": db.get_contrib_leaderboard_for,
        "value": "contribution",
    },
    "fishing": {
        "label": "Fishing",
        "emoji": "🎣",
        "global": db.get_fishing_leaderboard,
        "scoped": db.get_fishing_leaderboard_for,
        "value": "earned",
    },
}


async def leaderboard(
    board: str,
    *,
    member_ids: Optional[list[int]] = None,
    limit: int = 25,
    offset: int = 0,
) -> dict:
    """One board, global or filtered to a server's members.

    A server-scoped board is the global table filtered to the guild's member
    ids — not a separate tally — which is the rule the whole economy is built
    on and the one a dashboard would be most tempted to quietly break.
    """
    spec = BOARDS.get(board)
    if not spec:
        raise PlayError("No such leaderboard.", code="unknown_board")
    key = spec["value"]
    if member_ids is None:
        rows = await spec["global"](limit=limit, offset=offset)
        total = None
    else:
        # A server-scoped board comes back unordered: the database does the
        # membership filter, the caller does the ranking. That split is the
        # accessors' own contract (`page_rows` exists for it), and the number of
        # rows is bounded by the guild's member list.
        everyone = await spec["scoped"](member_ids)
        page = offset // limit + 1
        rows, page, _pages, total = h.page_rows(
            everyone, lambda row: (-row.get(key, 0), row["user_id"]), page, per=limit
        )
    return {
        "board": board,
        "label": spec["label"],
        "emoji": spec["emoji"],
        "value_key": key,
        "total": total,
        "rows": [
            {**row, "rank": offset + i + 1, "value": row.get(key, 0)}
            for i, row in enumerate(rows)
        ],
    }
