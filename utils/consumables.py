"""utils/consumables.py — arming a consumable, shared by everything that sells one.

Using an item is one operation with three parts: clamp the quantity so a bulk
use can't bank a near-permanent buff, spend the stack atomically, and write the
effect for whichever cog reads it. That lived in `cogs/inventory`, which was
fine while `/inventory use` was the only door — but a member who buys bait in
the tackle shop expects to arm it there rather than leaving fishing to run
another command, and cogs never import each other, so the shared half lives
here (the `utils/` rule).

Nothing in this module is Discord-aware: it takes a user id and an item key and
hands back a result, so each caller words its own reply in its own voice. The
per-user lock is the caller's job too — `use_item` is a read-check-write, and
the cogs already hold a `KeyedLocks` entry around their multi-step flows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from utils import db
from utils import items as item_catalog

# Bulk-using an effect item multiplies the granted duration/charges by qty, so
# without a ceiling one command could bank a near-permanent buff (1000 lucky
# charms = 500 hours of luck, or a years-long rob shield). A single use of an
# item whose own duration/uses already exceeds the cap still works — the cap
# limits *stacking*, not the item.
EFFECT_MAX_DURATION = 86_400  # granted seconds per use (24h)
EFFECT_MAX_USES = 50  # granted charges per use

# What a member may arm in one go. Well above any real loadout; it exists so a
# picker with 25 rows and a fat qty can't turn into a thousand-write loop.
MAX_USE_TARGETS = 10


def usable(d: Optional[item_catalog.ItemDef]) -> bool:
    """Whether an item def does anything when used.

    Chests are usable and carry no effect (they need a key and pay coins), so
    they are named here rather than inferred — this is the same predicate the
    `/inventory use` picker filters on.
    """
    return bool(d and (d.effect or d.key == "treasure_chest"))


def clamp_qty(d: item_catalog.ItemDef, qty: int) -> int:
    """How many of `d` may be used at once, under the stacking ceiling.

    Never returns less than 1: a single item is always usable, however long it
    lasts. Only the clamped quantity is ever consumed, so nothing is eaten
    without granting its effect.
    """
    qty = max(1, int(qty or 1))
    effect = d.effect or {}
    per_duration = float(effect.get("duration", 0) or 0)
    per_uses = int(effect.get("uses", 0) or 0)
    if per_duration > 0:
        return min(qty, max(1, int(EFFECT_MAX_DURATION // per_duration)))
    if per_uses > 0:
        return min(qty, max(1, EFFECT_MAX_USES // per_uses))
    return qty


def effect_grant(d: item_catalog.ItemDef, qty: int) -> tuple[float, int]:
    """The (duration, uses) `qty` of this item grants. One of the two is zero."""
    effect = d.effect or {}
    return (
        float(effect.get("duration", 0) or 0) * qty,
        int(effect.get("uses", 0) or 0) * qty,
    )


@dataclass
class UseResult:
    """What happened to one arming attempt.

    `reason` is the whole outcome — "ok", or why not — so a caller can word a
    refusal without re-deriving it: `unknown` (no such item), `not_usable` (a
    material or a trophy), `short` (fewer owned than asked for, with `have`
    saying how many there were).
    """

    item: Optional[item_catalog.ItemDef]
    qty: int = 0
    duration: float = 0.0
    uses: int = 0
    reason: str = "ok"
    have: int = 0

    @property
    def ok(self) -> bool:
        return self.reason == "ok"

    @property
    def effect_key(self) -> str:
        return (self.item.effect or {}).get("key", "") if self.item else ""


async def use_item(user_id: int, key: str, qty: int = 1) -> UseResult:
    """Spend `qty` of an item and grant its effect. Call under the user's lock.

    The consume is the atomic `try_consume_item`, so a stack drained by another
    coroutine (or another server) is reported short rather than granting an
    effect nobody paid for.
    """
    d = item_catalog.find(key)
    if d is None:
        return UseResult(None, reason="unknown")
    if not d.effect:
        return UseResult(d, reason="not_usable")
    qty = clamp_qty(d, qty)
    if not await db.try_consume_item(user_id, d.key, qty):
        return UseResult(
            d, qty=qty, reason="short", have=await db.get_item_qty(user_id, d.key)
        )
    duration, uses = effect_grant(d, qty)
    await db.grant_effect(
        user_id,
        d.effect["key"],
        float(d.effect.get("magnitude", 0)),
        duration=duration,
        uses=uses,
    )
    return UseResult(d, qty=qty, duration=duration, uses=uses)


async def owned_usable(user_id: int) -> list[dict]:
    """Every usable stack a member owns: [{"item": def, "qty": n}, …].

    Ordered by catalogue category then name, so two different pickers built
    from it list the same gear in the same order.
    """
    rows = []
    for stack in await db.get_inventory(user_id):
        d = item_catalog.get(stack["item_key"])
        if usable(d):
            rows.append({"item": d, "qty": stack["qty"]})
    order = {cat: i for i, cat in enumerate(item_catalog.CATEGORY_ORDER)}
    rows.sort(key=lambda r: (order.get(r["item"].category, 99), r["item"].name))
    return rows
