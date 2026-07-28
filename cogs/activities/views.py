"""discord.ui views for the activities cog: the /adventure dashboard's
tap-to-run buttons and the encounter choice.

Both are **transient** — no persistent custom_ids, unlike the /squad and /raid
boards in cogs/economy. That is a deliberate split rather than an oversight:
those boards owe someone a payout that has to survive a restart, whereas
nothing here is owed until a button is pressed. A dashboard button that stops
working costs a member one re-run of `/adventure`, and an encounter that lapses
costs them a bonus they never banked. Neither is worth a row in SQLite.

Both are also invoker-only. The dashboard counts *your* charges and the
encounter spends *your* coins, so a second member pressing either would be
acting on someone else's screen.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Optional

import discord

from utils import helpers as h

from .constants import (
    ACTIVITY_INFO,
    ADVENTURE_VIEW_TIMEOUT,
    ENCOUNTERS,
    ENCOUNTER_TIMEOUT,
)
from .helpers import find_option

if TYPE_CHECKING:
    from .cog import Activities


# The activities the dashboard can run with one tap, in button order. /rob is
# deliberately absent: it needs a target, and a button can't ask for one — it
# stays a command with a member argument (and it is the one activity where an
# accidental tap has a victim).
BUTTON_ACTIVITIES: tuple[str, ...] = ("work", "mine", "hunt", "explore")

# What each button says. The command names differ (/mine dig, /adventure hunt),
# so the labels are the *verb*, not the command.
BUTTON_LABELS: dict[str, str] = {
    "work": "Work",
    "mine": "Dig",
    "hunt": "Hunt",
    "explore": "Explore",
}


class _ActivityButton(discord.ui.Button):
    """One tap-to-run activity on the dashboard.

    Carries its own label state — how many charges are banked, or how long
    until the next one — because that is the number a member opened the
    dashboard to find out.
    """

    def __init__(self, activity: str, ready: int, next_in: int, enabled: bool):
        label = BUTTON_LABELS[activity]
        if not enabled:
            style = discord.ButtonStyle.secondary
        elif ready > 1:
            label = f"{label} ×{ready}"
            style = discord.ButtonStyle.success
        elif ready == 1:
            style = discord.ButtonStyle.success
        else:
            label = f"{label} · {h.fmt_duration(next_in)}"
            style = discord.ButtonStyle.secondary
        super().__init__(
            label=label,
            emoji=ACTIVITY_INFO[activity]["emoji"],
            style=style,
            disabled=not enabled or ready < 1,
            row=0,
        )
        self.activity = activity

    async def callback(self, interaction: discord.Interaction):
        await self.view.press(interaction, self.activity)


class _RefreshButton(discord.ui.Button):
    """Repaint the dashboard without running anything.

    Charges tick back up while the message sits there, and the labels don't —
    so without this the only way to see a refilled bucket is to re-run the
    command, which is the friction the whole view exists to remove.
    """

    def __init__(self):
        super().__init__(
            label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=1
        )

    async def callback(self, interaction: discord.Interaction):
        await self.view.refresh(interaction)


class AdventureView(discord.ui.View):
    """The /adventure dashboard's buttons.

    The dashboard used to be a card that told you four activities were ready
    and then made you type four more commands to run them. On a phone that is
    the whole cost of the feature. Each button runs its activity through the
    same path the command does — the claim is still the atomic one in
    utils.db.activities, so a double-tap can't double-claim — then repaints the
    card in place and posts the result underneath.
    """

    def __init__(self, cog: "Activities", invoker_id: int, state: dict):
        super().__init__(timeout=ADVENTURE_VIEW_TIMEOUT)
        self.cog = cog
        self.invoker_id = invoker_id
        self.message: Optional[discord.Message] = None
        self._build(state)

    def _build(self, state: dict):
        """Lay the buttons out for a freshly-computed dashboard state."""
        self.clear_items()
        for activity in BUTTON_ACTIVITIES:
            charges = state["charges"][activity]
            self.add_item(
                _ActivityButton(
                    activity,
                    charges["ready"],
                    charges["next_in"],
                    state["enabled"][activity],
                )
            )
        self.add_item(_RefreshButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                embed=h.info("Run `/adventure` yourself — it shows your own charges."),
                ephemeral=True,
            )
            return False
        return True

    async def _repaint(self, interaction: discord.Interaction):
        """Rebuild the card and the buttons from fresh state."""
        embed, state = await self.cog.adventure_dashboard(
            interaction.guild, interaction.user
        )
        self._build(state)
        await interaction.edit_original_response(embed=embed, view=self)

    async def refresh(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self._repaint(interaction)

    async def press(self, interaction: discord.Interaction, activity: str):
        await interaction.response.defer()
        run = await self.cog.run_activity(interaction.guild, interaction.user, activity)
        # Repaint first: the card is what the member is looking at, and it now
        # holds one fewer charge whether the run paid out or was refused.
        await self._repaint(interaction)
        message = await interaction.followup.send(
            embed=run.embed, view=run.view, wait=True
        )
        if run.view is not None:
            run.view.message = message

    async def on_timeout(self):
        if self.message is None:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass


class _EncounterButton(discord.ui.Button):
    def __init__(self, option: dict):
        super().__init__(
            label=option["label"],
            emoji=option.get("emoji"),
            style=discord.ButtonStyle.primary,
        )
        self.option_key = option["key"]

    async def callback(self, interaction: discord.Interaction):
        await self.view.choose(interaction, self.option_key)


class EncounterView(discord.ui.View):
    """The follow-up choice a rare run opens.

    Single-use: `_taken` is set before the first await inside `choose`, so a
    double-tap (or a tap racing the timeout) can't resolve the same encounter
    twice and pay twice. That guard is the whole reason this isn't just two
    buttons — everything else here is presentation.

    On timeout the choice simply lapses. The alternative, auto-picking the safe
    option, would hand out coins to a member who walked away, and would make
    "do nothing" a strategy on the encounters where the safe option is the
    better expected value.
    """

    def __init__(
        self, cog: "Activities", invoker_id: int, guild_id: int, encounter_key: str
    ):
        super().__init__(timeout=ENCOUNTER_TIMEOUT)
        self.cog = cog
        self.invoker_id = invoker_id
        self.guild_id = guild_id
        self.encounter_key = encounter_key
        self.message: Optional[discord.Message] = None
        self._taken = False
        for option in ENCOUNTERS[encounter_key]["options"]:
            self.add_item(_EncounterButton(option))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                embed=h.info("That's not your encounter — go find your own."),
                ephemeral=True,
            )
            return False
        return True

    async def choose(self, interaction: discord.Interaction, option_key: str):
        if self._taken:
            return await interaction.response.send_message(
                embed=h.info("You already made your choice."), ephemeral=True
            )
        self._taken = True
        option = find_option(self.encounter_key, option_key)
        await interaction.response.defer()
        embed = await self.cog.resolve_encounter(
            interaction.guild,
            interaction.user,
            self.encounter_key,
            option_key,
            random.random(),
            random.random(),
        )
        for child in self.children:
            child.disabled = True
            child.style = (
                discord.ButtonStyle.success
                if option is not None and child.option_key == option_key
                else discord.ButtonStyle.secondary
            )
        await interaction.edit_original_response(embed=embed, view=self)
        self.stop()

    async def on_timeout(self):
        if self.message is None or self._taken:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass
