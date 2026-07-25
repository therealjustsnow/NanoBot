"""cogs/identity/helpers.py — pure helpers for the profile/cosmetics cog.

Discord-free and side-effect-free so tests can drive them directly: building
the unlock context, deciding what a slot can hold, and formatting the small
strings the card and the embeds share.
"""

from __future__ import annotations

from utils import cosmetics


def unlock_context(
    *,
    global_level: int,
    prestige: int,
    achievements,
    stats: dict,
) -> dict:
    """The dict `cosmetics.is_unlocked` evaluates rules against.

    Built once per profile view and reused for every definition, so checking
    forty cosmetics costs no extra queries.
    """
    return {
        "global_level": int(global_level),
        "prestige": int(prestige),
        "achievements": set(achievements or ()),
        "stats": dict(stats or {}),
    }


def newly_unlocked(owned, ctx: dict) -> list[cosmetics.CosmeticDef]:
    """Earnable cosmetics whose rule the member now satisfies but doesn't own.

    Manual/event grants are never in here — those only arrive through an
    explicit grant, which is what makes them feel like awards.
    """
    have = set(owned or ())
    return [
        d
        for d in cosmetics.auto_unlockable()
        if d.key not in have and cosmetics.is_unlocked(d, ctx)
    ]


def equip_result(slot: str, equipped: list[str], key: str) -> tuple[list[str], str]:
    """Work out the new loadout for a slot when `key` is equipped.

    Single-value slots swap; multi-value slots (the badge showcase) append
    until they're full and then report which one to take off — the caller only
    has to render the message. Returns (new_list, outcome) where outcome is
    "equipped", "already", or "full".
    """
    slot_def = cosmetics.SLOTS.get(slot)
    limit = slot_def.max_equipped if slot_def else 1
    current = list(equipped or [])
    if key in current:
        return current, "already"
    if limit <= 1:
        return [key], "equipped"
    if len(current) >= limit:
        return current, "full"
    return current + [key], "equipped"


def rarity_marker(rarity: str) -> str:
    """A tiny visual tier tag for list embeds (the card carries the real art)."""
    return {
        "common": "⚪",
        "rare": "🔵",
        "epic": "🟣",
        "legendary": "🟠",
        "event": "🎏",
    }.get(rarity, "⚪")
