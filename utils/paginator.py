"""Button navigation for the bot's paginated listings.

Every leaderboard was page-numbered but had no way to turn a page: reading the
next ten rows meant re-running the command with `page:2`, which on a phone is
the whole command retyped. That is the same gap `/shop` had, and there are six
more of them (`/coin top`, `/coin contrib`, `/fish top`, `/fish global`,
`/casino top`, `/level top`), so the navigation lives here rather than being
written out per cog.

A listing plugs in by handing over a **builder** — `async (page, choice) ->
Page` — which is the code it already had for rendering one screen. The view
never formats anything itself; it only calls the builder again with a
different page or a different `choice`, so a board's rows, ranks and footers
stay in the cog that owns them.

`choice` is the second dimension a listing can be re-cut by, described by a
`Switch`: the This server / Global scope every economy board offers, or
`/fish global`'s stat. Two or three options render as buttons (a scope toggle
should be one tap), more become a dropdown. A listing without one passes
`switch=None` and the builder simply ignores the argument.

Transient by design — no persistent custom_ids, the way the /squad and /raid
boards have. Nothing is bought or changed by a page turn, so a restart that
orphans these buttons costs a member a re-run at worst.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

import discord

from utils import helpers as h

# How long a listing stays pressable. Long enough to read a leaderboard on a
# phone, short enough that an abandoned one stops responding instead of
# re-rendering rows that have since moved.
BROWSE_TIMEOUT = 300  # 5 min

# Past this many options a switch is a dropdown rather than a button row: a
# scope toggle should be one tap, but six stats would eat the whole row.
_MAX_SWITCH_BUTTONS = 3


@dataclass
class Page:
    """One rendered screen, and where it sits in the listing."""

    embed: discord.Embed
    page: int = 1
    pages: int = 1


@dataclass
class Switch:
    """A second dimension a listing can be re-cut by (scope, stat, …).

    `options` are (value, label) pairs in display order; `current` is the one
    on screen, which is never pressable. The value is handed straight back to
    the builder as its `choice`, so it is whatever that command already calls
    it ("server"/"global", a stat key).
    """

    options: list[tuple[str, str]]
    current: str
    placeholder: str = "Change the view…"
    emoji: dict[str, str] = field(default_factory=dict)

    def label_for(self, value: str) -> str:
        for option, label in self.options:
            if option == value:
                return label
        return value


def scope_switch(current: str) -> Switch:
    """The This server / Global toggle every economy leaderboard offers.

    This is the one place the two scopes are a genuine choice rather than
    trivia about a global account, which is why naming them here is worth the
    words (see the scope rule in CLAUDE.md).
    """
    return Switch(
        options=[("server", "This server"), ("global", "Global")],
        current=current,
        emoji={"server": "🏠", "global": "🌍"},
    )


PageBuilder = Callable[[int, Optional[str]], Awaitable[Page]]


class _NavButton(discord.ui.Button):
    """A page arrow. Moves `delta` pages without changing the view's choice."""

    def __init__(self, delta: int, emoji: str, disabled: bool):
        super().__init__(
            emoji=emoji, style=discord.ButtonStyle.secondary, disabled=disabled, row=0
        )
        self.delta = delta

    async def callback(self, interaction: discord.Interaction):
        view: "PagedView" = self.view
        await view.go(interaction, view.page + self.delta, view.choice)


class _SwitchButton(discord.ui.Button):
    """One option of a small switch — re-cuts the listing from page 1."""

    def __init__(self, value: str, label: str, emoji: Optional[str], current: bool):
        super().__init__(
            label=label,
            emoji=emoji,
            style=(
                discord.ButtonStyle.primary
                if current
                else discord.ButtonStyle.secondary
            ),
            disabled=current,
            row=1,
        )
        self.value = value

    async def callback(self, interaction: discord.Interaction):
        await self.view.go(interaction, 1, self.value)


class _SwitchSelect(discord.ui.Select):
    """A switch with too many options to be a button row."""

    def __init__(self, switch: Switch):
        super().__init__(
            placeholder=switch.placeholder,
            row=1,
            options=[
                discord.SelectOption(
                    label=label,
                    value=value,
                    emoji=switch.emoji.get(value),
                    default=value == switch.current,
                )
                for value, label in switch.options
            ],
        )

    async def callback(self, interaction: discord.Interaction):
        await self.view.go(interaction, 1, self.values[0])


class PagedView(discord.ui.View):
    """Arrows (and an optional switch) over a listing the cog knows how to build.

    Only the member who ran the command can press. A leaderboard isn't private,
    but it is *theirs to read* — someone else turning the page under them is
    the thing buttons are supposed to fix, not introduce — and anyone who wants
    their own copy runs the command.
    """

    def __init__(
        self,
        builder: PageBuilder,
        invoker_id: int,
        first: Page,
        *,
        switch: Optional[Switch] = None,
        timeout: float = BROWSE_TIMEOUT,
    ):
        super().__init__(timeout=timeout)
        self.builder = builder
        self.invoker_id = invoker_id
        self.switch = switch
        self.page = first.page
        self.pages = first.pages
        self.message: Optional[discord.Message] = None
        self._build()

    @property
    def choice(self) -> Optional[str]:
        return self.switch.current if self.switch else None

    # ── layout ────────────────────────────────────────────────────────────────
    def _build(self):
        """Rebuild the rows for the page currently on screen."""
        self.clear_items()
        if self.pages > 1:
            self.add_item(_NavButton(-1, "◀️", self.page <= 1))
            self.add_item(
                discord.ui.Button(
                    label=f"{self.page}/{self.pages}",
                    style=discord.ButtonStyle.secondary,
                    disabled=True,
                    row=0,
                )
            )
            self.add_item(_NavButton(1, "▶️", self.page >= self.pages))
        if self.switch is None:
            return
        if len(self.switch.options) <= _MAX_SWITCH_BUTTONS:
            for value, label in self.switch.options:
                self.add_item(
                    _SwitchButton(
                        value,
                        label,
                        self.switch.emoji.get(value),
                        value == self.switch.current,
                    )
                )
        else:
            self.add_item(_SwitchSelect(self.switch))

    # ── interaction ───────────────────────────────────────────────────────────
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                embed=h.info("Run the command yourself to browse this list."),
                ephemeral=True,
            )
            return False
        return True

    async def go(
        self, interaction: discord.Interaction, page: int, choice: Optional[str]
    ):
        """Repaint the message with another page (or another cut of the list)."""
        # A board can be a chunked cross-guild read, so acknowledge the press
        # first and edit once the rows are in.
        await interaction.response.defer()
        result = await self.builder(page, choice)
        if self.switch is not None and choice is not None:
            self.switch.current = choice
        self.page, self.pages = result.page, result.pages
        self._build()
        await interaction.edit_original_response(embed=result.embed, view=self)

    async def on_timeout(self):
        if self.message is None:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass


async def send(
    ctx,
    builder: PageBuilder,
    *,
    page: int = 1,
    switch: Optional[Switch] = None,
    timeout: float = BROWSE_TIMEOUT,
) -> Optional[PagedView]:
    """Render a listing's first screen and hang the navigation off it.

    The whole integration for a command: build the requested page, reply with
    it, and keep the view alive on the message. Returns the view (None when
    there is nothing to navigate — a single page with no switch gets a plain
    reply rather than a row of dead buttons).
    """
    first = await builder(page, switch.current if switch else None)
    view = PagedView(builder, ctx.author.id, first, switch=switch, timeout=timeout)
    if not view.children:
        await ctx.reply(embed=first.embed)
        return None
    view.message = await ctx.reply(embed=first.embed, view=view)
    return view
