"""cogs/identity/views.py — the tap-to-dress cosmetic picker.

A loadout is six slots and the badge showcase holds six of its own, so dressing
a card was up to a dozen invocations of `/profile equip <name>` — each one a
name typed or tapped out of an autocomplete, one at a time. The select here is
multi-value for that reason: pick the whole look, press once.

Transient by design, like every other browse-and-choose view in the bot (no
persistent custom_ids, unlike the economy's squad/raid boards). A cosmetic is
already owned before it can be worn, so nothing is owed by a press and a
restart that orphans the menu costs one re-run of the command.

The callback delegates to a plain `_on_select` coroutine so it can be driven
directly in tests — dpytest cannot dispatch components (the SellConfirmView
pattern).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import discord

from utils import helpers as h

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .cog import Identity


class _CosmeticSelect(discord.ui.Select):
    """The multi-select itself, rebuilt from fresh rows after every press."""

    def __init__(self, mode: str, rows: list[dict]):
        super().__init__(
            placeholder=(
                "Pick what to wear…" if mode == "equip" else "Pick what to take off…"
            ),
            min_values=1,
        )
        self.repopulate(rows)

    def repopulate(self, rows: list[dict]) -> None:
        self.options = [
            discord.SelectOption(
                label=row["label"][:100],
                value=row["key"],
                description=row.get("description"),
                emoji=row.get("emoji"),
            )
            for row in rows[:25]
        ]
        self.max_values = len(self.options) or 1

    async def callback(self, interaction: discord.Interaction):
        await self.view._on_select(interaction)


class CosmeticPickerView(discord.ui.View):
    """Equip or unequip several cosmetics in one press.

    One class serves both directions because they are the same screen with the
    list inverted — what you can put on, or what is already on — and a member
    who has just filled a showcase is the most likely person to want one back
    off again.
    """

    def __init__(
        self,
        cog: "Identity",
        *,
        user_id: int,
        mode: str,
        rows: list[dict],
        timeout: float,
    ):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.user_id = user_id
        self.mode = mode
        self.message: Optional[discord.abc.Message] = None
        self.select = _CosmeticSelect(mode, rows)
        self.add_item(self.select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                embed=h.info("Run `/profile equip` yourself — this is their card."),
                ephemeral=True,
            )
            return False
        return True

    async def _on_select(self, interaction: discord.Interaction) -> None:
        """Apply every pick, then repaint the list that's left.

        The menu is a snapshot, so the run is the arbiter: a slot that filled up
        between the card being drawn and the press comes back as a "full" line
        of its own rather than taking the other picks down with it.
        """
        picked = list(self.select.values)
        await interaction.response.defer()
        if self.mode == "equip":
            embed, _changed = await self.cog.run_equip(self.user_id, picked)
        else:
            embed, _changed = await self.cog.run_unequip(self.user_id, picked)
        rows = await self.cog.picker_rows(self.user_id, self.mode)
        if not rows:
            self.clear_items()
            self.stop()
        else:
            self.select.repopulate(rows)
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
