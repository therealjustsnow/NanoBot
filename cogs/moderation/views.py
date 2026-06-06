"""Interactive views for the moderation cog."""

import discord


class NukeConfirm(discord.ui.View):
    """Ephemeral confirm/cancel buttons for /nuke. Times out after 30 s."""

    def __init__(self, author: discord.Member):
        super().__init__(timeout=30)
        self.author = author
        self.outcome: bool | None = None
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.author:
            await interaction.response.send_message(
                "That's not your nuke to confirm.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="💥 Nuke it", style=discord.ButtonStyle.danger)
    async def confirm_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.outcome = True
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(description="💥 Nuking…", color=0xED4245), view=None
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.outcome = False
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(description="✅ Nuke cancelled.", color=0x57F287),
            view=None,
        )
