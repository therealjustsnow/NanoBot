"""Birthday timezone-picker UI: the /birthday timezone dropdown view."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from utils import db
from utils import helpers as h

from .constants import _TZ_CHOICES

if TYPE_CHECKING:
    from .cog import Birthday


class TimezoneView(discord.ui.View):
    """Dropdown of common timezones for /birthday timezone (no arg)."""

    def __init__(self, cog: "Birthday", author_id: int, guild_id: int):
        super().__init__(timeout=120)
        self.cog = cog
        self.author_id = author_id
        self.guild_id = guild_id
        self.add_item(_TimezoneSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the person who opened this menu can use it.", ephemeral=True
            )
            return False
        return True


class _TimezoneSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=label[:100], value=iana, emoji=emoji)
            for label, iana, emoji in _TZ_CHOICES
        ]
        super().__init__(placeholder="Pick your server's timezone…", options=options)

    async def callback(self, interaction: discord.Interaction):
        view: TimezoneView = self.view  # type: ignore[assignment]
        tz = self.values[0]
        await db.set_birthday_config(view.guild_id, timezone=tz)
        view.cog._tz_cache.pop(tz, None)
        for child in view.children:
            child.disabled = True
        await interaction.response.edit_message(
            embed=h.ok(f"Timezone set to **{tz}**.", "🕐 Timezone Set"),
            view=view,
        )
        view.stop()
