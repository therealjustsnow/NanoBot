"""
web/engine/activities.py
Work, mining, hunting, exploring — and the encounters that hang off them.

Mirrors `Activities.run_activity`, in its order and for its stated reason: the
claim first (so a refusal costs nothing), the streak straight after (so every
payout below is multiplied by it), then the roll.

Charges are the same token bucket, because they are the *same row* —
`db.try_claim_activity` with the same `max_charges`. A member who banks twelve
runs overnight finds twelve waiting whether they open Discord or the site, and
spending one in the browser spends it everywhere. There is no second bucket.

Encounters are the one place the web can do better than an embed rather than
differently: the same registry, the same single-use rule, but the choice can sit
in the page instead of on a message that scrolls away. The single-use guard is
carried in a short-lived token here — an encounter has no database row of its
own, so what stops a double payout in Discord is a `_taken` flag on the view,
and the browser needs the same thing that survives a page it can't see.
"""

from __future__ import annotations

import random
import secrets
import time
from typing import Any, Optional

from cogs.activities.constants import (
    ACTIVITY_INFO,
    ACTIVITY_NAMES,
    ENCOUNTERS,
    EXPLORE_COINS_BIG,
    EXPLORE_COINS_SMALL,
    EXPLORE_FLAVOR,
    HUNT_CATCHES,
    ORES,
    PICKAXES,
    ROB_FINE,
    ROB_MIN_ROBBER_BALANCE,
    ROB_MIN_TARGET_BALANCE,
)
from cogs.activities.helpers import (
    apply_streak,
    career_info,
    charge_state,
    effective_cooldown,
    hunt_injury_fine,
    max_charges,
    next_adventure_streak,
    next_career,
    next_pickaxe,
    pick_explore_outcome,
    pick_hunt_catch,
    pick_ore,
    pick_work_scene,
    pickaxe_info,
    resolve_encounter,
    outcome_coins,
    outcome_next,
    rob_steal_amount,
    rob_success,
    roll_cave_in,
    roll_coin_amount,
    roll_encounter,
    roll_hunt_bag,
    roll_hunt_injury,
    roll_hunt_padlock,
    roll_mine_treasure_key,
    roll_vein,
    roll_work_pay,
    streak_multiplier,
)
from utils import db, globalxp
from utils import helpers as h
from utils import items as item_catalog
from utils.db._ttl import TTLCache

from .fishing import PlayError

# Activities a browser button can run. `rob` is excluded for the same reason it
# has no Discord button: it takes a target, and a one-tap control can't ask for
# one. It has its own endpoint that does.
RUNNABLE = tuple(a for a in ACTIVITY_NAMES if a != "rob")

# Pending encounters, by opaque token. An encounter is worth one payout and has
# no row to check against, so the token *is* the claim: popped before anything
# is paid, which makes a double-submit a miss rather than a second reward. Ten
# minutes matches how long the Discord view stays live.
_pending = TTLCache(maxsize=4096, ttl=600.0)


async def config(guild_id: int) -> dict:
    """This server's switches with the bot-wide cooldowns folded in.

    Same merge the cog does — the guild owns the on/off switch and nothing else,
    because the claim the cooldown gates is global.
    """
    cfg = dict(await db.get_activities_config(guild_id))
    for activity, seconds in (await db.get_activity_cooldowns()).items():
        cfg[f"{activity}_cooldown"] = seconds
    return cfg


def cooldown_of(cfg: dict, activity: str) -> int:
    return effective_cooldown(activity, cfg.get(f"{activity}_cooldown"))


