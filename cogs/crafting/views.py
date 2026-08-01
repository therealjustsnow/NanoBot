"""cogs/crafting/views.py — the craft-everything confirmation.

Crafting all is the mirror image of /inventory sell all: one press consumes
whole material stacks — ore someone may have been saving for a pickaxe, the
diamond they wanted for a treasure key — so it previews the whole plan and
crafts only when the member presses the button.

Deliberately *transient* (in memory, no persistent custom_ids, unlike the
economy's squad/raid boards): nothing is consumed until the press lands, so a
restart that orphans the buttons costs nobody anything — the command is simply
run again.

The callbacks delegate to plain coroutines (`_on_confirm`/`_on_cancel`) so they
can be driven directly in tests, since dpytest can't dispatch components (the
SellConfirmView pattern).
"""

from typing import TYPE_CHECKING, Optional

import discord

from utils import helpers as h

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .cog import Crafting


class CraftAllConfirmView(discord.ui.View):
    """Confirm/cancel for one pending craft-everything.

    The preview is a snapshot: the craft itself re-reads the inventory when
    Confirm lands, so materials spent or gained in between are accounted for
    rather than crafted from a stale list.
    """

    def __init__(
        self,
        cog: "Crafting",
        *,
        user_id: int,
        timeout: float,
    ):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.user_id = user_id
        self.message: Optional[discord.abc.Message] = None
        self._done = False

    # ── lifecycle ────────────────────────────────────────────────────────────
    def _finish(self) -> None:
        """Mark handled and grey out the buttons (single-use view)."""
        self._done = True
        for child in self.children:
            child.disabled = True
        self.cog.forget_pending_craft(self.user_id, self)
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                embed=h.err("This isn't your craft."), ephemeral=True
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
                    "Craft confirmation timed out — nothing was made.",
                    "🛠️ Cancelled",
                ),
                view=self,
            )
        except discord.HTTPException:
            pass  # message deleted or channel gone — nothing to clean up

    async def cancel_quietly(self) -> None:
        """Retire a superseded confirmation (the member ran another craft all)."""
        if self._done:
            return
        self._finish()
        if self.message is None:
            return
        try:
            await self.message.edit(
                embed=h.warn(
                    "Replaced by a newer craft confirmation — nothing was made.",
                    "🛠️ Cancelled",
                ),
                view=self,
            )
        except discord.HTTPException:
            pass

    # ── button handlers ──────────────────────────────────────────────────────
    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        if self._done:
            return await interaction.response.send_message(
                embed=h.warn("That craft is already settled."), ephemeral=True
            )
        self._finish()
        embed = await self.cog.execute_craft_all(self.user_id)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        if self._done:
            return await interaction.response.send_message(
                embed=h.warn("That craft is already settled."), ephemeral=True
            )
        self._finish()
        await interaction.response.edit_message(
            embed=h.warn("Craft cancelled — nothing was made.", "🛠️ Cancelled"),
            view=self,
        )

    @discord.ui.button(label="Craft", style=discord.ButtonStyle.success, emoji="🛠️")
    async def confirm_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._on_confirm(interaction)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._on_cancel(interaction)
