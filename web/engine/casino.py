"""
web/engine/casino.py
The five casino games, resolved into plain dicts.

`settle` is the web's copy of `Casino._settle_common`, and it is copied in the
same sense the rest of this package is: the *order* is restated, every number in
it comes from the cog. Debit, resolve, apply the streak bonus on a true win,
feed the jackpot on a net loss, record the game, bump today's challenges. Get
that order wrong and the house edge changes, which is why it lives in one
function here rather than five times over.

Blackjack is the interesting one. A hand is persisted to the *same*
`casino_blackjack` table Discord uses, so a hand dealt in the browser is a real
open hand: it expires on the same timer, settles through the same atomic
`claim_blackjack_hand`, and — because a web hand has no message to edit — is
picked up by the cog's existing orphaned-hand sweep on restart, which settles it
rather than leaving the bet debited forever. That path already existed for a
crash between the row insert and the reply; a web hand is the same shape.
"""

from __future__ import annotations

import random
import time
from typing import Optional

from cogs.casino.constants import (
    BLACKJACK_DECKS,
    BJ_TIMEOUT,
    JACKPOT_FEED_RATE,
    ROULETTE_SPACES,
    SLOT_JACKPOT_SYMBOL,
    STREAK_BONUS_CAP,
    STREAK_BONUS_STEP,
    STREAK_MIN,
)
from cogs.casino.helpers import (
    apply_streak_bonus,
    card_label,
    challenge_short_label,
    dealer_should_hit,
    generate_challenges,
    hand_value,
    is_blackjack,
    new_shoe,
    parse_roulette_space,
    resolve_dice,
    resolve_flip,
    resolve_roulette,
    resolve_slots,
    settle_blackjack,
    spin_number,
    streak_bonus,
)
from utils import db, globalxp

from .fishing import PlayError

GAMES = ("flip", "dice", "slots", "roulette", "blackjack")


async def _guard(user_id: int, guild_id: int, bet: int) -> dict:
    """Every game's front door: enabled, bet in range, and the config back."""
    cfg = await db.get_casino_config(guild_id)
    if not cfg["enabled"]:
        raise PlayError(
            "Casino games are switched off in this server.", code="disabled"
        )
    try:
        bet = int(bet)
    except (TypeError, ValueError):
        raise PlayError("That isn't a number of coins.", code="bad_bet") from None
    if bet < cfg["min_bet"]:
        raise PlayError(
            f"The smallest bet here is {cfg['min_bet']:,}.", code="bet_too_small"
        )
    if bet > cfg["max_bet"]:
        raise PlayError(
            f"The biggest bet here is {cfg['max_bet']:,}.", code="bet_too_big"
        )
    return cfg


async def _take_bet(user_id: int, bet: int) -> int:
    """Debit the stake, or refuse. Returns the streak the game starts on."""
    if not await db.try_debit_coins(user_id, bet):
        balance = await db.get_balance(user_id)
        raise PlayError(f"That's a {bet:,} bet and you have {balance:,}.", code="broke")
    stats = await db.get_casino_stats(user_id)
    return stats["streak"]


async def settle(
    guild_id: int, user_id: int, bet: int, streak: int, raw_payout: int
) -> dict:
    """The shared tail of every game — the web's `_settle_common`.

    Order matters and is the cog's: the streak bonus applies only to a *true*
    win (a push refunds and must not be boosted), the jackpot is fed a slice of
    the net loss, and the challenge bump happens last because it reads the
    payout that actually landed.
    """
    final_payout = raw_payout
    if raw_payout > bet:
        final_payout = apply_streak_bonus(raw_payout, streak + 1)
    if final_payout > 0:
        await db.add_coins(user_id, final_payout)
    net = final_payout - bet
    if net < 0:
        await db.add_to_jackpot(guild_id, round(-net * JACKPOT_FEED_RATE))
    stats = await db.record_casino_game(user_id, bet, final_payout)
    await globalxp.award(user_id, "casino_game")
    challenges = await _bump_challenges(user_id, bet, final_payout)
    return {
        "payout": final_payout,
        "net": final_payout - bet,
        "streak": stats["streak"],
        # The bonus that was actually applied, from the cog's own curve rather
        # than recomputed here — a second formula is a second thing to get wrong.
        "streak_bonus": round(streak_bonus(streak + 1), 4) if final_payout > bet else 0,
        "stats": stats,
        "challenges": challenges,
        "balance": await db.get_balance(user_id),
    }


