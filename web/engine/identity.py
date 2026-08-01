"""
web/engine/identity.py
The wardrobe: what a member owns, what they're wearing, and buying the rest.

Equipping is where the web can beat a chat client outright. `/profile equip`
already takes several cosmetics at once, but you have to know what you want
before you type it; here the whole loadout is a set of choices you can change
and *see* before anything is written. So the shape of this module is:

  `state`   — the catalogue, marked owned / worn / earnable / for sale
  `equip`   — apply a whole loadout in one call, slot by slot
  `unlock`  — buy a `purchase` cosmetic with coins

`equip` takes a full `{slot: [keys]}` map rather than one key at a time, and
writes only the slots that actually changed. That is what makes the page's
"preview then apply" honest: the preview is a loadout, and applying it is one
request that either lands or doesn't.

Ownership is re-checked here, not trusted from the client — a preview is free,
wearing something is not.
"""

from __future__ import annotations

from typing import Optional

from cogs.identity.helpers import newly_unlocked, resolve_loadout, unlock_context
from cogs.progression.helpers import prestige_title
from cogs.progression.stats import compute_stats
from utils import cosmetics, db, globalxp

from .fishing import PlayError


async def _context(user_id: int) -> dict:
    """The dict `cosmetics.is_unlocked` evaluates rules against."""
    progression = await db.get_progression(user_id)
    earned = await db.get_earned_achievements(user_id)
    return unlock_context(
        global_level=globalxp.level_from_xp(await db.get_global_xp(user_id)),
        prestige=progression["prestige"],
        achievements=earned.keys(),
        stats=await compute_stats(user_id),
    )


def _payload(spec, *, owned: set[str], worn: set[str], context: dict) -> dict:
    slot = cosmetics.SLOTS.get(spec.slot)
    kind = (spec.unlock or {}).get("kind", "manual")
    is_owned = spec.key in owned
    return {
        "key": spec.key,
        "name": spec.name,
        "slot": spec.slot,
        "slot_label": slot.label if slot else spec.slot,
        "category": slot.category if slot else "profile",
        "description": spec.description,
        "unlock": cosmetics.describe_unlock(spec),
        "unlock_kind": kind,
        "price": getattr(spec, "price", 0) or 0,
        "for_sale": kind == "purchase",
        "owned": is_owned,
        "worn": spec.key in worn,
        # Qualified but not claimed: a real third state, and the one worth
        # surfacing — opening your own card is what banks it.
        "earned_not_claimed": not is_owned and cosmetics.is_unlocked(spec, context),
        "palette": list(spec.palette or []),
        "glyph": getattr(spec, "glyph", None),
    }


async def state(user_id: int, guild_id: int) -> dict:
    """The whole wardrobe, plus everything a live card preview needs."""
    owned = set(await db.get_unlocked_cosmetics(user_id))
    equipped = await db.get_equipped(user_id)
    context = await _context(user_id)

    # Opening your own wardrobe banks what you've earned — the same lazy rule
    # opening your own card follows, so the two can't disagree about what you own.
    claimed = []
    for spec in newly_unlocked(owned, context):
        if await db.unlock_cosmetic(user_id, spec.key):
            claimed.append({"key": spec.key, "name": spec.name})
    if claimed:
        owned = set(await db.get_unlocked_cosmetics(user_id))

    worn = {key for keys in equipped.values() for key in keys}
    rows = [
        _payload(s, owned=owned, worn=worn, context=context)
        for s in cosmetics.COSMETICS.values()
    ]
    rows.sort(key=lambda r: (r["category"], r["slot"], not r["owned"], r["name"]))

    econ = await db.get_econ_config(guild_id)
    progression = await db.get_progression(user_id)
    return {
        "currency": {"name": econ["currency_name"], "emoji": econ["currency_emoji"]},
        "balance": await db.get_balance(user_id),
        "cosmetics": rows,
        "equipped": equipped,
        "loadout": resolve_loadout(equipped, owned),
        "newly_claimed": claimed,
        "slots": [
            {
                "key": key,
                "label": spec.label,
                "max": spec.max_equipped,
                "category": spec.category,
                "description": spec.description,
                "owned": sum(1 for r in rows if r["slot"] == key and r["owned"]),
                "total": sum(1 for r in rows if r["slot"] == key),
            }
            for key, spec in cosmetics.SLOTS.items()
        ],
        "categories": list(cosmetics.CATEGORIES),
        "prestige": {
            "rank": progression["prestige"],
            "title": (
                prestige_title(progression["prestige"])
                if progression["prestige"]
                else None
            ),
        },
        "title": progression["selected_title"],
    }


