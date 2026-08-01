"""
web/engine/crafting.py
Crafting, resolved into plain dicts.

Mirrors `Craft.craft_make` including the part that matters: inputs are consumed
one at a time through the atomic `try_consume_item`, and if a later one comes up
short — a concurrent spend in Discord, another tab — everything already consumed
is handed back. Materials must never partially vanish, and there is no
transaction wrapping this because the connection is shared (see
`_core.transaction`), so the compensation is the mechanism rather than a
fallback.

The browser gets one thing the embed can't afford: `craftable` and the exact
shortfall on *every* recipe at once, so the list can be sorted and filtered by
what you can actually make right now instead of by what exists.
"""

from __future__ import annotations

from typing import Optional

from cogs.crafting.helpers import (
    clamp_craft_qty,
    find_recipe,
    max_craftable,
    missing_inputs,
)
from cogs.crafting.recipes import RECIPES
from utils import db
from utils import items as item_catalog

from .economy import item_payload
from .fishing import PlayError


def _recipe_payload(recipe, inventory: dict[str, int]) -> dict:
    """One recipe, told against a specific member's materials.

    `max_craftable` is the number the page puts on the quantity stepper, so the
    stepper can't offer a number the craft would then refuse.
    """
    output = item_catalog.get(recipe.output_item)
    inputs = []
    for key, need in recipe.inputs.items():
        have = inventory.get(key, 0)
        inputs.append(
            {
                **item_payload(key, have),
                "need": need,
                "have": have,
                "short": max(0, need - have),
            }
        )
    # Shared with the `max` quantity /craft make offers, rather than recomputed
    # here — the number a stepper stops at and the number a command makes are
    # the same fact about the same materials.
    limit = max_craftable(recipe, inventory)

    return {
        "key": recipe.key,
        "description": recipe.description,
        "output": {
            **item_payload(recipe.output_item),
            "qty": recipe.output_qty,
            "category": output.category if output else "misc",
        },
        "inputs": inputs,
        "craftable": all(i["short"] == 0 for i in inputs),
        "max_craftable": max(0, min(limit, 100)),
        # What it's worth compared with selling the parts — the reason to craft
        # something rather than sell it, said plainly.
        "input_value": sum(
            (item_catalog.get(i["key"]).value if item_catalog.get(i["key"]) else 0)
            * i["need"]
            for i in inputs
        ),
        "output_value": (output.value if output else 0) * recipe.output_qty,
    }


async def state(user_id: int, guild_id: int) -> dict:
    """Every recipe, measured against what this member is carrying."""
    econ = await db.get_econ_config(guild_id)
    inventory = {s["item_key"]: s["qty"] for s in await db.get_inventory(user_id)}
    recipes = [_recipe_payload(r, inventory) for r in RECIPES.values()]
    recipes.sort(key=lambda r: (not r["craftable"], r["output"]["name"]))
    categories = sorted({r["output"]["category"] for r in recipes})
    return {
        "currency": {"name": econ["currency_name"], "emoji": econ["currency_emoji"]},
        "balance": await db.get_balance(user_id),
        "recipes": recipes,
        "categories": categories,
        "craftable_now": sum(1 for r in recipes if r["craftable"]),
        "max_qty": clamp_craft_qty(10_000),
    }


async def craft(user_id: int, guild_id: int, recipe_key: str, qty: int = 1) -> dict:
    """Make something, or say exactly what's missing."""
    recipe = find_recipe(recipe_key)
    if recipe is None:
        raise PlayError(f"No recipe called “{recipe_key}”.", code="unknown_recipe")

    count = clamp_craft_qty(qty)
    stacks = await db.get_inventory(user_id)
    inventory = {s["item_key"]: s["qty"] for s in stacks}
    missing = missing_inputs(recipe, inventory, count)
    if missing:
        raise PlayError(
            "You're short: "
            + ", ".join(
                f"{item_catalog.display(k)} ×{v:,}" for k, v in missing.items()
            ),
            code="short",
            missing=[{**item_payload(k), "short": v} for k, v in missing.items()],
        )

    # Consume one input at a time; refund everything already taken if a later
    # one comes up short, so a concurrent spend can't eat half a recipe.
    consumed: list[tuple[str, int]] = []
    shortfall: Optional[str] = None
    for item_key, need in recipe.inputs.items():
        total = need * count
        if await db.try_consume_item(user_id, item_key, total):
            consumed.append((item_key, total))
        else:
            shortfall = item_key
            break
    if shortfall is not None:
        for item_key, amount in consumed:
            await db.add_item(user_id, item_key, amount)
        have = await db.get_item_qty(user_id, shortfall)
        raise PlayError(
            f"Your materials changed while you were crafting — you now have "
            f"{have} × {item_catalog.display(shortfall)}. Nothing was consumed.",
            code="race",
        )

    made = recipe.output_qty * count
    await db.add_item(user_id, recipe.output_item, made)
    fresh = {s["item_key"]: s["qty"] for s in await db.get_inventory(user_id)}
    return {
        "recipe": recipe.key,
        "qty": count,
        "made": made,
        "output": {**item_payload(recipe.output_item, made), "qty": made},
        "consumed": [
            {**item_payload(key, amount), "qty": amount} for key, amount in consumed
        ],
        "recipes": [_recipe_payload(r, fresh) for r in RECIPES.values()],
    }
