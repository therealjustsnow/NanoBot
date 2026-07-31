"""
web/engine/fishing.py
Fishing, resolved into plain dicts.

Mirrors `Fishing._resolve_cast` step for step — the same claim, the same luck
stack, the same rolls through the same helpers — and returns structured data
instead of an embed, so the browser can animate a catch rather than re-render a
Discord message.

The one thing the web deliberately does *not* do is start random fishing events.
Events are guild-wide and get announced in a channel; a cast from a browser has
no channel to announce into, and a faucet that turns itself on where nobody can
see it is worse than one that only ever starts in chat. Web casts still *honour*
every running event.
"""

from __future__ import annotations

import random
import time
from typing import Any, Optional

from cogs.fishing.constants import (
    ARMABLE_EFFECTS,
    CAST_COOLDOWN,
    EVENT_LABELS,
    FISH,
    MAX_LUCK,
    NET_CATCHES,
    RARITIES,
    RODS,
    TRAP_CATCHES,
    TRAP_SOAK,
    XP_PER_RARITY,
)
from cogs.fishing.helpers import (
    catch_value,
    find_fish,
    fish_level,
    fmt_weight,
    generate_quest,
    level_luck_bonus,
    level_progress,
    next_rod,
    next_streak,
    quest_label,
    quest_reward,
    rarity_odds,
    rod_info,
    roll_weight,
    streak_bonus_coins,
    treasure_coins,
)
from cogs.fishing.items import FISH_TRAP
from cogs.fishing.spots import (
    SPOT_ORDER,
    exclusive_keys,
    find_spot,
    pick_spot_fish,
    pick_spot_rarity,
    resolve_spot,
    roll_hazard,
    spot_info,
    unlock_state,
)
from utils import consumables, db, globalxp
from utils import helpers as h
from utils import items as item_catalog

# The bait shop's shelf, in the order the tackle shop lists it.
SHOP_KEYS = (
    "bait_worm",
    "bait_shrimp",
    "bait_glowgrub",
    "bait_magnet",
    "fish_xp_potion",
    "fish_net",
    FISH_TRAP,
)


class PlayError(Exception):
    """A refusal a member should read: on cooldown, broke, nothing to sell.

    Distinct from a bug. Everything raised with this is copy the frontend shows
    verbatim, so it is written as a sentence rather than a status code.
    """

    def __init__(self, message: str, *, code: str = "refused", **extra: Any) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.extra = extra


def _fish(key: str) -> dict:
    return FISH.get(key, {"name": key, "emoji": "🐟", "rarity": "common"})


def rarity_payload(rarity: str) -> dict:
    """A rarity's label and colour, from the cog's own table.

    `RARITIES` stores `(label, colour)` where the label carries a coloured
    circle emoji. The web splits the two apart so a rarity can be a *border* and
    a tint rather than a glyph glued to the front of a word — a card can show
    what an embed could only spell out.
    """
    label, colour = RARITIES.get(rarity, ("Common", 0x99AAB5))
    text = label.split(" ", 1)[-1] if " " in label else label
    return {"key": rarity, "label": text, "color": f"#{colour:06x}"}


def fish_payload(key: str) -> dict:
    """One species, as the browser wants it."""
    spec = _fish(key)
    rarity = spec.get("rarity", "common")
    meta = rarity_payload(rarity)
    return {
        "key": key,
        "name": spec.get("name", key),
        "emoji": spec.get("emoji", "🐟"),
        "rarity": rarity,
        "rarity_label": meta["label"],
        "rarity_color": meta["color"],
        "value": spec.get("value", 0),
        "spot": spec.get("spot"),
    }


