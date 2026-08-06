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


def resolve_loadout(equipped: dict, owned, slots=None) -> dict[str, list[str]]:
    """What a card should actually draw, per slot.

    Filters the stored loadout down to cosmetics that still exist and are still
    held (a revoked one silently stops being worn), then tops up from
    DEFAULT_LOADOUT so a brand-new account never renders an empty card. Pure,
    so both the profile card and the wallet card can share one answer — pass
    `slots` to resolve only the ones a given card draws.
    """
    have = set(owned or ())
    out: dict[str, list[str]] = {}
    for slot in slots if slots is not None else cosmetics.SLOTS:
        keys = [
            k
            for k in (equipped or {}).get(slot, [])
            if cosmetics.get(k)
            and (k in have or (cosmetics.get(k).unlock or {}).get("kind") == "default")
        ]
        if not keys:
            keys = [
                k for k in cosmetics.DEFAULT_LOADOUT.get(slot, []) if cosmetics.get(k)
            ]
        out[slot] = keys
    return out


def interleave_by_slot(pairs: list[tuple[str, object]]) -> list:
    """Round-robin a (slot, row) list so every slot gets a turn.

    Discord shows 25 autocomplete rows. Sorted by slot, the badge list alone is
    longer than that, so a straight sort meant `/profile equip` never offered a
    banner once the catalogue grew — the picker silently stopped being the
    discovery UI it is supposed to be. Taking one row per slot per pass keeps
    the visible 25 spread across everything you can wear, in each slot's own
    order.
    """
    buckets: dict[str, list] = {}
    for slot, row in pairs:
        buckets.setdefault(slot, []).append(row)
    out = []
    while any(buckets.values()):
        for rows in buckets.values():
            if rows:
                out.append(rows.pop(0))
    return out


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


def announce_channel_id(
    cfg: dict, ignored: set[int] | None, in_place_id: int | None
) -> int | None:
    """Which channel a global level-up belongs in, or None to skip to DM.

    The level is account-wide but a channel isn't, so each server decides where
    a stray "level up!" is allowed to land in *its* channels:

      1. `global_xp_enabled` off → nowhere here (the whole global system is
         opted out of in this server; the member still gets a DM).
      2. `global_announce` off → nowhere here (the member still gets a DM).
      3. `/level globalannounce #channel` → that channel, always.
      4. otherwise the server's own level-up channel (`/level announce`), so a
         server that already routed level-ups doesn't have to say it twice.
      5. otherwise wherever they were talking — unless that channel earns no
         XP (`/level ignore`), which is how a venting channel opts out.

    Returns an id rather than a channel so this stays Discord-free; the caller
    resolves it and falls through to the DM when it can't (a configured channel
    that's been deleted means "not in here", never "in place instead").
    """
    if not cfg.get("global_xp_enabled", True):
        return None
    if not cfg.get("global_announce", True):
        return None
    explicit = cfg.get("global_announce_channel") or cfg.get("announce_channel")
    if explicit:
        return int(explicit)
    if in_place_id is None:
        return None
    if in_place_id in (ignored or set()):
        return None
    return int(in_place_id)


def rarity_marker(rarity: str) -> str:
    """A tiny visual tier tag for list embeds (the card carries the real art)."""
    return {
        "common": "⚪",
        "rare": "🔵",
        "epic": "🟣",
        "legendary": "🟠",
        "event": "🎏",
    }.get(rarity, "⚪")
