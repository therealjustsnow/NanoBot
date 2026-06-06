"""Interactive views for the admin cog."""

import discord


class ServersView(discord.ui.View):
    """Paginated ◀ ▶ navigation for the servers command."""

    def __init__(
        self,
        embeds: list[discord.Embed],
        author: discord.Member,
        index: int = 0,
    ):
        super().__init__(timeout=120)
        self.embeds = embeds
        self.author = author
        self.index = index
        self.message: discord.Message | None = None
        self._update_buttons()

    def _update_buttons(self):
        self.prev_btn.disabled = self.index == 0
        self.next_btn.disabled = self.index == len(self.embeds) - 1

    async def _edit(self, interaction: discord.Interaction):
        self._update_buttons()
        await interaction.response.edit_message(
            embed=self.embeds[self.index], view=self
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.author:
            await interaction.response.send_message(
                "Only " + self.author.display_name + " can navigate this list.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self):
        self.stop()
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass

    @discord.ui.button(
        emoji=chr(11013) + chr(65039), style=discord.ButtonStyle.secondary
    )
    async def prev_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.index -= 1
        await self._edit(interaction)

    @discord.ui.button(emoji=chr(10060), style=discord.ButtonStyle.secondary)
    async def close_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.stop()
        await interaction.message.delete()

    @discord.ui.button(
        emoji=chr(10145) + chr(65039), style=discord.ButtonStyle.secondary
    )
    async def next_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.index += 1
        await self._edit(interaction)
