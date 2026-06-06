"""Interactive views for the fun cog: Would-You-Rather voting and Rock-Paper-Scissors."""

import random
import time

import discord

from utils import helpers as h

from .actions import _RPS_CHOICES, _RPS_WINS, _RPS_COLOR


class WyrView(discord.ui.View):
    """Two-button vote view for Would You Rather. Edits itself on expiry."""

    def __init__(self, option_a: str, option_b: str, duration: int = 3600):
        super().__init__(timeout=duration)
        self.option_a = option_a
        self.option_b = option_b
        self.votes: dict[int, str] = {}
        self.ended = False
        self.message: discord.Message | None = None
        self.end_ts = int(time.time() + duration)

    def _tally(self) -> tuple[int, int]:
        a = sum(1 for v in self.votes.values() if v == "A")
        b = sum(1 for v in self.votes.values() if v == "B")
        return a, b

    def _results_embed(self) -> discord.Embed:
        a, b = self._tally()
        total = a + b
        pct_a = round(a / total * 100) if total else 0
        pct_b = 100 - pct_a if total else 0
        bar_a = "\u2593" * round(pct_a / 10) + "\u2591" * (10 - round(pct_a / 10))
        bar_b = "\u2593" * round(pct_b / 10) + "\u2591" * (10 - round(pct_b / 10))
        e = discord.Embed(
            title="\U0001f914 Would You Rather -- Results!",
            color=0x5865F2,
        )
        e.add_field(
            name=f"\U0001f1e6 {self.option_a}",
            value=f"{bar_a} **{pct_a}%** ({a} vote{'s' if a != 1 else ''})",
            inline=False,
        )
        e.add_field(
            name=f"\U0001f1e7 {self.option_b}",
            value=f"{bar_b} **{pct_b}%** ({b} vote{'s' if b != 1 else ''})",
            inline=False,
        )
        e.set_footer(
            text=f"NanoBot Fun \u00b7 {total} total vote{'s' if total != 1 else ''}"
        )
        return e

    def _voting_embed(self) -> discord.Embed:
        total = len(self.votes)
        e = discord.Embed(
            title="\U0001f914 Would You Rather...",
            color=0x5865F2,
        )
        e.add_field(name="\U0001f1e6", value=self.option_a, inline=False)
        e.add_field(name="\U0001f1e7", value=self.option_b, inline=False)
        e.add_field(
            name="",
            value=f"\U0001f4ca {total} vote{'s' if total != 1 else ''} so far \u00b7 Results <t:{self.end_ts}:R>",
            inline=False,
        )
        e.set_footer(text="NanoBot Fun \u00b7 Tap a button to vote!")
        return e

    async def on_timeout(self):
        self.ended = True
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(embed=self._results_embed(), view=self)
            except discord.HTTPException:
                pass

    async def _handle_vote(self, interaction: discord.Interaction, choice: str):
        if self.ended:
            return await interaction.response.send_message(
                "Voting has ended!", ephemeral=True
            )
        uid = interaction.user.id
        previous = self.votes.get(uid)
        if previous == choice:
            return await interaction.response.send_message(
                f"You already voted for **{self.option_a if choice == 'A' else self.option_b}**!",
                ephemeral=True,
            )
        self.votes[uid] = choice
        label = self.option_a if choice == "A" else self.option_b
        if previous:
            msg = f"Changed your vote to **{label}**!"
        else:
            msg = f"Voted for **{label}**!"
        await interaction.response.send_message(msg, ephemeral=True)
        try:
            await interaction.message.edit(embed=self._voting_embed())
        except discord.HTTPException:
            pass

    @discord.ui.button(
        label="Option A", style=discord.ButtonStyle.blurple, emoji="\U0001f1e6"
    )
    async def btn_a(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_vote(interaction, "A")

    @discord.ui.button(
        label="Option B", style=discord.ButtonStyle.blurple, emoji="\U0001f1e7"
    )
    async def btn_b(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_vote(interaction, "B")


# ── RPS view ─────────────────────────────────────────────────────────────────
class RpsView(discord.ui.View):
    """Three-button Rock Paper Scissors view. Handles PvP and PvBot."""

    def __init__(
        self,
        challenger: discord.Member,
        opponent: discord.Member | None,
        *,
        is_bot: bool = False,
    ):
        super().__init__(timeout=30)
        self.challenger = challenger
        self.opponent = opponent  # None means vs bot
        self.is_bot = is_bot
        self.choices: dict[int, str] = {}  # user_id -> choice
        self.ended = False
        self.message: discord.Message | None = None

    def _result_embed(self) -> discord.Embed:
        """Build the final results embed."""
        c_choice = self.choices.get(self.challenger.id, "rock")
        if self.is_bot:
            o_choice = random.choice(list(_RPS_CHOICES.keys()))
            o_name = "NanoBot"
        else:
            o_choice = self.choices.get(self.opponent.id, "rock")
            o_name = self.opponent.display_name

        c_emoji = _RPS_CHOICES[c_choice]
        o_emoji = _RPS_CHOICES[o_choice]

        if c_choice == o_choice:
            result = "It's a tie! \U0001f91d"
            color = h.YELLOW
        elif _RPS_WINS[c_choice] == o_choice:
            result = f"**{self.challenger.display_name}** wins! \U0001f389"
            color = h.GREEN
        else:
            result = f"**{o_name}** wins! \U0001f389"
            color = h.RED

        e = discord.Embed(
            title="\u270a\u270b\u2702\ufe0f Rock Paper Scissors",
            color=color,
        )
        e.add_field(
            name=self.challenger.display_name,
            value=f"{c_emoji} {c_choice.capitalize()}",
            inline=True,
        )
        e.add_field(name="vs", value="\u200b", inline=True)
        e.add_field(
            name=o_name,
            value=f"{o_emoji} {o_choice.capitalize()}",
            inline=True,
        )
        e.add_field(name="", value=result, inline=False)
        e.set_footer(text="NanoBot Fun")
        return e

    def _waiting_embed(self) -> discord.Embed:
        """Embed shown while waiting for picks."""
        picked = len(self.choices)
        if self.is_bot:
            desc = f"{self.challenger.mention} vs **NanoBot** -- pick your move!"
        else:
            desc = (
                f"{self.challenger.mention} vs {self.opponent.mention} -- "
                f"pick your moves!\n\n"
                f"\U0001f4e5 {picked}/2 players have chosen"
            )
        e = discord.Embed(
            title="\u270a\u270b\u2702\ufe0f Rock Paper Scissors",
            description=desc,
            color=_RPS_COLOR,
        )
        e.set_footer(text="NanoBot Fun \u00b7 Tap a button!")
        return e

    async def _handle_pick(self, interaction: discord.Interaction, choice: str):
        if self.ended:
            return await interaction.response.send_message(
                "This game is over!", ephemeral=True
            )
        uid = interaction.user.id

        # Only allowed players can pick
        allowed = {self.challenger.id}
        if self.opponent and not self.is_bot:
            allowed.add(self.opponent.id)
        if uid not in allowed:
            return await interaction.response.send_message(
                "This isn't your game! Start your own with `/fun rps` or `!rps`.",
                ephemeral=True,
            )

        # Already picked
        if uid in self.choices:
            return await interaction.response.send_message(
                f"You already picked **{self.choices[uid].capitalize()}**!",
                ephemeral=True,
            )

        self.choices[uid] = choice

        # PvBot: reveal immediately
        if self.is_bot:
            self.ended = True
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(
                embed=self._result_embed(), view=self
            )
            self.stop()
            return

        # PvP: check if both have picked
        await interaction.response.send_message(
            f"You picked **{choice.capitalize()}**! Waiting on your opponent...",
            ephemeral=True,
        )
        if len(self.choices) >= 2:
            self.ended = True
            for item in self.children:
                item.disabled = True
            try:
                await self.message.edit(embed=self._result_embed(), view=self)
            except discord.HTTPException:
                pass
            self.stop()
        else:
            try:
                await self.message.edit(embed=self._waiting_embed())
            except discord.HTTPException:
                pass

    async def on_timeout(self):
        if self.ended:
            return
        self.ended = True
        for item in self.children:
            item.disabled = True

        # Figure out who didn't pick
        if self.is_bot:
            desc = "Time's up! Nobody picked."
        elif len(self.choices) == 0:
            desc = "Time's up! Nobody picked."
        elif len(self.choices) == 1:
            picker_id = next(iter(self.choices))
            if self.opponent and picker_id != self.opponent.id:
                desc = f"Time's up! {self.opponent.mention} didn't pick."
            else:
                desc = f"Time's up! {self.challenger.mention} didn't pick."
        else:
            desc = "Time's up!"

        e = discord.Embed(
            title="\u270a\u270b\u2702\ufe0f Rock Paper Scissors",
            description=desc,
            color=h.YELLOW,
        )
        e.set_footer(text="NanoBot Fun")
        if self.message:
            try:
                await self.message.edit(embed=e, view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Rock", style=discord.ButtonStyle.blurple, emoji="\u270a")
    async def btn_rock(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._handle_pick(interaction, "rock")

    @discord.ui.button(label="Paper", style=discord.ButtonStyle.blurple, emoji="\u270b")
    async def btn_paper(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._handle_pick(interaction, "paper")

    @discord.ui.button(
        label="Scissors", style=discord.ButtonStyle.blurple, emoji="\u2702\ufe0f"
    )
    async def btn_scissors(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._handle_pick(interaction, "scissors")
