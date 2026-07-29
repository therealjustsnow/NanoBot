"""utils/items.py — the shared item catalogue behind the generic inventory.

Every ownable thing that isn't a fish-in-the-bag (bait, consumables, crafting
materials, keys, treasure, event drops, …) is an *item*: a code-side
:class:`ItemDef` keyed by a stable ``item_key`` string. The database
(``utils/db/items.py``) stores only ``(guild_id, user_id, item_key, qty)``
rows, so adding a new item never touches the schema — feature packages just
register more definitions here at import time.

Registration pattern (keeps cogs decoupled — no cog imports another cog):

    from utils import items
    items.register(
        items.ItemDef(key="worm", name="Worm", emoji="🪱", category="bait", ...),
    )

Consumables carry an ``effect`` describing what /inventory use grants. Effects
are written to the ``user_effects`` table and *interpreted by whichever cog
cares* (fishing reads ``fish_luck``/``fish_xp`` effects, activities may read
``rob_shield``, …) — the inventory cog itself only stores them. An effect is
either timed (``duration`` seconds) or charge-based (``uses`` charges consumed
by the interpreting cog via db.consume_effect_use), never both.

Items with ``value > 0`` can be sold for coins via /inventory sell.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ItemDef:
    key: str  # stable id stored in the DB — never rename once shipped
    name: str
    emoji: str
    category: str  # bait / consumable / material / treasure / key / event / misc
    description: str = ""
    value: int = 0  # coin sell price (0 = can't be sold)
    price: int = 0  # coin buy price where a cog sells it (0 = not buyable)
    effect: dict = field(default_factory=dict)
    # effect keys: {"key": str, "magnitude": float, "duration": s} (timed)
    #           or {"key": str, "magnitude": float, "uses": n}     (charges)


ITEMS: dict[str, ItemDef] = {}

CATEGORY_ORDER = (
    "bait",
    "tackle",
    "consumable",
    "material",
    "treasure",
    "key",
    "event",
    "misc",
)
CATEGORY_LABELS = {
    "bait": "🪱 Bait",
    "tackle": "🎣 Tackle",
    "consumable": "🧪 Consumables",
    "material": "⛏️ Materials",
    "treasure": "💎 Treasure",
    "key": "🗝️ Keys",
    "event": "🎉 Event Items",
    "misc": "📦 Miscellaneous",
}


def register(*defs: ItemDef) -> None:
    """Add item definitions to the catalogue. Re-registering a key must be an
    identical definition (idempotent double-import), otherwise it's a bug."""
    for d in defs:
        existing = ITEMS.get(d.key)
        if existing is not None and existing != d:
            raise ValueError(f"item key {d.key!r} registered twice with different defs")
        ITEMS[d.key] = d


def get(key: str) -> ItemDef | None:
    return ITEMS.get(key)


def display(key: str) -> str:
    """Emoji + name for an item key; degrades to the raw key for unknown items
    (e.g. an item removed from the catalogue that someone still owns)."""
    d = ITEMS.get(key)
    return f"{d.emoji} {d.name}" if d else key


def find(query: str) -> ItemDef | None:
    """Look an item up by key or (case-insensitive) display name."""
    q = query.strip().lower()
    d = ITEMS.get(q)
    if d:
        return d
    for item in ITEMS.values():
        if item.name.lower() == q:
            return item
    return None


# ── Generic items owned by no single feature ───────────────────────────────────
register(
    ItemDef(
        key="treasure_key",
        name="Treasure Key",
        emoji="🗝️",
        category="key",
        description="Opens a treasure chest.",
    ),
    ItemDef(
        key="treasure_chest",
        name="Treasure Chest",
        emoji="🧰",
        category="treasure",
        description="A locked chest. Use it with a Treasure Key in your inventory.",
    ),
    ItemDef(
        key="lucky_charm",
        name="Lucky Charm",
        emoji="🍀",
        category="consumable",
        description="Boosts your luck everywhere for 30 minutes.",
        value=0,
        price=250,
        effect={"key": "luck", "magnitude": 0.5, "duration": 1800},
    ),
)
