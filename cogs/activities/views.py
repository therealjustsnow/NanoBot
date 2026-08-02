"""discord.ui views for the activities cog: the /adventure dashboard's
tap-to-run buttons, the card those same buttons ride under an individual
command's result, and the encounter choice.

All three are **transient** — no persistent custom_ids, unlike the /squad and
/raid boards in cogs/economy. That is a deliberate split rather than an
oversight: those boards owe someone a payout that has to survive a restart,
whereas nothing here is owed until a button is pressed. A dashboard button that
stops working costs a member one re-run of `/adventure`, and an encounter that
lapses costs them a bonus they never banked. Neither is worth a row in SQLite.

They are also invoker-only. The dashboard counts *your* charges and the
encounter spends *your* coins, so a second member pressing either would be
acting on someone else's screen.

Two cards, one set of buttons
─────────────────────────────
`AdventureView` is the board `/adventure` opens: no result of its own, so a
press repaints the card and posts what happened *underneath* it, as a log.
`RunResultView` is what an individual command (`/work`, `/mine dig`, …) replies
with: it already has a result on top, so a press replaces that result in place
rather than starting a new message every three hours. Same buttons, same run
path — the difference is only whether the card has a slot for an outcome.
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

# Where an encounter's options sit on a card that also carries the dashboard.
# Rows 0 and 1 are the activity buttons and the collect/refresh pair.
ENCOUNTER_ROW = 2


def _view_kwargs(view: Optional[discord.ui.View]) -> dict:
    """`view=` for a followup send, omitted entirely when there is no view.

    Every other send path in the bot takes `view=None` happily — `Context.send`
    and `Messageable.send` both default it to None — so handing a run's optional
    view straight to `followup.send` read as correct and was not. A webhook
    followup type-checks the argument and raises `TypeError` on None, which is
    the bug that made a dashboard press claim the charge, run the activity, and
    then show the member nothing at all: the raise landed *after* the payout,
    on the one message that was supposed to report it.
    """
    return {} if view is None else {"view": view}


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


class _CollectButton(discord.ui.Button):
    """Empty every bucket in one press.

    With caps sized to half a day, a member who turns up twice a day arrives to
    a dozen banked runs — and a dozen taps is its own kind of chore. This is
    the button for people who came to collect rather than to play; the
    individual ones stay for anyone who wants to run just the one thing.
    """

    def __init__(self, banked: int):
        super().__init__(
            label=f"Collect all ({banked})" if banked else "Collect all",
            emoji="📥",
            style=(
                discord.ButtonStyle.primary if banked else discord.ButtonStyle.secondary
            ),
            disabled=not banked,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        await self.view.collect(interaction)


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


class _EncounterButton(discord.ui.Button):
    def __init__(self, option: dict, row: Optional[int] = None):
        super().__init__(
            label=option["label"],
            emoji=option.get("emoji"),
            style=discord.ButtonStyle.primary,
            row=row,
        )
        self.option_key = option["key"]

    async def callback(self, interaction: discord.Interaction):
        await self.view.choose(interaction, self.option_key)


class _EncounterHost:
    """The encounter half of a view: its option buttons, and resolving one.

    Shared by the standalone `EncounterView` and by `RunResultView`, which
    carries an encounter on row 2 under the dashboard buttons. Chaining lives
    here too: an outcome naming a `next` stage re-arms the host with that stage
    instead of ending the encounter, which is what lets one decision open
    another without a second view, a second message, or a second code path.

    Single-use per stage: `_taken` is set before the first await inside
    `choose`, so a double-tap (or a tap racing the timeout) can't resolve the
    same stage twice and pay twice. That guard is the whole reason this isn't
    just a row of buttons — there is no database row to check against.
    """

    cog: "Activities"
    encounter_key: Optional[str]

    def _arm_encounter(self, encounter_key: Optional[str]):
        """Point the host at an encounter (or at nothing) and unlock it."""
        self.encounter_key = encounter_key if encounter_key in ENCOUNTERS else None
        self._taken = False

    def _mount_encounter(self, row: Optional[int] = None):
        """Add the current encounter's option buttons, if there is one."""
        encounter = ENCOUNTERS.get(self.encounter_key or "")
        if encounter is None:
            return
        for option in encounter["options"]:
            self.add_item(_EncounterButton(option, row=row))

    def _settle_encounter(self, chosen: str):
        """Grey the options out, marking the one that was taken."""
        for child in self.children:
            if isinstance(child, _EncounterButton):
                child.disabled = True
                child.style = (
                    discord.ButtonStyle.success
                    if child.option_key == chosen
                    else discord.ButtonStyle.secondary
                )

    async def choose(self, interaction: discord.Interaction, option_key: str):
        if self.encounter_key is None or self._taken:
            return await interaction.response.send_message(
                embed=h.info("You already made your choice."), ephemeral=True
            )
        self._taken = True
        await interaction.response.defer()
        step = await self.cog.resolve_encounter(
            interaction.guild,
            interaction.user,
            self.encounter_key,
            option_key,
            random.random(),
            random.random(),
        )
        # Re-arm *before* the subclass repaints, so a rebuild picks up the
        # follow-up stage's buttons rather than the ones just answered.
        self._arm_encounter(step.next_key)
        await self._show_encounter(interaction, step, option_key)

    async def _show_encounter(self, interaction, step, chosen: str):
        """Put the answered stage on screen. Each card does this its own way —
        one has a result slot to drop the outcome into, the other is the
        outcome — so there is no sensible default."""
        raise NotImplementedError