async def _bump_challenges(user_id: int, bet: int, payout: int) -> list[dict]:
    """Advance today's two challenges and auto-claim any that just finished.

    Returns every challenge's state rather than the cog's one-line footer hint:
    a web page has room to show both, and "1 more win" is the thing that brings
    somebody back tomorrow.
    """
    won = payout > bet
    today = int(time.time() // 86400)
    out = []
    for chal in generate_challenges(user_id, today):
        key, target, reward = chal["chal_key"], chal["target"], chal["reward"]
        current = await db.get_challenge_progress(user_id, today, key)
        row = current
        if not current["claimed"]:
            amount, mode = 0, "add"
            if key == "win_games":
                amount = 1 if won else 0
            elif key == "play_games":
                amount = 1
            elif key == "wager_coins":
                amount = bet
            elif key == "big_payout":
                amount, mode = payout, "max"
            if not (mode == "add" and amount <= 0):
                row = await db.bump_challenge_progress(
                    user_id, today, key, target, amount, mode=mode
                )
        claimed_now = False
        if row["progress"] >= target and not row["claimed"]:
            if await db.try_claim_challenge(user_id, today, key):
                await db.add_coins(user_id, reward)
                await globalxp.award(user_id, "quest")
                claimed_now = True
        out.append(
            {
                "key": key,
                "label": chal["label"],
                "short": challenge_short_label(key),
                "progress": min(row["progress"], target),
                "target": target,
                "reward": reward,
                "claimed": bool(row["claimed"]) or claimed_now,
                "completed_now": claimed_now,
            }
        )
    return out


# ══════════════════════════════════════════════════════════════════════════════
#  The instant games
# ══════════════════════════════════════════════════════════════════════════════
async def flip(user_id: int, guild_id: int, bet: int, side: str) -> dict:
    choice = (side or "").strip().lower()
    if choice not in ("heads", "tails"):
        raise PlayError("Pick heads or tails.", code="bad_side")
    await _guard(user_id, guild_id, bet)
    streak = await _take_bet(user_id, bet)
    outcome = resolve_flip(bet, choice, random.random())
    result = await settle(guild_id, user_id, bet, streak, outcome["payout"])
    return {
        "game": "flip",
        "bet": bet,
        "choice": choice,
        "result": outcome["result"],
        "won": outcome["won"],
        **result,
    }


async def dice(user_id: int, guild_id: int, bet: int) -> dict:
    await _guard(user_id, guild_id, bet)
    streak = await _take_bet(user_id, bet)
    outcome = resolve_dice(
        bet,
        (random.random(), random.random()),
        (random.random(), random.random()),
    )
    result = await settle(guild_id, user_id, bet, streak, outcome["payout"])
    return {
        "game": "dice",
        "bet": bet,
        "player_rolls": outcome["player_rolls"],
        "dealer_rolls": outcome["dealer_rolls"],
        "player_total": outcome["player_total"],
        "dealer_total": outcome["dealer_total"],
        "outcome": outcome["outcome"],
        "won": outcome["outcome"] == "win",
        **result,
    }


async def slots(user_id: int, guild_id: int, bet: int) -> dict:
    """Spin the reels — and claim the progressive jackpot on a triple 7.

    The jackpot claim is `try_claim_jackpot`, a compare-and-swap, so two triple
    sevens landing in the same instant can't both win the pot.
    """
    await _guard(user_id, guild_id, bet)
    streak = await _take_bet(user_id, bet)
    outcome = resolve_slots(bet, (random.random(), random.random(), random.random()))
    result = await settle(guild_id, user_id, bet, streak, outcome["payout"])

    jackpot = 0
    if outcome["jackpot_win"]:
        jackpot = await db.try_claim_jackpot(guild_id)
        if jackpot:
            await db.add_coins(user_id, jackpot)
            result["balance"] = await db.get_balance(user_id)
    return {
        "game": "slots",
        "bet": bet,
        "reels": outcome["reels"],
        "outcome": outcome["outcome"],
        "won": outcome["payout"] > 0,
        "jackpot": jackpot,
        "jackpot_symbol": SLOT_JACKPOT_SYMBOL,
        **result,
    }


async def roulette(user_id: int, guild_id: int, bet: int, space: str) -> dict:
    parsed = parse_roulette_space(str(space or ""))
    if parsed is None:
        raise PlayError(
            "Bet on red, black, odd, even, high or low — or an exact number "
            "from 0 to 36.",
            code="bad_space",
        )
    await _guard(user_id, guild_id, bet)
    streak = await _take_bet(user_id, bet)
    number = spin_number(random.random())
    outcome = resolve_roulette(bet, parsed, number)
    result = await settle(guild_id, user_id, bet, streak, outcome["payout"])
    return {
        "game": "roulette",
        "bet": bet,
        "space": parsed if isinstance(parsed, str) else str(parsed),
        "number": outcome["number"],
        "color": outcome["color"],
        "won": outcome["won"],
        **result,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Blackjack
# ══════════════════════════════════════════════════════════════════════════════
def _hand_payload(cards: list, *, hide: bool = False) -> dict:
    """One hand, as cards the browser can lay out.

    A hidden dealer hole card is omitted rather than sent-and-hidden: a card the
    client is told not to draw is a card anybody can read out of the network tab,
    which would make counting trivial.
    """
    if hide:
        shown = cards[:1]
        total, soft = hand_value(shown)
        return {
            "cards": [_card(c) for c in shown] + [{"hidden": True}],
            "total": total,
            "soft": soft,
            "hidden": True,
        }
    total, soft = hand_value(cards)
    return {
        "cards": [_card(c) for c in cards],
        "total": total,
        "soft": soft,
        "blackjack": is_blackjack(cards),
        "bust": total > 21,
        "hidden": False,
    }


def _card(card) -> dict:
    rank, suit = card
    return {
        "rank": rank,
        "suit": suit,
        "label": card_label(card),
        "red": suit in ("♥", "♦"),
    }


async def blackjack(user_id: int, guild_id: int, bet: int) -> dict:
    """Deal a hand. A natural settles instantly; anything else is persisted."""
    await _guard(user_id, guild_id, bet)
    streak = await _take_bet(user_id, bet)

    shoe = new_shoe(BLACKJACK_DECKS)
    random.shuffle(shoe)
    player = [shoe.pop(), shoe.pop()]
    dealer = [shoe.pop(), shoe.pop()]

    if is_blackjack(player) or is_blackjack(dealer):
        return await _finish(guild_id, user_id, bet, streak, player, dealer, shoe)

    # Persist before the browser is shown a single card, exactly as the cog
    # persists before showing the buttons: the bet is already debited, so a
    # crash from here on has to be recoverable. `channel_id`/`message_id` stay
    # empty — a web hand has no message, which is the shape the cog's
    # orphaned-hand sweep already knows how to settle on restart.
    created_at = time.time()
    hand_id = await db.create_blackjack_hand(
        guild_id, 0, user_id, bet, streak, shoe, player, dealer, created_at
    )
    return {
        "game": "blackjack",
        "state": "playing",
        "hand_id": hand_id,
        "bet": bet,
        "player": _hand_payload(player),
        "dealer": _hand_payload(dealer, hide=True),
        "expires_in": BJ_TIMEOUT,
        "balance": await db.get_balance(user_id),
    }


async def blackjack_action(
    user_id: int, guild_id: int, hand_id: int, action: str
) -> dict:
    """Hit or stand on an open hand.

    A hit re-reads and re-writes the row rather than trusting anything the
    client sent, so the shoe cannot be re-dealt by replaying a request. A stand
    goes through `claim_blackjack_hand`, whose row-DELETE rowcount is the sole
    arbiter — a press racing the expiry timer settles exactly once.
    """
    if action not in ("hit", "stand"):
        raise PlayError("That isn't a move.", code="bad_action")

    row = await db.get_blackjack_hand(hand_id)
    if row is None:
        raise PlayError("That hand is already finished.", code="gone")
    if int(row["user_id"]) != int(user_id):
        raise PlayError("That isn't your hand.", code="not_yours")

    shoe, player, dealer = row["shoe"], row["player"], row["dealer"]

    if action == "hit":
        player.append(shoe.pop())
        total, _ = hand_value(player)
        if total <= 21:
            await db.update_blackjack_hand(hand_id, shoe, player, dealer)
            return {
                "game": "blackjack",
                "state": "playing",
                "hand_id": hand_id,
                "bet": row["bet"],
                "player": _hand_payload(player),
                "dealer": _hand_payload(dealer, hide=True),
                "balance": await db.get_balance(user_id),
            }
        # A bust is a stand you didn't choose: fall through and settle.

    claimed = await db.claim_blackjack_hand(hand_id)
    if claimed is None:
        raise PlayError(
            "That hand was already settled — check your balance.",
            code="already_settled",
        )
    if action == "hit":
        # The bust we just dealt: settle the cards in hand, not the stale row.
        return await _finish(
            row["guild_id"], user_id, row["bet"], row["streak"], player, dealer, shoe
        )
    while dealer_should_hit(dealer):
        dealer.append(shoe.pop())
    return await _finish(
        claimed["guild_id"],
        user_id,
        claimed["bet"],
        claimed["streak"],
        claimed["player"],
        dealer,
        shoe,
    )


async def _finish(
    guild_id: int,
    user_id: int,
    bet: int,
    streak: int,
    player: list,
    dealer: list,
    shoe: list,
) -> dict:
    """Score a finished hand and pay it out."""
    outcome = settle_blackjack(bet, player, dealer)
    result = await settle(guild_id, user_id, bet, streak, outcome["payout"])
    return {
        "game": "blackjack",
        "state": "done",
        "bet": bet,
        "outcome": outcome["outcome"],
        "won": outcome["payout"] > bet,
        "push": outcome["outcome"] == "push",
        "player": _hand_payload(player),
        "dealer": _hand_payload(dealer),
        **result,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Read-only state
# ══════════════════════════════════════════════════════════════════════════════
async def state(user_id: int, guild_id: int) -> dict:
    """The casino floor: what's playable, the limits, the jackpot, your record."""
    cfg = await db.get_casino_config(guild_id)
    econ = await db.get_econ_config(guild_id)
    stats = await db.get_casino_stats(user_id)
    balance = await db.get_balance(user_id)
    today = int(time.time() // 86400)

    challenges = []
    for chal in generate_challenges(user_id, today):
        row = await db.get_challenge_progress(user_id, today, chal["chal_key"])
        challenges.append(
            {
                "key": chal["chal_key"],
                "label": chal["label"],
                "short": challenge_short_label(chal["chal_key"]),
                "progress": min(row["progress"], chal["target"]),
                "target": chal["target"],
                "reward": chal["reward"],
                "claimed": bool(row["claimed"]),
            }
        )

    # An open hand from Discord (or a previous browser tab) is the member's, and
    # the page has to offer it back rather than silently dealing a second one
    # against the same debited bet.
    open_hand = None
    for row in await db.get_open_blackjack_hands():
        if int(row["user_id"]) == int(user_id):
            open_hand = {
                "hand_id": row["hand_id"],
                "bet": row["bet"],
                "player": _hand_payload(row["player"]),
                "dealer": _hand_payload(row["dealer"], hide=True),
                "expires_in": max(
                    0, int(BJ_TIMEOUT - (time.time() - row["created_at"]))
                ),
                "from_discord": bool(row["message_id"]),
            }
            break

    return {
        "enabled": bool(cfg["enabled"]),
        "currency": {"name": econ["currency_name"], "emoji": econ["currency_emoji"]},
        "balance": balance,
        "limits": {"min": cfg["min_bet"], "max": cfg["max_bet"]},
        "jackpot": cfg["jackpot_pool"],
        "games": _games_payload(),
        "stats": {
            **stats,
            "net": stats["won"] - stats["wagered"],
            # (position, net) — the stats card wants the position on its own.
            "rank": (await db.get_casino_rank(user_id) or (None,))[0],
            "of": await db.count_casino_players(),
        },
        "challenges": challenges,
        "open_hand": open_hand,
        "roulette_spaces": list(ROULETTE_SPACES),
        "streak": {
            "current": stats["streak"],
            "min": STREAK_MIN,
            "step": STREAK_BONUS_STEP,
            "cap": STREAK_BONUS_CAP,
        },
    }


def _games_payload() -> list[dict]:
    """What each game is, said once, so the page and the picker agree."""
    return [
        {
            "key": "flip",
            "name": "Coin Flip",
            "emoji": "🪙",
            "blurb": "Call it. Pays 1.92x.",
            "needs": "side",
        },
        {
            "key": "dice",
            "name": "Dice",
            "emoji": "🎲",
            "blurb": "2d6 against the dealer. Pays 2.1x, a tie pushes.",
            "needs": None,
        },
        {
            "key": "slots",
            "name": "Slots",
            "emoji": "🎰",
            "blurb": "Three reels. A triple 7 takes the whole jackpot.",
            "needs": None,
        },
        {
            "key": "roulette",
            "name": "Roulette",
            "emoji": "🎡",
            "blurb": "European wheel. Outside bets 2x, a straight number 35x.",
            "needs": "space",
        },
        {
            "key": "blackjack",
            "name": "Blackjack",
            "emoji": "🃏",
            "blurb": "Hit or stand. Dealer stands on 17, a natural pays 3:2.",
            "needs": None,
        },
    ]


async def leaderboard(
    *, member_ids: Optional[list[int]] = None, limit: int = 25, offset: int = 0
) -> dict:
    """Net winnings, global or filtered to a server's members."""
    from utils import helpers as h

    if member_ids is None:
        rows = await db.get_casino_leaderboard(limit=limit, offset=offset)
        total = await db.count_casino_players()
    else:
        everyone = await db.get_casino_leaderboard_for(member_ids)
        page = offset // limit + 1
        rows, page, _pages, total = h.page_rows(
            everyone, lambda r: (-(r.get("net", 0)), r["user_id"]), page, per=limit
        )
    return {
        "board": "casino",
        "label": "Casino",
        "emoji": "🎰",
        "value_key": "net",
        "total": total,
        "rows": [
            {**row, "rank": offset + i + 1, "value": row.get("net", 0)}
            for i, row in enumerate(rows)
        ],
    }


__all__: list[str] = [
    "GAMES",
    "blackjack",
    "blackjack_action",
    "dice",
    "flip",
    "leaderboard",
    "roulette",
    "slots",
    "state",
]
