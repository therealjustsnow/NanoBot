"""Ticket UI: the persistent panel button → subject modal, and the persistent
in-thread Close/Claim buttons.

All three buttons use static custom_ids (see constants.py) and resolve their
ticket from the interaction at click time, so one add_view() per view at
startup keeps every panel and every open ticket working across restarts —
no per-ticket state to restore.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from utils import helpers as h

from .constants import (
    DETAILS_MAX,
    SUBJECT_MAX,
    _CLAIM_BUTTON_CID,
    _CLOSE_BUTTON_CID,
    _PANEL_BUTTON_CID,
)

if TYPE_CHECKING:
    from .cog import Tickets


class TicketModal(discord.ui.Modal):
    """Subject + optional details, shown when a member opens a ticket."""

    def __init__(self, cog: "Tickets"):
        super().__init__(title="Open a Ticket")
        self.cog = cog
        self.subject = discord.ui.TextInput(
            label="What do you need help with?",
            placeholder="A short summary of your issue…",
            max_length=SUBJECT_MAX,
            required=True,
        )
        self.details = discord.ui.TextInput(
            label="Details (optional)",
            style=discord.TextStyle.paragraph,
            placeholder="Anything else the staff should know?",
            max_length=DETAILS_MAX,
            required=False,
        )
        self.add_item(self.subject)
        self.add_item(self.details)

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.open_ticket(
            interaction, self.subject.value.strip(), self.details.value.strip()
        )


class TicketPanelView(discord.ui.View):
    """Persistent view holding the single Open Ticket panel button."""

    def __init__(self, cog: "Tickets"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Open Ticket",
        style=discord.ButtonStyle.primary,
        emoji="🎫",
        custom_id=_PANEL_BUTTON_CID,
    )
    async def open(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.guild is None:
            return
        error = await self.cog.precheck_open(interaction)
        if error is not None:
            await interaction.response.send_message(embed=h.err(error), ephemeral=True)
            return
        await interaction.response.send_modal(TicketModal(self.cog))


class TicketThreadView(discord.ui.View):
    """Persistent Close/Claim controls posted on each ticket's opening message."""

    def __init__(self, cog: "Tickets"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Close",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id=_CLOSE_BUTTON_CID,
    )
    async def close(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self.cog.handle_close(interaction, reason=None)

    @discord.ui.button(
        label="Claim",
        style=discord.ButtonStyle.secondary,
        emoji="🙋",
        custom_id=_CLAIM_BUTTON_CID,
    )
    async def claim(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self.cog.handle_claim(interaction)
