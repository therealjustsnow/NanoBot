"""Gatekeeper verification UI: persistent Verify button → math-captcha modal."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

import discord

from utils import db
from utils import helpers as h

from .constants import _VERIFY_BUTTON_CID, _VERIFY_LOCKOUT_SECONDS

if TYPE_CHECKING:
    from .cog import Gatekeeper


class VerifyModal(discord.ui.Modal):
    """Modal that poses a random addition problem."""

    def __init__(self, cog: "Gatekeeper", a: int, b: int):
        super().__init__(title="Verify you're human")
        self.cog = cog
        self.answer = a + b
        self.response = discord.ui.TextInput(
            label=f"What is {a} + {b}?",
            placeholder="Type the number…",
            max_length=4,
            required=True,
        )
        self.add_item(self.response)

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.response.value.strip()
        try:
            given = int(raw)
        except ValueError:
            given = None
        if given != self.answer:
            locked_out = self.cog._register_wrong_answer(interaction.user.id)
            if locked_out:
                msg = (
                    f"That's not right, and you've had too many tries. "
                    f"Wait **{_VERIFY_LOCKOUT_SECONDS}s** before trying again."
                )
            else:
                msg = "That's not the right answer. Press **Verify** to try again."
            await interaction.response.send_message(
                embed=h.err(msg, "❌ Incorrect"),
                ephemeral=True,
            )
            return
        await self.cog.complete_verification(interaction)


class VerifyView(discord.ui.View):
    """Persistent view holding the single Verify button."""

    def __init__(self, cog: "Gatekeeper"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Verify",
        style=discord.ButtonStyle.success,
        emoji="✅",
        custom_id=_VERIFY_BUTTON_CID,
    )
    async def verify(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ):
        locked = self.cog._verify_lockout_remaining(interaction.user.id)
        if locked:
            await interaction.response.send_message(
                embed=h.warn(
                    f"Too many wrong answers. Try again in **{locked}s**.",
                    "⏳ Slow Down",
                ),
                ephemeral=True,
            )
            return
        if interaction.guild is None:
            # Button pressed in a DM — resolve the pending guild from the DB.
            pending = await self.cog._find_pending_for_user(interaction.user.id)
            if pending is None:
                await interaction.response.send_message(
                    embed=h.info("You have nothing to verify right now.", "✅ All Set"),
                    ephemeral=True,
                )
                return
        else:
            key = f"{interaction.guild.id}:{interaction.user.id}"
            if await db.get_gatekeeper_pending(key) is None:
                await interaction.response.send_message(
                    embed=h.info("You're already verified here.", "✅ All Set"),
                    ephemeral=True,
                )
                return
        a, b = random.randint(2, 9), random.randint(2, 9)
        await interaction.response.send_modal(VerifyModal(self.cog, a, b))
