"""cogs/inventory/views.py — inventory confirmation and gear UI.

A bulk sell empties whole stacks in one press — including rare treasure a
member may have been saving — so it goes through a preview + confirm step
instead of firing straight away. The gear picker is the mirror image: arming a
loadout was one command per buff, so it offers a multi-select and does the lot
in one press.

Both are deliberately *transient* (in memory, no persistent custom_ids, unlike
the economy's squad/raid boards): nothing is consumed or paid until the press
lands, so a restart that orphans the buttons costs nobody anything — the
command is simply run again.

The callbacks delegate to plain coroutines (`_on_confirm`/`_on_cancel`,
`_on_select`) so they can be driven directly in tests, since dpytest can't
dispatch components (the BlackjackView pattern).
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


class UseGearView(discord.ui.View):
    """Arm several consumables in one press.

    A loadout is rarely one item — bait, a luck charm and an XP booster is the
    ordinary case — and arming it was one command each, typed from memory. The
    select is multi-value for exactly that reason: pick everything, press once,
    and the reply lists what landed.

    Only one of each is used per press. A quantity is a different question
    ("burn six charms now"), and `/inventory use <item> <qty>` already answers
    it without a menu.
    """

    def __init__(
        self,
        cog: "Inventory",
        *,
        user_id: int,
        guild_id: int,
        gear: list[dict],
        timeout: float,
    ):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.user_id = user_id
        self.guild_id = guild_id
        self.message: Optional[discord.abc.Message] = None
        self.select = _UseGearSelect(gear)
        self.add_item(self.select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                embed=h.info("Run `/inventory use` yourself — this is their gear."),
                ephemeral=True,
            )
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction) -> None:
        """Arm everything picked, then repaint the list that's left.

        The menu is a snapshot, so the run is the arbiter: an item spent
        elsewhere since the card was drawn comes back as "you only have 0" on
        its own line rather than taking the rest of the press down with it.
        """
        picked = list(self.select.values)
        await interaction.response.defer()
        embed, _armed = await self.cog.run_use(self.user_id, self.guild_id, picked, 1)
        gear = await self.cog.remaining_gear(self.user_id)
        if not gear:
            self.clear_items()
            self.stop()
        else:
            self.select.repopulate(gear)
        await interaction.edit_original_response(embed=embed, view=self)

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass


class _UseGearSelect(discord.ui.Select):
    """The multi-select behind UseGearView, repopulated after every press."""

    def __init__(self, gear: list[dict]):
        super().__init__(placeholder="Pick what to use…", min_values=1)
        self.repopulate(gear)

    def repopulate(self, gear: list[dict]) -> None:
        """Rebuild the options from a fresh inventory read.

        Discord shows 25 options, so a long shelf is cut rather than rejected —
        the command with a comma-separated list is the way past that, and the
        footer says so.
        """
        self.options = [
            discord.SelectOption(
                label=f"{row['item'].name} ×{row['qty']:,}"[:100],
                value=row["item"].key,
                emoji=row["item"].emoji or None,
                description=(row["item"].description or "")[:100] or None,
            )
            for row in gear[:25]
        ]
        self.max_values = len(self.options) or 1

    async def callback(self, interaction: discord.Interaction):
        await self.view._on_select(interaction)