async def _luck(user_id: int, guild_id: int, fisher: dict) -> dict:
    """Everything that shifts the drop table, and where each part came from.

    Returned as a breakdown rather than one number because "why did I catch
    that?" is the question the Discord embed can't afford the space to answer
    and a web page can — the cast screen shows the stack itemised.
    """
    effects = await db.get_active_effects(user_id)
    events = await db.get_active_events(guild_id)
    level = fish_level(fisher["xp"])

    rod_luck = rod_info(fisher["rod_level"])["luck"]
    level_bonus = level_luck_bonus(level)
    generic = effects.get("luck", {}).get("magnitude", 0.0)
    event_luck = 0.0
    xp_event_mult = 1.0
    value_mult = h.coin_boost_multiplier(effects)
    for ev in events:
        if ev["event_key"] == "lucky_waters":
            event_luck = max(event_luck, ev["magnitude"])
        elif ev["event_key"] == "frenzy":
            value_mult = max(value_mult, float(ev["magnitude"]))
        elif ev["event_key"] == "double_xp":
            xp_event_mult = max(xp_event_mult, ev["magnitude"])

    return {
        "effects": effects,
        "events": events,
        "rod": rod_luck,
        "level_bonus": level_bonus,
        "generic": generic,
        "event": event_luck,
        "xp_effect_mult": effects.get("fish_xp", {}).get("magnitude") or 1.0,
        "xp_event_mult": xp_event_mult,
        "value_mult": value_mult,
    }


def _total_luck(parts: dict, bait: float = 0.0) -> float:
    return max(
        0.0,
        min(
            MAX_LUCK,
            parts["rod"]
            + parts["level_bonus"]
            + parts["generic"]
            + bait
            + parts["event"],
        ),
    )


