"""discord.ui view for the interactive /casino blackjack hand.

Transient, in-memory only — no persistence across restarts (a lightweight
minigame; unlike /squad or /raid there's no queued payout to lose, just an
in-progress hand). Calls back into the cog via a TYPE_CHECKING hint.
"""

import logging
from typing import TYPE_CHECKING

import discord

from utils import helpers as h

from .constants import BLACKJACK_TIMEOUT
from .helpers import card_label, dealer_should_hit, hand_value

if TYPE_CHECKING:
    from .cog import Casino

log = logging.getLogger("NanoBot.casino")


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


class BlackjackView(discord.ui.View):
    """Hit/Stand controls for one /casino blackjack hand.

    The bet is already debited by the time this view is shown. Auto-stands on
    timeout (dealer plays out, hand settles) so a bet never dangles forever.
    """

    def __init__(
        self,
        cog: "Casino",
        *,
        guild_id: int,
        user_id: int,
        bet: int,
        shoe: list,
        player_cards: list,
        dealer_cards: list,
        econ: dict,
        streak: int,
    ):
        super().__init__(timeout=BLACKJACK_TIMEOUT)
        self.cog = cog
        self.guild_id = guild_id
        self.user_id = user_id
        self.bet = bet
        self.shoe = shoe
        self.player_cards = player_cards
        self.dealer_cards = dealer_cards
        self.econ = econ
        self.streak = streak
        self.message: discord.Message | None = None
        self._done = False

    def render(self) -> discord.Embed:
        return _bj_embed(
            player_cards=self.player_cards,
            dealer_cards=self.dealer_cards,
            hide_dealer=True,
            title="🃏 Blackjack",
            color=h.BLUE,
            footer=f"Bet: {self.bet:,} {self.econ['currency_name']} · Hit or Stand",
        )

    async def _deny(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=h.err("This isn't your hand."), ephemeral=True
        )

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary, emoji="🃏")
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await self._deny(interaction)
        self.player_cards.append(self.shoe.pop())
        value, _soft = hand_value(self.player_cards)
        if value >= 21:
            await self._settle(interaction)
        else:
            await interaction.response.edit_message(embed=self.render(), view=self)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary, emoji="✋")
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await self._deny(interaction)
        await self._settle(interaction)

    async def on_timeout(self):
        if self._done:
            return
        await self._settle(None)

    async def _settle(self, interaction: discord.Interaction | None):
        if self._done:
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
                log.debug("blackjack timeout edit failed (message gone)", exc_info=True)