class AdventureView(_EncounterHost, discord.ui.View):
    """The /adventure dashboard's buttons.

    The dashboard used to be a card that told you four activities were ready
    and then made you type four more commands to run them. On a phone that is
    the whole cost of the feature. Each button runs its activity through the
    same path the command does — the claim is still the atomic one in
    utils.db.activities, so a double-tap can't double-claim — then repaints the
    card in place and posts the result underneath.
    """

    # Whether the card this view paints is the short form. The board is the
    # full card; RunResultView overrides this because it sits under a result.
    compact = False

    def __init__(
        self,
        cog: "Activities",
        invoker_id: int,
        state: dict,
        encounter_key: Optional[str] = None,
    ):
        super().__init__(timeout=ADVENTURE_VIEW_TIMEOUT)
        self.cog = cog
        self.invoker_id = invoker_id
        self.message: Optional[discord.Message] = None
        # Armed before `_build`, which mounts whatever is armed. The board
        # never passes one; the run card does.
        self._arm_encounter(encounter_key)
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
        banked = sum(
            state["charges"][a]["ready"]
            for a in BUTTON_ACTIVITIES
            if state["enabled"][a]
        )
        self.add_item(_CollectButton(banked))
        self.add_item(_RefreshButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                embed=h.info(
                    "Run `/adventure dashboard` yourself — it shows your own "
                    "charges."
                ),
                ephemeral=True,
            )
            return False
        return True

    def _payload(self, dashboard: discord.Embed) -> dict:
        """What an edit sends. The board is the dashboard and nothing else."""
        return {"embed": dashboard}

    async def _repaint(self, interaction: discord.Interaction):
        """Rebuild the card and the buttons from fresh state."""
        embed, state = await self.cog.adventure_dashboard(
            interaction.guild, interaction.user, compact=self.compact
        )
        self._build(state)
        await interaction.edit_original_response(view=self, **self._payload(embed))

    async def refresh(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self._repaint(interaction)

    async def _report(self, interaction: discord.Interaction, run):
        """Post a finished run under the board.

        A refusal goes out privately: the card underneath already says what
        happened, and a public "not yet" under a dashboard is noise.
        """
        message = await interaction.followup.send(
            embed=run.embed,
            ephemeral=not run.claimed,
            wait=True,
            **_view_kwargs(run.view),
        )
        if run.view is not None:
            run.view.message = message

    async def collect(self, interaction: discord.Interaction):
        await interaction.response.defer()
        run = await self.cog.collect_all(interaction.guild, interaction.user)
        await self._repaint(interaction)
        await self._report(interaction, run)

    async def press(self, interaction: discord.Interaction, activity: str):
        await interaction.response.defer()
        run = await self.cog.run_activity(interaction.guild, interaction.user, activity)
        # Repaint first: the card is what the member is looking at, and it now
        # holds one fewer charge whether the run paid out or was refused.
        await self._repaint(interaction)
        await self._report(interaction, run)

    async def on_timeout(self):
        if self.message is None:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass


class RunResultView(AdventureView):
    """The dashboard, riding under one command's result.

    Most members never ran `/adventure`. They typed `/work`, got a paycheck,
    and had no way of knowing there was a card that would have told them three
    hunts were also waiting — so the loop's best feature was the one only
    people who already knew about it used. Every individual activity command
    now replies with its result *and* the short dashboard, buttons included,
    which makes the discovery and the shortcut the same thing.

    Unlike the board, this card has a slot for an outcome, so a press replaces
    the result on top rather than posting a new message. Three hours of
    `/work` should not be three hours of new messages.

    An encounter hosted here lives as long as the card does rather than
    ENCOUNTER_TIMEOUT, which is deliberate: the two timers are on the same
    message and the shorter one would grey out half its buttons while the rest
    stayed live. Nothing is owed until a press either way.
    """

    compact = True

    def __init__(
        self,
        cog: "Activities",
        invoker_id: int,
        state: dict,
        result: discord.Embed,
        encounter_key: Optional[str] = None,
    ):
        # Set before super().__init__, which paints the card `_payload` reads.
        self.result = result
        super().__init__(cog, invoker_id, state, encounter_key=encounter_key)

    def _build(self, state: dict):
        super()._build(state)
        self._mount_encounter(row=ENCOUNTER_ROW)

    def _payload(self, dashboard: discord.Embed) -> dict:
        return {"embeds": [self.result, dashboard]}

    async def press(self, interaction: discord.Interaction, activity: str):
        await interaction.response.defer()
        run = await self.cog.run_activity(interaction.guild, interaction.user, activity)
        if not run.claimed:
            # A refusal must never overwrite the result being looked at — the
            # fishing rule: losing a good roll to a "still on cooldown" notice
            # is worse than the notice is useful.
            await self._repaint(interaction)
            return await interaction.followup.send(embed=run.embed, ephemeral=True)
        self._adopt(run)
        await self._repaint(interaction)

    async def collect(self, interaction: discord.Interaction):
        await interaction.response.defer()
        run = await self.cog.collect_all(interaction.guild, interaction.user)
        if not run.claimed:
            await self._repaint(interaction)
            return await interaction.followup.send(embed=run.embed, ephemeral=True)
        self._adopt(run)
        await self._repaint(interaction)

    def _adopt(self, run):
        """Take over a finished run: its result becomes the card's top embed.

        The run's own EncounterView is discarded and only its key is kept —
        the choice belongs on this message, under the dashboard, not on a
        second one.
        """
        self.result = run.embed
        self._arm_encounter(run.encounter_key)

    async def _show_encounter(self, interaction, step, chosen: str):
        self.result = step.embed
        await self._repaint(interaction)


class EncounterView(_EncounterHost, discord.ui.View):
    """The follow-up choice a run opens, on a message of its own.

    Used where the result is already its own message — a press on the
    `/adventure` board, or a collect. When the result *is* the card (an
    individual command), `RunResultView` hosts the same encounter instead.

    On timeout the choice simply lapses. The alternative, auto-picking the safe
    option, would hand out coins to a member who walked away, and would make
    "do nothing" a strategy on the encounters whose safe option is the better
    expected value. A chain that lapses mid-way keeps whatever earlier stages
    already paid — those were separate decisions, and they were answered.
    """

    def __init__(
        self, cog: "Activities", invoker_id: int, guild_id: int, encounter_key: str
    ):
        super().__init__(timeout=ENCOUNTER_TIMEOUT)
        self.cog = cog
        self.invoker_id = invoker_id
        self.guild_id = guild_id
        self.message: Optional[discord.Message] = None
        self._arm_encounter(encounter_key)
        self._mount_encounter()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                embed=h.info("That's not your encounter — go find your own."),
                ephemeral=True,
            )
            return False
        return True

    async def _show_encounter(self, interaction, step, chosen: str):
        if step.next_key:
            # One decision opened another: swap in the new stage's options and
            # restart the clock, since the member is being asked something new.
            self.clear_items()
            self._mount_encounter()
        else:
            self._settle_encounter(chosen)
        await interaction.edit_original_response(embed=step.embed, view=self)
        if not step.next_key:
            self.stop()

    async def on_timeout(self):
        if self.message is None or self.encounter_key is None:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass
