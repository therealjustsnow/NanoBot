"""cogs/inventory/views.py — inventory confirmation UI.

A bulk sell empties whole stacks in one press — including rare treasure a
member may have been saving — so it goes through a preview + confirm step
instead of firing straight away. The view is deliberately *transient* (in
memory, no persistent custom_ids, unlike the economy's squad/raid boards):
nothing is consumed or paid until Confirm, so a restart that orphans the
buttons costs nobody anything — the command is simply run again.

The button callbacks delegate to plain `_on_confirm`/`_on_cancel` coroutines so
they can be driven directly in tests (the BlackjackView pattern).
"""

from typing import TYPE_CHECKING, Optional

import discord

from utils import helpers as h

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .cog import Inventory


class SellConfirmView(discord.ui.View):
    """Confirm/cancel for one pending bulk sell.

    The preview is a snapshot: the sale itself re-reads the inventory when
    Confirm lands, so anything spent or gained in between is accounted for
    rather than sold from a stale list.
    """

    def __init__(
        self,
        cog: "Inventory",
        *,
        user_id: int,
        guild_id: int,
        category: Optional[str],
        qty: Optional[int],
        timeout: float,
    ):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.user_id = user_id
        self.guild_id = guild_id
        self.category = category
        self.qty = qty
        self.message: Optional[discord.abc.Message] = None
        self._done = False

    # ── lifecycle ────────────────────────────────────────────────────────────
    def _finish(self) -> None:
        """Mark handled and grey out the buttons (single-use view)."""
        self._done = True
        for child in self.children:
            child.disabled = True
        self.cog.forget_pending_sell(self.user_id, self)
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                embed=h.err("This isn't your sale."), ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        if self._done:
            return
        self._finish()
        if self.message is None:
            return
        try:
            await self.message.edit(
                embed=h.warn(
                    "Sale confirmation timed out — nothing was sold.", "🎒 Cancelled"
                ),
                view=self,
            )
        except discord.HTTPException:
            pass  # message deleted or channel gone — nothing to clean up

    async def cancel_quietly(self) -> None:
        """Retire a superseded confirmation (the member ran another bulk sell)."""
        if self._done:
            return
        self._finish()
        if self.message is None:
            return
        try:
            await self.message.edit(
                embed=h.warn(
                    "Replaced by a newer sell confirmation — nothing was sold.",
                    "🎒 Cancelled",
                ),
                view=self,
            )
        except discord.HTTPException:
            pass

    # ── button handlers ──────────────────────────────────────────────────────
    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        if self._done:
            return await interaction.response.send_message(
                embed=h.warn("That sale is already settled."), ephemeral=True
            )
        self._finish()
        embed = await self.cog.execute_bulk_sell(
            self.user_id, self.guild_id, self.category, self.qty
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        if self._done:
            return await interaction.response.send_message(
                embed=h.warn("That sale is already settled."), ephemeral=True
            )
        self._finish()
        await interaction.response.edit_message(
            embed=h.warn("Sale cancelled — nothing was sold.", "🎒 Cancelled"),
            view=self,
        )

    @discord.ui.button(label="Sell", style=discord.ButtonStyle.success, emoji="💰")
    async def confirm_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._on_confirm(interaction)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._on_cancel(interaction)
