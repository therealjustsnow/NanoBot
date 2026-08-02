"""Interactive views for the fun cog: Would-You-Rather voting and Rock-Paper-Scissors."""

import random
import time
from typing import TYPE_CHECKING, Optional

import discord

from utils import db
from utils import helpers as h

from .actions import _RPS_CHOICES, _RPS_WINS, _RPS_COLOR

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, typing only
    from .cog import Fun


def _wyr_cid(poll_id: int, choice: str) -> str:
    return f"wyr:{poll_id}:{choice}"


class WyrButton(discord.ui.Button):
    """A vote button carrying a per-poll custom_id so it survives restarts."""

    def __init__(self, poll_id: int, choice: str, emoji: str):
        super().__init__(
            label=f"Option {choice}",
            style=discord.ButtonStyle.blurple,
            emoji=emoji,
            custom_id=_wyr_cid(poll_id, choice),
        )
        self.choice = choice

    async def callback(self, interaction: discord.Interaction):
        await self.view._handle_vote(interaction, self.choice)


class WyrView(discord.ui.View):
    """Two-button vote view for Would You Rather.

    A poll runs for up to 24 hours, so it long outlives the average gap between
    restarts — which is why nothing here is held in the process. Every vote is
    written to `wyr_polls` (utils/db/polls.py), the buttons carry per-poll
    custom_ids so they are still live after a restart, and the closing time is a
    timestamp the cog re-arms a timer against rather than a `View` timeout that
    dies with the process. The view is persistent (timeout=None) for exactly
    that reason: the cog owns the clock, and `finish()` is the one path that
    announces a winner, whether it is reached by the timer or by a late click.
    """

    def __init__(
        self,
        cog: "Fun",
        poll_id: int,
        option_a: str,
        option_b: str,
        end_ts: float,
        votes: Optional[dict[int, str]] = None,
    ):
        super().__init__(timeout=None)
        self.cog = cog
        self.poll_id = poll_id
        self.option_a = option_a
        self.option_b = option_b
        self.votes: dict[int, str] = dict(votes or {})
        self.ended = False
        self.message: discord.abc.Message | None = None
        self.end_ts = int(end_ts)
        for choice, emoji in (("A", "\U0001f1e6"), ("B", "\U0001f1e7")):
            self.add_item(WyrButton(poll_id, choice, emoji))

    def _expired(self) -> bool:
        return time.time() >= self.end_ts

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
        if total == 0:
            verdict = "Nobody voted!"
        elif a > b:
            verdict = f"\U0001f3c6 **{self.option_a}** wins!"
        elif b > a:
            verdict = f"\U0001f3c6 **{self.option_b}** wins!"
        else:
            verdict = "\U0001f91d It's a tie!"
        e = discord.Embed(
            title="\U0001f914 Would You Rather -- Results!",
            description=verdict,
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

    async def finish(self):
        """Close voting: announce the winner, disable the buttons, drop the row.

        The one place results are published, so a poll that ends by its timer and
        one that ends because somebody clicked a minute late read identically.
        Idempotent — the cog's timer and a late click can both reach it.
        """
        if self.ended:
            return
        self.ended = True
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(embed=self._results_embed(), view=self)
            except discord.HTTPException:
                # A deleted message costs the announcement, never the cleanup.
                pass
        await self.cog._end_poll(self.poll_id)
        self.stop()

    async def _handle_vote(self, interaction: discord.Interaction, choice: str):
        if self.ended:
            return await interaction.response.send_message(
                "Voting has ended!", ephemeral=True
            )
        if self._expired():
            # The clock beat the timer here (a restart during the poll, a very
            # late click). Close it now rather than counting a vote the results
            # would have to contradict.
            await interaction.response.send_message("Voting has ended!", ephemeral=True)
            return await self.finish()
        uid = interaction.user.id
        previous = self.votes.get(uid)
        if previous == choice:
            return await interaction.response.send_message(
                f"You already voted for **{self.option_a if choice == 'A' else self.option_b}**!",
                ephemeral=True,
            )
        self.votes[uid] = choice
        await db.set_wyr_votes(self.poll_id, self.votes)
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
