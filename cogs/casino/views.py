"""discord.ui view for the interactive /casino blackjack hand.

Persistent (timeout=None) with stable per-hand custom_ids (`bj:{hand_id}:hit`
/ `bj:{hand_id}:stand`) so a restart can re-register the view against its
message instead of orphaning it — the open hand (bet already debited) is
mirrored in SQLite (utils/db casino_blackjack) and updated on every hit, and
a cog-owned timer auto-stands it after BJ_TIMEOUT (the economy /raid pattern).
Calls back into the cog via a TYPE_CHECKING hint.
"""

import logging
import time
from typing import TYPE_CHECKING, Callable, Optional

import discord

from utils import db
from utils import helpers as h

from .helpers import card_label, dealer_should_hit, hand_value

if TYPE_CHECKING:
    from .cog import Casino

log = logging.getLogger("NanoBot.casino")


def _bj_cid(hand_id: int, action: str) -> str:
    return f"bj:{hand_id}:{action}"


def _parse_bj_cid(custom_id: str) -> Optional[tuple[int, str]]:
    parts = custom_id.split(":")
    if len(parts) != 3 or parts[0] != "bj":
        return None
    try:
        return int(parts[1]), parts[2]
    except ValueError:
        return None


def _hand_line(cards: list) -> str:
    value, soft = hand_value(cards)
    label = " ".join(card_label(c) for c in cards)
    soft_tag = " (soft)" if soft and value <= 21 else ""
    return f"{label} — **{value}**{soft_tag}"


def _bj_embed(
    *,
    player_cards: list,
    dealer_cards: list,
    hide_dealer: bool,
    title: str,
    color: int,
    footer: str,
) -> discord.Embed:
    e = h.embed(title, color=color)
    e.add_field(name="Your hand", value=_hand_line(player_cards), inline=False)
    if hide_dealer:
        e.add_field(
            name="Dealer shows",
            value=f"{card_label(dealer_cards[0])} ??",
            inline=False,
        )
    else:
        e.add_field(name="Dealer hand", value=_hand_line(dealer_cards), inline=False)
    e.set_footer(text=footer)
    return e


class BjButton(discord.ui.Button):
    """A Hit/Stand button carrying a per-hand custom_id so it survives restarts."""

    def __init__(
        self,
        hand_id: int,
        action: str,
        label: str,
        style: discord.ButtonStyle,
        emoji: str,
        handler: Callable,
    ):
        super().__init__(
            label=label, style=style, emoji=emoji, custom_id=_bj_cid(hand_id, action)
        )
        self._handler = handler

    async def callback(self, interaction: discord.Interaction):
        await self._handler(interaction)


class BlackjackView(discord.ui.View):
    """Hit/Stand controls for one /casino blackjack hand.

    The bet is already debited and the hand persisted by the time this view is
    shown. Auto-stands via the cog's expiry timer (dealer plays out, hand
    settles) so a bet never dangles forever — including across a restart,
    since on_restore_schedules rebuilds this view from the persisted row and
    re-arms the timer relative to the original deal time.
    """

    def __init__(
        self,
        cog: "Casino",
        *,
        hand_id: int,
        guild_id: int,
        user_id: int,
        bet: int,
        shoe: list,
        player_cards: list,
        dealer_cards: list,
        econ: dict,
        streak: int,
        created_at: Optional[float] = None,
    ):
        super().__init__(timeout=None)
        self.cog = cog
        self.hand_id = hand_id
        self.guild_id = guild_id
        self.user_id = user_id
        self.bet = bet
        self.shoe = shoe
        self.player_cards = player_cards
        self.dealer_cards = dealer_cards
        self.econ = econ
        self.streak = streak
        self.created_at = created_at if created_at is not None else time.time()
        self.message: discord.abc.Message | None = None
        self._done = False
        for action, label, style, emoji, handler in (
            ("hit", "Hit", discord.ButtonStyle.primary, "🃏", self._on_hit),
            ("stand", "Stand", discord.ButtonStyle.secondary, "✋", self._on_stand),
        ):
            self.add_item(BjButton(hand_id, action, label, style, emoji, handler))

    def render(self) -> discord.Embed:
        return _bj_embed(
            player_cards=self.player_cards,
            dealer_cards=self.dealer_cards,
            hide_dealer=True,
            title="🃏 Blackjack",
            color=h.BLUE,
            footer=f"Bet: {self.bet:,} {self.econ['currency_name']} · Hit or Stand",
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                embed=h.err("This isn't your hand."), ephemeral=True
            )
            return False
        return True

    async def _on_hit(self, interaction: discord.Interaction):
        if self._done:
            return await interaction.response.send_message(
                embed=h.warn("This hand is already settled."), ephemeral=True
            )
        self.player_cards.append(self.shoe.pop())
        await db.update_blackjack_hand(
            self.hand_id, self.shoe, self.player_cards, self.dealer_cards
        )
        value, _soft = hand_value(self.player_cards)
        if value >= 21:
            await self._settle(interaction)
        else:
            await interaction.response.edit_message(embed=self.render(), view=self)

    async def _on_stand(self, interaction: discord.Interaction):
        if self._done:
            return await interaction.response.send_message(
                embed=h.warn("This hand is already settled."), ephemeral=True
            )
        await self._settle(interaction)

    async def expire(self):
        """Auto-stand a hand that's sat too long — called by the cog's timer."""
        if self._done:
            return
        await self._settle(None)

    async def _settle(self, interaction: discord.Interaction | None):
        if self._done:
            if interaction is not None:
                await interaction.response.send_message(
                    embed=h.warn("This hand is already settled."), ephemeral=True
                )
            return

        # Claim the row first: a button press, the expiry timer, and a
        # restart-restored expiry can all reach here for the same hand. Only
        # the caller whose claim actually deletes the row may pay out.
        claimed = await db.claim_blackjack_hand(self.hand_id)
        if claimed is None:
            self._done = True
            for child in self.children:
                child.disabled = True
            self.stop()
            if interaction is not None:
                await interaction.response.send_message(
                    embed=h.warn("This hand is already settled."), ephemeral=True
                )
            return

        self._done = True
        for child in self.children:
            child.disabled = True

        while dealer_should_hit(self.dealer_cards):
            self.dealer_cards.append(self.shoe.pop())

        result = await self.cog.settle_blackjack_hand(
            self.guild_id,
            self.user_id,
            self.bet,
            self.streak,
            self.player_cards,
            self.dealer_cards,
        )
        embed = _bj_embed(
            player_cards=self.player_cards,
            dealer_cards=self.dealer_cards,
            hide_dealer=False,
            title=result["title"],
            color=result["color"],
            footer=result["footer"],
        )
        self.stop()
        if interaction is not None:
            await interaction.response.edit_message(embed=embed, view=self)
        elif self.message is not None:
            try:
                await self.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                log.debug("blackjack expiry edit failed (message gone)", exc_info=True)
        await self.cog._end_blackjack(self.hand_id)
