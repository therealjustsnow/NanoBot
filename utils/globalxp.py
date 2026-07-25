"""utils/globalxp.py — the account-wide (global) level system.

This is deliberately **not** the per-guild leveling cog. The two exist side by
side and answer different questions:

  * ``cogs/leveling.py`` — "how active are you *in this community*?" Server
    admins own it: XP rate, cooldown, role rewards, announcements, resets.
  * this module              — "how much have you played the bot, ever?" Nobody
    owns it. The curve and every award value below are **hard-coded** so the
    number means the same thing in every server.

Rules that follow from that:

  * A server with a 5x server-XP rate does not grant global XP any faster.
  * A server with leveling switched off still earns its members global XP.
  * Global XP is awarded per *normalized action* (a message, a cast, a game, a
    daily claim) — never by copying or scaling a guild's XP.

Award sites call ``award(user_id, "action")``; nothing else is required of
them. A level-up is *recorded* here (``pending_level``) rather than announced,
because half the award sites aren't a place a message can be sent from — the
identity cog delivers it the next time the member turns up. Everything is one
atomic DB statement, so two servers awarding at the same instant can't lose an
update.
"""

import math
import time

from utils import db

# ── The curve ────────────────────────────────────────────────────────────────
# Cumulative XP to *reach* level L: 25·L² + 75·L. Quadratic like the classic
# chat-level curves, so early levels come quickly and late ones are a genuine
# grind (L10 = 3,250 · L50 = 66,250 · L87 = 195,750). Hard-coded on purpose:
# see the module docstring.
_XP_QUADRATIC = 25
_XP_LINEAR = 75


def cum_xp_for_level(level: int) -> int:
    """Total lifetime XP needed to reach `level` (level 0 = 0)."""
    level = max(0, int(level))
    return _XP_QUADRATIC * level * level + _XP_LINEAR * level


def level_from_xp(xp: int) -> int:
    """The level a lifetime XP total puts an account at (floors at 0)."""
    xp = max(0, int(xp))
    # Inverse of the quadratic above, floored.
    disc = _XP_LINEAR**2 + 4 * _XP_QUADRATIC * xp
    return int((-_XP_LINEAR + math.sqrt(disc)) // (2 * _XP_QUADRATIC))


def level_progress(xp: int) -> tuple[int, int, int]:
    """(level, xp_into_level, xp_needed_for_next) for a lifetime XP total."""
    level = level_from_xp(xp)
    floor_xp = cum_xp_for_level(level)
    next_xp = cum_xp_for_level(level + 1)
    return level, int(xp) - floor_xp, next_xp - floor_xp


# ── What earns global XP ─────────────────────────────────────────────────────
# One entry per *normalized action*. Values are deliberately flat: an action is
# worth the same everywhere, regardless of the server's own settings, its size,
# or its economy tuning. Adding a source is one entry here plus one `award()`
# call at the site that performs the action.
XP_AWARDS: dict[str, int] = {
    "message": 8,  # a chat message (rate-limited, see MESSAGE_COOLDOWN)
    "daily": 40,  # /daily claim
    "fish_cast": 5,  # a cast that landed something
    "fish_sell": 2,  # selling a haul
    "casino_game": 4,  # any settled casino game
    "activity": 6,  # /work, /mine, /hunt, /explore, /rob
    "craft": 5,  # crafting anything
    "shop": 5,  # redeeming a shop reward
    "coop": 15,  # a confirmed /squad or finished /raid
    "quest": 25,  # a completed daily quest / casino challenge
    "achievement": 50,  # earning an achievement
}

# Chat XP is the one source a member can fire at will, so it gets the same
# style of per-user cooldown the guild leveling cog uses — fixed, not tunable.
MESSAGE_COOLDOWN = 60


def xp_for_action(action: str) -> int:
    """XP an action is worth (0 for anything unregistered)."""
    return XP_AWARDS.get(action, 0)


async def award(user_id: int, action: str, *, multiplier: int = 1) -> dict:
    """Grant the XP for `action` and report whether it levelled the account up.

    Returns {"awarded", "xp", "level", "levelled_up", "old_level"}. `awarded`
    is 0 for unknown actions, which makes call sites safe to add before the
    action is registered.
    """
    amount = xp_for_action(action) * max(1, int(multiplier))
    if amount <= 0:
        current = await db.get_global_xp(user_id)
        return {
            "awarded": 0,
            "xp": current,
            "level": level_from_xp(current),
            "levelled_up": False,
            "old_level": level_from_xp(current),
        }
    before, after = await db.add_global_xp(user_id, amount)
    old_level, new_level = level_from_xp(before), level_from_xp(after)
    if new_level > old_level:
        # Award sites are all over the bot (and some, like the co-op payout,
        # aren't even the member's own command), so the *announcement* is
        # decoupled from the award: record it and let whichever cog next sees
        # this member deliver it. See cogs/identity's delivery chain.
        await db.set_pending_levelup(user_id, new_level)
    return {
        "awarded": amount,
        "xp": after,
        "level": new_level,
        "levelled_up": new_level > old_level,
        "old_level": old_level,
    }


async def award_message(user_id: int, now: float | None = None) -> dict:
    """Chat XP, rate-limited per user. A no-op inside the cooldown window.

    The claim is a conditional UPDATE in the DB layer, so a member spamming in
    two servers at once still only earns one message's worth.
    """
    now = time.time() if now is None else now
    if not await db.try_claim_global_message(user_id, now, MESSAGE_COOLDOWN):
        return {"awarded": 0, "levelled_up": False}
    return await award(user_id, "message")