async def equip(user_id: int, loadout: dict) -> dict:
    """Apply a whole loadout at once.

    Only slots that actually changed are written, so re-applying a loadout is a
    no-op rather than six deletes and six inserts. Every key is re-checked
    against what the member owns and against the slot it claims to belong to —
    the client picked it, the server decides.
    """
    if not isinstance(loadout, dict):
        raise PlayError(
            "Send a loadout as an object of slot → keys.", code="bad_loadout"
        )

    owned = set(await db.get_unlocked_cosmetics(user_id))
    current = await db.get_equipped(user_id)
    changed: list[str] = []
    refused: list[dict] = []

    for slot, keys in loadout.items():
        spec = cosmetics.SLOTS.get(slot)
        if spec is None:
            raise PlayError(f"There's no “{slot}” slot.", code="bad_slot")
        if keys is None:
            keys = []
        if isinstance(keys, str):
            keys = [keys]
        if not isinstance(keys, list):
            raise PlayError(f"“{slot}” has to be a list of cosmetics.", code="bad_slot")

        wanted: list[str] = []
        for key in keys:
            item = cosmetics.get(str(key))
            if item is None:
                refused.append({"key": str(key), "why": "That cosmetic doesn't exist."})
                continue
            if item.slot != slot:
                refused.append(
                    {
                        "key": item.key,
                        "why": f"{item.name} isn't a {spec.label.lower()}.",
                    }
                )
                continue
            if item.key not in owned:
                refused.append(
                    {"key": item.key, "why": f"You don't own {item.name} yet."}
                )
                continue
            if item.key in wanted:
                continue
            if len(wanted) >= spec.max_equipped:
                refused.append(
                    {
                        "key": item.key,
                        "why": f"{spec.label} holds {spec.max_equipped} — take one off first.",
                    }
                )
                continue
            wanted.append(item.key)

        if wanted != list(current.get(slot, [])):
            await db.set_equipped(user_id, slot, wanted)
            changed.append(slot)

    return {
        "equipped": await db.get_equipped(user_id),
        "changed": changed,
        "refused": refused,
    }


async def unlock(user_id: int, guild_id: int, key: str) -> dict:
    """Buy a cosmetic from the shop.

    Charged first, then unlocked, with the coins handed straight back if the
    unlock loses a race — the order every purchase in this bot uses, because the
    alternative gives a cosmetic away to whoever double-taps fastest.
    """
    spec = cosmetics.find(str(key or ""))
    if spec is None:
        raise PlayError("No such cosmetic.", code="unknown")
    if (spec.unlock or {}).get("kind") != "purchase":
        raise PlayError(
            f"{spec.name} isn't for sale — it's earned.", code="not_for_sale"
        )
    if spec.key in await db.get_unlocked_cosmetics(user_id):
        raise PlayError(f"You already own {spec.name}.", code="owned")

    price = int(getattr(spec, "price", 0) or 0)
    if not await db.try_debit_coins(user_id, price):
        balance = await db.get_balance(user_id)
        raise PlayError(
            f"{spec.name} costs {price:,} and you have {balance:,}.", code="broke"
        )
    if not await db.unlock_cosmetic(user_id, spec.key):
        await db.add_coins(user_id, price)
        raise PlayError("You already own that.", code="owned")

    return {
        "key": spec.key,
        "name": spec.name,
        "slot": spec.slot,
        "spent": price,
        "balance": await db.get_balance(user_id),
    }


async def set_title(user_id: int, title: Optional[str]) -> dict:
    """Wear an earned title. Delegates the check to the progression engine."""
    from . import progression

    return await progression.set_title(user_id, title or "")