# ══════════════════════════════════════════════════════════════════════════════
#  Casting
# ══════════════════════════════════════════════════════════════════════════════
async def cast(user_id: int, guild_id: int) -> dict:
    """Claim and resolve one cast.

    The claim comes first and everything else is downstream of it, exactly as in
    the cog: a refusal must cost nothing, and a cast that got as far as rolling
    a fish must already own the cooldown it spent.
    """
    cfg = await db.get_fishing_config(guild_id)
    if not cfg["enabled"]:
        raise PlayError(
            "Fishing is switched off in this server.",
            code="disabled",
        )

    now = time.time()
    retry = await db.try_claim_cast(user_id, now, CAST_COOLDOWN)
    if retry:
        raise PlayError(
            f"Your line is still in the water — {h.fmt_duration(retry)} to go.",
            code="cooldown",
            retry_after=retry,
        )

    fisher = await db.get_fisher(user_id)
    spot = resolve_spot(fisher["spot"])
    info = spot_info(spot)
    today = int(now // 86400)
    notes: list[dict] = []

    # First cast of a new UTC day: the login streak and its bonus.
    if fisher["last_day"] != today:
        streak = next_streak(fisher["last_day"], today, fisher["streak_days"])
        if await db.try_claim_daily_streak(user_id, today, streak):
            bonus = streak_bonus_coins(streak)
            if bonus:
                await db.add_coins(user_id, bonus)
                await db.add_fishing_earned(user_id, bonus)
            notes.append(
                {
                    "kind": "streak",
                    "streak": streak,
                    "coins": bonus,
                    "text": f"{streak}-day streak!",
                }
            )

    parts = await _luck(user_id, guild_id, fisher)
    effects = parts["effects"]

    # Bait is spent the moment the line goes in — before the hazard roll, and
    # deliberately so: a snag costs this one charge and never the rest.
    bait_luck = 0.0
    if "fish_bait" in effects and await db.consume_effect_use(user_id, "fish_bait"):
        bait_luck = effects["fish_bait"]["magnitude"]

    catches = 1
    if "fish_net" in effects and await db.consume_effect_use(user_id, "fish_net"):
        catches = max(1, int(effects["fish_net"]["magnitude"] or NET_CATCHES))

    luck = _total_luck(parts, bait_luck)
    await globalxp.award(user_id, "fish_cast")

    old_xp = fisher["xp"]
    qgen = generate_quest(user_id, today)
    quest = await db.get_or_create_quest(
        user_id, today, qgen["quest_key"], qgen["target"]
    )

    # ── The snag ─────────────────────────────────────────────────────────────
    if roll_hazard(random.random(), spot):
        xp_gain = max(1, round(XP_PER_RARITY["common"] * parts["xp_event_mult"]))
        new_xp = await db.add_fishing_xp(user_id, xp_gain)
        return {
            "outcome": "snag",
            "spot": _spot_payload(spot),
            "catches": [],
            "coins": 0,
            "xp": xp_gain,
            "bait_spent": bool(bait_luck),
            "luck": _luck_payload(parts, bait_luck, luck),
            "notes": notes,
            "level": _level_payload(old_xp, new_xp),
            "bag_count": await db.count_bag(user_id),
            "balance": await db.get_balance(user_id),
            "cooldown": CAST_COOLDOWN,
        }

    # ── The haul ─────────────────────────────────────────────────────────────
    hauled: list[dict] = []
    treasure_total = 0
    xp_total = 0
    for _ in range(catches):
        rarity = pick_spot_rarity(random.random(), rarity_odds(luck), spot)
        key = pick_spot_fish(rarity, random.random(), spot)
        xp_total += max(
            1,
            round(
                XP_PER_RARITY[rarity] * parts["xp_effect_mult"] * parts["xp_event_mult"]
            ),
        )
        if rarity == "treasure":
            coins = max(
                1, int(treasure_coins(key, random.random()) * parts["value_mult"])
            )
            treasure_total += coins
            hauled.append({**fish_payload(key), "coins": coins, "treasure": True})
            continue
        weight = roll_weight(key, random.random())
        value = max(1, int(catch_value(key, weight) * parts["value_mult"]))
        await db.record_catch(user_id, key, weight, value, track_best=rarity != "junk")
        hauled.append(
            {
                **fish_payload(key),
                "weight": weight,
                "weight_label": fmt_weight(weight),
                "value": value,
                "treasure": False,
                "personal_best": weight > fisher["best_weight"] and rarity != "junk",
            }
        )

    if treasure_total:
        await db.add_coins(user_id, treasure_total)
        await db.add_fishing_earned(user_id, treasure_total)
        quest = await _bump_quest(
            user_id, quest, today, kind="earn", coins=treasure_total
        )
    for entry in hauled:
        if not entry["treasure"]:
            quest = await _bump_quest(
                user_id, quest, today, kind="catch", rarity=entry["rarity"]
            )

    new_xp = await db.add_fishing_xp(user_id, xp_total)
    quest_note = await _award_quest_if_complete(user_id, today, quest)
    if quest_note:
        notes.append(quest_note)

    return {
        "outcome": "catch",
        "spot": _spot_payload(spot),
        "catches": hauled,
        "coins": treasure_total,
        "xp": xp_total,
        "bait_spent": bool(bait_luck),
        "net": catches > 1,
        "luck": _luck_payload(parts, bait_luck, luck),
        "notes": notes,
        "level": _level_payload(old_xp, new_xp),
        "bag_count": await db.count_bag(user_id),
        "balance": await db.get_balance(user_id),
        "cooldown": CAST_COOLDOWN,
        "events": [
            {
                "key": e["event_key"],
                "label": EVENT_LABELS.get(e["event_key"], e["event_key"]),
            }
            for e in parts["events"]
        ],
        "spot_name": info["name"],
    }


def _luck_payload(parts: dict, bait: float, total: float) -> dict:
    return {
        "total": round(total, 4),
        "max": MAX_LUCK,
        "parts": [
            {"label": "Rod", "value": round(parts["rod"], 4)},
            {"label": "Fishing level", "value": round(parts["level_bonus"], 4)},
            {"label": "Bait", "value": round(bait, 4)},
            {"label": "Charms", "value": round(parts["generic"], 4)},
            {"label": "Events", "value": round(parts["event"], 4)},
        ],
    }


def _level_payload(old_xp: int, new_xp: int) -> dict:
    before, after = fish_level(old_xp), fish_level(new_xp)
    into, need, _ = level_progress(new_xp)
    return {
        "level": after,
        "levelled_up": after > before,
        "gained": new_xp - old_xp,
        "xp": new_xp,
        "into": into,
        "need": need,
        "luck_bonus": round(level_luck_bonus(after), 4),
    }


async def _bump_quest(
    user_id: int,
    quest: dict,
    today: int,
    *,
    kind: str,
    rarity: Optional[str] = None,
    coins: int = 0,
) -> dict:
    """Advance today's quest when this event is the kind it counts."""
    if quest["claimed"]:
        return quest
    key = quest["quest_key"]
    amount = 0
    if kind == "catch" and key == "catch_any":
        amount = 1
    elif (
        kind == "catch"
        and key.startswith("catch_rarity:")
        and key.split(":", 1)[1] == rarity
    ):
        amount = 1
    elif kind == "earn" and key == "earn_coins":
        amount = coins
    if amount <= 0:
        return quest
    progress = await db.bump_quest_progress(user_id, today, amount)
    return {**quest, "progress": progress}


async def _award_quest_if_complete(
    user_id: int, today: int, quest: dict
) -> Optional[dict]:
    """Pay out a just-finished quest, exactly once."""
    if quest["claimed"] or quest["progress"] < quest["target"]:
        return None
    if not await db.try_claim_quest_reward(user_id, today):
        return None
    coins, xp = quest_reward(quest["quest_key"], quest["target"])
    if coins:
        await db.add_coins(user_id, coins)
        await db.add_fishing_earned(user_id, coins)
    if xp:
        await db.add_fishing_xp(user_id, xp)
    await globalxp.award(user_id, "quest")
    return {
        "kind": "quest",
        "coins": coins,
        "xp": xp,
        "text": "Daily quest complete!",
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Selling, travelling, upgrading, buying, trapping
# ══════════════════════════════════════════════════════════════════════════════
async def sell(user_id: int, guild_id: int, target: str = "all") -> dict:
    """Sell one species out of the bag, or the whole bag."""
    key: Optional[str] = None
    if target and target not in ("all", "*", "everything"):
        key = find_fish(target)
        if not key:
            raise PlayError(f"No species called “{target}”.", code="unknown_fish")

    count, total = await db.sell_catches(user_id, key)
    if not count:
        raise PlayError(
            (
                "Nothing in your bag to sell."
                if key is None
                else "You have none of those."
            ),
            code="empty",
        )
    await db.add_coins(user_id, total)
    await db.add_fishing_earned(user_id, total)
    return {
        "sold": count,
        "coins": total,
        "species": fish_payload(key) if key else None,
        "balance": await db.get_balance(user_id),
        "bag_count": await db.count_bag(user_id),
    }


async def travel(user_id: int, guild_id: int, spot_query: str) -> dict:
    """Move to a spot, chartering it first if this is the first visit.

    The charter is debited before the unlock is written and handed back if the
    unlock loses a race — the same order `/fish travel` uses, because the
    alternative gives away a spot to whoever double-taps fastest.
    """
    key = find_spot(spot_query)
    if not key:
        raise PlayError(f"No fishing spot called “{spot_query}”.", code="unknown_spot")

    fisher = await db.get_fisher(user_id)
    if resolve_spot(fisher["spot"]) == key:
        raise PlayError("You're already fishing there.", code="already_there")

    unlocked = await db.get_unlocked_spots(user_id)
    balance = await db.get_balance(user_id)
    level = fish_level(fisher["xp"])
    gate = unlock_state(key, level, unlocked, balance)
    if gate["state"] in ("locked", "poor"):
        # The level gate is reported before the price on purpose — being told
        # what something costs when you couldn't buy it anyway is the wrong
        # answer, and `unlock_state` already orders the two that way.
        raise PlayError(gate["reason"], code=gate["state"])

    charged = 0
    if gate["state"] == "buyable":
        price = int(spot_info(key)["price"])
        if not await db.try_debit_coins(user_id, price):
            raise PlayError("You can't afford that charter.", code="poor")
        if not await db.unlock_spot(user_id, key, time.time()):
            await db.add_coins(user_id, price)  # someone else's tap landed first
        else:
            charged = price

    await db.set_spot(user_id, key)
    return {
        "spot": _spot_payload(key),
        "charged": charged,
        "balance": await db.get_balance(user_id),
    }


async def upgrade_rod(user_id: int, guild_id: int) -> dict:
    """Buy the next rod tier."""
    fisher = await db.get_fisher(user_id)
    nxt = next_rod(fisher["rod_level"])
    if not nxt:
        raise PlayError("You already have the best rod there is.", code="maxed")
    price = int(nxt["price"])
    if not await db.try_debit_coins(user_id, price):
        raise PlayError(
            f"That rod costs {price:,} and you don't have it yet.", code="broke"
        )
    if not await db.set_rod_level(
        user_id, fisher["rod_level"] + 1, expected=fisher["rod_level"]
    ):
        await db.add_coins(user_id, price)
        raise PlayError(
            "Your rod changed while you were buying. Try again.", code="race"
        )
    return {
        "rod": _rod_payload(fisher["rod_level"] + 1),
        "spent": price,
        "balance": await db.get_balance(user_id),
    }


async def buy(user_id: int, guild_id: int, item_key: str, qty: int = 1) -> dict:
    """Buy tackle off the shelf."""
    if item_key not in SHOP_KEYS:
        raise PlayError("That isn't sold at the tackle shop.", code="unknown_item")
    spec = item_catalog.find(item_key)
    if not spec or not spec.price:
        raise PlayError("That isn't for sale.", code="unknown_item")
    qty = max(1, min(int(qty), 100))
    total = spec.price * qty
    if not await db.try_debit_coins(user_id, total):
        raise PlayError(f"That comes to {total:,} — more than you have.", code="broke")
    await db.add_item(user_id, item_key, qty)
    return {
        "item": {"key": item_key, "name": spec.name, "emoji": spec.emoji},
        "qty": qty,
        "spent": total,
        "balance": await db.get_balance(user_id),
    }


async def arm(user_id: int, item_key: str, qty: int = 1) -> dict:
    """Arm a piece of tackle — the shelf's ⚡ menu, not a separate mechanic.

    Scoped to `ARMABLE_EFFECTS` for the same reason the tackle shop's own menu
    is: a rob shield armed from a boat is nonsense, and the whole inventory
    belongs to the inventory page.
    """
    spec = item_catalog.find(item_key)
    effect = (spec.effect or {}) if spec else {}
    if not spec or effect.get("key") not in ARMABLE_EFFECTS:
        raise PlayError("That isn't fishing tackle you can arm.", code="not_armable")
    result = await consumables.use_item(user_id, item_key, qty)
    if not result.ok:
        raise PlayError(
            {
                "unknown": "You don't own that.",
                "not_usable": "That isn't something you arm.",
                "short": f"You only have {result.have}.",
            }.get(result.reason, "That didn't work."),
            code=result.reason,
        )
    return {
        "item": {"key": item_key, "name": spec.name, "emoji": spec.emoji},
        "qty": result.qty,
        "uses": result.uses,
        "duration": result.duration,
        "armed": await armed_effects(user_id),
    }


async def armed_effects(user_id: int) -> list[dict]:
    """What's live right now, in the order the tackle shop lists it."""
    effects = await db.get_active_effects(user_id)
    now = time.time()
    out = []
    for key in ARMABLE_EFFECTS:
        eff = effects.get(key)
        if not eff:
            continue
        out.append(
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
        )
    return out


async def trap_state(user_id: int) -> dict:
    """Whether a trap is soaking, and how much longer it needs."""
    trap = await db.get_trap(user_id)
    owned = await db.get_item_qty(user_id, FISH_TRAP)
    if not trap:
        return {"set": False, "owned": owned, "soak": TRAP_SOAK}
    soaked = time.time() - trap["set_at"]
    return {
        "set": True,
        "owned": owned,
        "spot": _spot_payload(trap["spot"]),
        "soak": TRAP_SOAK,
        "soaked": int(soaked),
        "ready": soaked >= TRAP_SOAK,
        "ready_in": max(0, int(TRAP_SOAK - soaked)),
    }


async def trap(user_id: int, guild_id: int) -> dict:
    """Set a trap, or pull one that has soaked long enough.

    One verb for both because that is how it reads to a member: you go to the
    trap. Which of the two happens is a property of the water, not a choice.
    """
    now = time.time()
    existing = await db.get_trap(user_id)
    if existing:
        pulled = await db.claim_trap(user_id, now, TRAP_SOAK)
        if not pulled:
            left = max(0, int(TRAP_SOAK - (now - existing["set_at"])))
            raise PlayError(
                f"Still soaking — give it {h.fmt_duration(left)}.",
                code="soaking",
                retry_after=left,
            )
        return await _pull_trap(user_id, guild_id, pulled["spot"])

    if not await db.try_consume_item(user_id, FISH_TRAP, 1):
        raise PlayError(
            "You don't own a fish trap. The tackle shop sells them.",
            code="no_trap",
        )
    fisher = await db.get_fisher(user_id)
    spot = resolve_spot(fisher["spot"])
    if not await db.set_trap(user_id, spot, now):
        await db.add_item(user_id, FISH_TRAP, 1)  # lost the race — hand it back
        raise PlayError("You already have a trap in the water.", code="already_set")
    return {"action": "set", "spot": _spot_payload(spot), "soak": TRAP_SOAK}


async def _pull_trap(user_id: int, guild_id: int, spot: str) -> dict:
    """Roll a soaked trap's basket, at the spot it was set in."""
    fisher = await db.get_fisher(user_id)
    parts = await _luck(user_id, guild_id, fisher)
    luck = _total_luck(parts)
    hauled: list[dict] = []
    coins = 0
    xp_total = 0
    for _ in range(TRAP_CATCHES):
        rarity = pick_spot_rarity(random.random(), rarity_odds(luck), spot)
        key = pick_spot_fish(rarity, random.random(), spot)
        xp_total += max(1, round(XP_PER_RARITY[rarity] * parts["xp_event_mult"]))
        if rarity == "treasure":
            paid = max(
                1, int(treasure_coins(key, random.random()) * parts["value_mult"])
            )
            coins += paid
            hauled.append({**fish_payload(key), "coins": paid, "treasure": True})
            continue
        weight = roll_weight(key, random.random())
        value = max(1, int(catch_value(key, weight) * parts["value_mult"]))
        await db.record_catch(user_id, key, weight, value, track_best=rarity != "junk")
        hauled.append(
            {
                **fish_payload(key),
                "weight": weight,
                "weight_label": fmt_weight(weight),
                "value": value,
                "treasure": False,
            }
        )
    if coins:
        await db.add_coins(user_id, coins)
        await db.add_fishing_earned(user_id, coins)
    new_xp = await db.add_fishing_xp(user_id, xp_total)
    return {
        "action": "pulled",
        "spot": _spot_payload(spot),
        "catches": hauled,
        "coins": coins,
        "xp": xp_total,
        "level": _level_payload(fisher["xp"], new_xp),
        "balance": await db.get_balance(user_id),
        "bag_count": await db.count_bag(user_id),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Read-only state
# ══════════════════════════════════════════════════════════════════════════════
def _rod_payload(level: int) -> dict:
    info = rod_info(level)
    nxt = next_rod(level)
    return {
        "level": level,
        "name": info["name"],
        "emoji": info.get("emoji", "🎣"),
        "luck": info["luck"],
        "max": level >= len(RODS) - 1,
        "next": (
            {
                "name": nxt["name"],
                "emoji": nxt.get("emoji", "🎣"),
                "price": nxt["price"],
                "luck": nxt["luck"],
            }
            if nxt
            else None
        ),
    }


def _spot_payload(spot: str) -> dict:
    key = resolve_spot(spot)
    info = spot_info(key)
    return {
        "key": key,
        "name": info["name"],
        "emoji": info.get("emoji", "🎣"),
        "blurb": info.get("blurb", ""),
        "hazard": info.get("hazard", 0.0),
        "level": info.get("level", 0),
        "price": info.get("price", 0),
    }


async def state(user_id: int, guild_id: int) -> dict:
    """Everything the fishing page paints itself with, in one round trip.

    Deliberately one call rather than six: on a phone each extra request is a
    spinner, and the page has nothing useful to show until it has all of this
    anyway.
    """
    cfg = await db.get_fishing_config(guild_id)
    fisher = await db.get_fisher(user_id)
    econ = await db.get_econ_config(guild_id)
    balance = await db.get_balance(user_id)
    spot = resolve_spot(fisher["spot"])
    level = fish_level(fisher["xp"])
    into, need, _ = level_progress(fisher["xp"])
    unlocked = await db.get_unlocked_spots(user_id)
    species = await db.get_species_counts(user_id)
    bag = await db.get_bag(user_id)
    today = int(time.time() // 86400)
    qgen = generate_quest(user_id, today)
    quest = await db.get_or_create_quest(
        user_id, today, qgen["quest_key"], qgen["target"]
    )
    now = time.time()

    return {
        "enabled": bool(cfg["enabled"]),
        "currency": {"name": econ["currency_name"], "emoji": econ["currency_emoji"]},
        "balance": balance,
        "cooldown": CAST_COOLDOWN,
        "ready_in": max(0, int(CAST_COOLDOWN - (now - fisher["last_cast"]))),
        "spot": _spot_payload(spot),
        "rod": _rod_payload(fisher["rod_level"]),
        "level": {
            "level": level,
            "xp": fisher["xp"],
            "into": into,
            "need": need,
            "luck_bonus": round(level_luck_bonus(level), 4),
        },
        "streak": {"days": fisher["streak_days"], "last_day": fisher["last_day"]},
        "stats": {
            "casts": fisher["casts"],
            "caught": fisher["caught"],
            "earned": fisher["earned"],
            "best": (
                {
                    **fish_payload(fisher["best_key"]),
                    "weight_label": fmt_weight(fisher["best_weight"]),
                }
                if fisher["best_key"]
                else None
            ),
        },
        "bag": {
            "count": sum(row["qty"] for row in bag),
            "value": sum(row["total_value"] for row in bag),
            "rows": [
                {
                    **fish_payload(r["fish_key"]),
                    "qty": r["qty"],
                    "total_value": r["total_value"],
                }
                for r in bag
            ],
        },
        "quest": {
            "key": quest["quest_key"],
            "label": quest_label(quest["quest_key"], quest["target"]),
            "progress": quest["progress"],
            "target": quest["target"],
            "claimed": bool(quest["claimed"]),
            "reward": dict(
                zip(("coins", "xp"), quest_reward(quest["quest_key"], quest["target"]))
            ),
        },
        "spots": [
            {
                **_spot_payload(key),
                "current": key == spot,
                "state": unlock_state(key, level, unlocked, balance),
                "exclusives": [fish_payload(k) for k in exclusive_keys(key)],
            }
            for key in SPOT_ORDER
        ],
        "dex": {
            "caught": len(species),
            "total": len(FISH),
            "species": [
                {**fish_payload(key), "count": species.get(key, 0)} for key in FISH
            ],
        },
        "shop": await _shop_payload(user_id, balance),
        "armed": await armed_effects(user_id),
        "trap": await trap_state(user_id),
        "events": [
            {
                "key": e["event_key"],
                "label": EVENT_LABELS.get(e["event_key"], e["event_key"]),
                "magnitude": e["magnitude"],
                "ends_in": max(0, int(e["expires_at"] - now)),
            }
            for e in await db.get_active_events(guild_id)
        ],
    }


async def _shop_payload(user_id: int, balance: int) -> list[dict]:
    rows = []
    for key in SHOP_KEYS:
        spec = item_catalog.find(key)
        if not spec:
            continue
        rows.append(
            {
                "key": key,
                "name": spec.name,
                "emoji": spec.emoji,
                "description": spec.description,
                "price": spec.price,
                "category": spec.category,
                "owned": await db.get_item_qty(user_id, key),
                "affordable": balance >= spec.price,
                "armable": bool((spec.effect or {}).get("key") in ARMABLE_EFFECTS),
            }
        )
    return rows