async def _touch_streak(user_id: int, stats: dict) -> tuple[int, bool]:
    """Advance the daily streak, once a day, through the conditional stamp."""
    today = int(time.time() // 86_400)
    last_day = stats.get("last_day") or 0
    current = stats.get("streak_days") or 0
    if last_day == today:
        return max(1, current), False
    streak = next_adventure_streak(last_day, today, current)
    if await db.try_claim_adventure_streak(user_id, today, streak):
        return streak, True
    fresh = await db.get_activity_stats(user_id)
    return max(1, fresh["streak_days"] or 0), False


# ══════════════════════════════════════════════════════════════════════════════
#  Running one activity
# ══════════════════════════════════════════════════════════════════════════════
async def run(user_id: int, guild_id: int, activity: str) -> dict:
    """Check, claim, and resolve one run."""
    if activity not in RUNNABLE:
        raise PlayError("That isn't something you can run.", code="unknown_activity")

    cfg = await config(guild_id)
    info = ACTIVITY_INFO[activity]
    if not cfg[f"{activity}_enabled"]:
        raise PlayError(info["disabled"], code="disabled")

    retry = await db.try_claim_activity(
        user_id,
        activity,
        time.time(),
        cooldown_of(cfg, activity),
        max_charges(activity),
    )
    if retry:
        raise PlayError(
            info["wait"].format(wait=h.fmt_duration(retry)).replace("**", ""),
            code="cooldown",
            retry_after=retry,
        )

    await globalxp.award(user_id, "activity")
    stats = await db.get_activity_stats(user_id)
    streak, streak_started = await _touch_streak(user_id, stats)
    boost = await db.get_active_effects(user_id)

    resolver = {
        "work": _resolve_work,
        "mine": _resolve_dig,
        "hunt": _resolve_hunt,
        "explore": _resolve_explore,
    }[activity]
    result = await resolver(user_id, stats, streak, boost)

    result.update(
        {
            "activity": activity,
            "emoji": info["emoji"],
            "streak": {
                "days": streak,
                "started": streak_started,
                "multiplier": round(streak_multiplier(streak), 4),
            },
            "balance": await db.get_balance(user_id),
            "charges": await charges(user_id, guild_id),
        }
    )
    encounter = _maybe_encounter(user_id, guild_id, activity)
    if encounter:
        result["encounter"] = encounter
    return result


async def run_many(user_id: int, guild_id: int, activity: Optional[str] = None) -> dict:
    """Empty every banked charge — the 📥 Collect all button.

    Totals are measured as balance and inventory *deltas* around the batch
    rather than summed from each resolver, so the summary stays honest about a
    streak multiplier, an injury fine, or a cave-in that paid nothing. At most
    one encounter survives, for the reason the cog gives: they fire per run, and
    a full bucket would otherwise stack a dozen two-button choices.
    """
    before_coins = await db.get_balance(user_id)
    before_items = {r["item_key"]: r["qty"] for r in await db.get_inventory(user_id)}

    targets = (activity,) if activity else RUNNABLE
    ran: dict[str, int] = {}
    encounter = None
    for name in targets:
        if name not in RUNNABLE:
            continue
        # Bounded by the cap as well as the claim, so a bug in the bucket
        # arithmetic can't become an unbounded payout loop.
        for _ in range(max_charges(name)):
            try:
                result = await run(user_id, guild_id, name)
            except PlayError:
                break
            ran[name] = ran.get(name, 0) + 1
            encounter = encounter or result.get("encounter")

    if not ran:
        raise PlayError("Nothing is ready to collect yet.", code="cooldown")

    after_items = {r["item_key"]: r["qty"] for r in await db.get_inventory(user_id)}
    gained = [
        {
            **_item_payload(key),
            "qty": qty - before_items.get(key, 0),
        }
        for key, qty in after_items.items()
        if qty > before_items.get(key, 0)
    ]
    after_coins = await db.get_balance(user_id)
    return {
        "outcome": "collected",
        "ran": [
            {"activity": k, "runs": v, "emoji": ACTIVITY_INFO[k]["emoji"]}
            for k, v in ran.items()
        ],
        "runs": sum(ran.values()),
        "coins": after_coins - before_coins,
        "items": sorted(gained, key=lambda r: -r["qty"]),
        "balance": after_coins,
        "charges": await charges(user_id, guild_id),
        **({"encounter": encounter} if encounter else {}),
    }


def _item_payload(key: str) -> dict:
    spec = item_catalog.get(key)
    if not spec:
        return {"key": key, "name": key, "emoji": "📦", "value": 0, "category": "misc"}
    return {
        "key": key,
        "name": spec.name,
        "emoji": spec.emoji,
        "value": spec.value,
        "category": spec.category,
    }


# ── resolvers ─────────────────────────────────────────────────────────────────
async def _resolve_work(user_id: int, stats: dict, streak: int, boost: dict) -> dict:
    info = career_info(stats["work_shifts"])
    # `stats` is read after the claim, so work_shifts already counts this shift.
    promoted = info["tier"] > career_info(max(0, stats["work_shifts"] - 1))["tier"]
    pay = h.apply_coin_boost(
        apply_streak(roll_work_pay(random.random(), info["bonus"]), streak), boost
    )
    await db.add_coins(user_id, pay)
    nxt = next_career(stats["work_shifts"])
    return {
        "outcome": "work",
        "scene": pick_work_scene(random.random()),
        "coins": pay,
        "items": [],
        "career": {
            "title": info["title"],
            "tier": info["tier"],
            "bonus": info["bonus"],
            "promoted": promoted,
            "shifts": stats["work_shifts"],
            "next": ({"title": nxt["title"], "shifts": nxt["shifts"]} if nxt else None),
        },
    }


async def _resolve_dig(user_id: int, stats: dict, streak: int, boost: dict) -> dict:
    if roll_cave_in(random.random()):
        return {"outcome": "cave_in", "coins": 0, "items": []}

    pickaxe = pickaxe_info(stats["pickaxe_level"])
    # One vein rolled ore by ore: a seam of four diamonds is possible and rare,
    # which is why the size and the rarity are separate rolls.
    vein = roll_vein(random.random(), pickaxe["vein"])
    haul: dict[str, int] = {}
    for _ in range(vein):
        key = pick_ore(random.random(), pickaxe["luck"])
        haul[key] = haul.get(key, 0) + 1
    for key, qty in haul.items():
        await db.add_item(user_id, key, qty)

    bonus = []
    if roll_mine_treasure_key(random.random()):
        await db.add_item(user_id, "treasure_key", 1)
        bonus.append({**_item_payload("treasure_key"), "qty": 1})

    return {
        "outcome": "dig",
        "coins": 0,
        "vein": vein,
        "rich_seam": vein >= 3,
        "items": [{**_item_payload(k), "qty": q} for k, q in haul.items()] + bonus,
        "pickaxe": {
            "name": pickaxe["name"],
            "emoji": pickaxe["emoji"],
            "luck": pickaxe["luck"],
        },
    }


async def _resolve_hunt(user_id: int, stats: dict, streak: int, boost: dict) -> dict:
    # Each catch is rolled on its own, so a good hunt can turn up a trophy
    # alongside the pelts rather than three of the same thing.
    bag: dict[str, int] = {}
    for _ in range(roll_hunt_bag(random.random())):
        key = pick_hunt_catch(random.random())
        bag[key] = bag.get(key, 0) + 1
    for key, qty in bag.items():
        await db.add_item(user_id, key, qty)

    fine = 0
    if roll_hunt_injury(random.random()):
        fine = hunt_injury_fine(random.random())
        if fine > 0:
            await db.add_coins(user_id, -fine)

    extra = []
    if roll_hunt_padlock(random.random()):
        await db.add_item(user_id, "padlock", 1)
        extra.append({**_item_payload("padlock"), "qty": 1})

    return {
        "outcome": "hunt",
        "coins": -fine,
        "injury": fine,
        "items": [{**_item_payload(k), "qty": q} for k, q in bag.items()] + extra,
    }


async def _resolve_explore(user_id: int, stats: dict, streak: int, boost: dict) -> dict:
    outcome = pick_explore_outcome(random.random())
    payload: dict[str, Any] = {
        "outcome": "explore",
        "find": outcome,
        "flavor": EXPLORE_FLAVOR[outcome],
        "coins": 0,
        "items": [],
    }
    if outcome == "nothing":
        return payload
    if outcome in ("coins_small", "coins_big"):
        lo, hi = EXPLORE_COINS_SMALL if outcome == "coins_small" else EXPLORE_COINS_BIG
        amount = h.apply_coin_boost(
            apply_streak(roll_coin_amount(random.random(), lo, hi), streak), boost
        )
        await db.add_coins(user_id, amount)
        payload["coins"] = amount
        payload["big"] = outcome == "coins_big"
        return payload
    await db.add_item(user_id, outcome, 1)
    payload["items"] = [{**_item_payload(outcome), "qty": 1}]
    return payload


# ── encounters ────────────────────────────────────────────────────────────────
def _maybe_encounter(user_id: int, guild_id: int, activity: str) -> Optional[dict]:
    """Roll for an encounter and, if one fires, mint its single-use token."""
    key = roll_encounter(activity, random.random(), random.random())
    if not key:
        return None
    return _open_encounter(user_id, guild_id, key)


def _open_encounter(user_id: int, guild_id: int, key: str) -> dict:
    """Mint a single-use token for one encounter — an opener or a stage."""
    spec = ENCOUNTERS[key]
    token = secrets.token_urlsafe(12)
    _pending.put(token, {"user_id": user_id, "guild_id": guild_id, "encounter": key})
    return {
        "token": token,
        "key": key,
        "emoji": spec["emoji"],
        "title": spec["title"],
        "prompt": spec["prompt"],
        "options": [
            {"key": o["key"], "label": o["label"], "emoji": o["emoji"]}
            for o in spec["options"]
        ],
    }


async def choose(user_id: int, token: str, option_key: str) -> dict:
    """Resolve one encounter choice, paying out exactly once.

    The token is popped *before* anything is paid, which is what makes a
    double-tap a miss rather than a second reward — the browser equivalent of
    `EncounterView._taken`.

    An outcome naming a follow-up stage mints a *fresh* token for it and
    returns it under the same `encounter` key a run does, so the page continues
    the chain through the code path it already has. Popping first and minting
    second is what keeps each stage worth exactly one payout.
    """
    pending = _pending.get(token)
    if not pending or int(pending["user_id"]) != int(user_id):
        raise PlayError(
            "That moment has passed — the encounter timed out.",
            code="expired",
        )
    _pending.put(token, None, now=0.0)

    outcome = resolve_encounter(pending["encounter"], option_key, random.random())
    if outcome is None:
        raise PlayError("That isn't one of the options.", code="bad_option")

    coins = outcome_coins(outcome, random.random())
    if coins:
        effects = await db.get_active_effects(user_id)
        # A boost multiplies a reward and never a cost — an encounter that
        # charges 250 still charges 250 while doubled.
        coins = h.apply_coin_boost(coins, effects) if coins > 0 else coins
        await db.add_coins(user_id, coins)
    items = []
    if "item" in outcome:
        # A (key, qty) pair, like every other outcome in the registry — reading
        # it as a bare key put a tuple where an item_key belongs.
        item_key, qty = outcome["item"]
        await db.add_item(user_id, item_key, qty)
        items = [{**_item_payload(item_key), "qty": qty}]

    payload = {
        "text": outcome["text"],
        "coins": coins,
        "items": items,
        "balance": await db.get_balance(user_id),
    }
    next_key = outcome_next(outcome)
    if next_key:
        payload["encounter"] = _open_encounter(
            user_id, int(pending["guild_id"]), next_key
        )
    return payload


# ══════════════════════════════════════════════════════════════════════════════
#  Reading state
# ══════════════════════════════════════════════════════════════════════════════
async def charges(user_id: int, guild_id: int) -> list[dict]:
    """Every activity's bucket, read-only.

    The claim stays the sole arbiter — this may be a second stale by the time a
    button is pressed, and a stale press is refused rather than paid.
    """
    cfg = await config(guild_id)
    stats = await db.get_activity_stats(user_id)
    now = time.time()
    out = []
    for name in ACTIVITY_NAMES:
        info = ACTIVITY_INFO[name]
        cooldown = cooldown_of(cfg, name)
        state = charge_state(
            stats.get(f"last_{name}") or 0.0, now, cooldown, max_charges(name)
        )
        out.append(
            {
                "activity": name,
                "emoji": info["emoji"],
                "command": info["command"],
                "blurb": info["blurb"],
                "enabled": bool(cfg[f"{name}_enabled"]),
                "runnable": name in RUNNABLE,
                "cooldown": cooldown,
                "max_charges": max_charges(name),
                "ready": state["ready"],
                "next_in": state["next_in"],
                "count": stats.get(f"{name}_count", 0),
            }
        )
    return out


async def state(user_id: int, guild_id: int) -> dict:
    """The adventure dashboard, in one read."""
    stats = await db.get_activity_stats(user_id)
    econ = await db.get_econ_config(guild_id)
    balance = await db.get_balance(user_id)
    career = career_info(stats["work_shifts"])
    nxt_career = next_career(stats["work_shifts"])
    pickaxe = pickaxe_info(stats["pickaxe_level"])
    nxt_pickaxe = next_pickaxe(stats["pickaxe_level"])
    rows = await charges(user_id, guild_id)
    today = int(time.time() // 86_400)
    streak = stats.get("streak_days") or 0
    if (stats.get("last_day") or 0) != today:
        streak = 0  # not claimed today — shown as "start one" rather than a lie

    return {
        "currency": {"name": econ["currency_name"], "emoji": econ["currency_emoji"]},
        "balance": balance,
        "activities": rows,
        "ready_total": sum(r["ready"] for r in rows if r["enabled"] and r["runnable"]),
        "streak": {
            "days": streak,
            "multiplier": round(streak_multiplier(max(1, streak)), 4),
            "claimed_today": (stats.get("last_day") or 0) == today,
        },
        "career": {
            "title": career["title"],
            "tier": career["tier"],
            "bonus": career["bonus"],
            "shifts": stats["work_shifts"],
            "next": (
                {
                    "title": nxt_career["title"],
                    "shifts": nxt_career["shifts"],
                    "to_go": max(0, nxt_career["shifts"] - stats["work_shifts"]),
                }
                if nxt_career
                else None
            ),
        },
        "pickaxe": {
            "level": stats["pickaxe_level"],
            "tier": stats["pickaxe_level"] + 1,
            "tiers": len(PICKAXES),
            "name": pickaxe["name"],
            "emoji": pickaxe["emoji"],
            "luck": pickaxe["luck"],
            "vein": pickaxe["vein"],
            "digs": stats["mine_count"],
            "next": (
                {
                    "name": nxt_pickaxe["name"],
                    "emoji": nxt_pickaxe["emoji"],
                    "price": nxt_pickaxe["price"],
                    "luck": nxt_pickaxe["luck"],
                    "vein": nxt_pickaxe["vein"],
                    "affordable": balance >= nxt_pickaxe["price"],
                }
                if nxt_pickaxe
                else None
            ),
        },
        "ores": [
            {**_item_payload(k), "label": v["name"], "emoji": v["emoji"]}
            for k, v in ORES.items()
        ],
        "quarry": [
            {**_item_payload(k), "label": v["name"], "emoji": v["emoji"]}
            for k, v in HUNT_CATCHES.items()
        ],
    }


async def upgrade_pickaxe(user_id: int, guild_id: int) -> dict:
    """Buy the next pickaxe tier."""
    stats = await db.get_activity_stats(user_id)
    nxt = next_pickaxe(stats["pickaxe_level"])
    if nxt is None:
        raise PlayError("You already own the best pickaxe there is.", code="maxed")
    if not await db.try_debit_coins(user_id, nxt["price"]):
        raise PlayError(
            f"That pickaxe costs {nxt['price']:,} and you don't have it yet.",
            code="broke",
        )
    if not await db.set_pickaxe_level(
        user_id, stats["pickaxe_level"] + 1, expected=stats["pickaxe_level"]
    ):
        await db.add_coins(user_id, nxt["price"])
        raise PlayError("That upgrade already went through.", code="race")
    return {
        "pickaxe": {
            "name": nxt["name"],
            "emoji": nxt["emoji"],
            "luck": nxt["luck"],
            "vein": nxt["vein"],
        },
        "spent": nxt["price"],
        "balance": await db.get_balance(user_id),
    }


async def rob(user_id: int, guild_id: int, target_id: int) -> dict:
    """The one activity with a target, and four guard rails of its own."""
    cfg = await config(guild_id)
    if not cfg["rob_enabled"]:
        raise PlayError(ACTIVITY_INFO["rob"]["disabled"], code="disabled")
    if int(target_id) == int(user_id):
        raise PlayError("You can't rob yourself.", code="bad_target")

    if "rob_shield" in await db.get_active_effects(target_id):
        raise PlayError(
            "They're holding a padlock — protected from robbery right now.",
            code="shielded",
        )
    robber_balance = await db.get_balance(user_id)
    if robber_balance < ROB_MIN_ROBBER_BALANCE:
        raise PlayError(
            f"You need at least {ROB_MIN_ROBBER_BALANCE:,} of your own to risk this.",
            code="too_poor",
        )
    target_balance = await db.get_balance(target_id)
    if target_balance < ROB_MIN_TARGET_BALANCE:
        raise PlayError(
            "They don't have enough on them to be worth it.", code="target_poor"
        )

    retry = await db.try_claim_activity(
        user_id, "rob", time.time(), cooldown_of(cfg, "rob"), max_charges("rob")
    )
    if retry:
        raise PlayError(
            f"Lying low — try again in {h.fmt_duration(retry)}.",
            code="cooldown",
            retry_after=retry,
        )

    await globalxp.award(user_id, "activity")
    effects = await db.get_active_effects(user_id)
    if rob_success(random.random(), has_luck="luck" in effects):
        amount = rob_steal_amount(random.random(), target_balance)
        await db.transfer_coins(target_id, user_id, amount)
        return {
            "outcome": "success",
            "coins": amount,
            "balance": await db.get_balance(user_id),
        }
    await db.add_coins(user_id, -ROB_FINE)
    return {
        "outcome": "caught",
        "coins": -ROB_FINE,
        "balance": await db.get_balance(user_id),
    }
