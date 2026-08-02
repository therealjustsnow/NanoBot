"""discord.ui views for the fishing cog: the buttons under a cast, the travel
map, and the tackle shop.

Why buttons here matter more than anywhere else in the bot: a cast is a
twenty-second cooldown, which means an hour of fishing is a hundred and eighty
invocations of the same command. Typing `/fish` that many times is the actual
experience of the feature, and no amount of balance work fixes it. A 🎣 button
that re-casts and edits the same message in place turns the loop into tapping,
which is what it always should have been.

All three views are **transient** — no persistent custom_ids, unlike the
/squad and /raid boards in cogs/economy. Nothing here is owed until a press
lands: a cast still goes through the same atomic `try_claim_cast`, a charter
still debits before it unlocks. A restart that orphans these buttons costs a
member one `/fish`, which is not worth a row in SQLite.

They are invoker-only for the ordinary reason: the screens count *your* coins,
*your* bag and *your* charters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import discord

from utils import helpers as h

from .constants import FISHING_VIEW_TIMEOUT
from .spots import SPOT_ORDER, spot_info

if TYPE_CHECKING:
    from .cog import Fishing


class _FishingBase(discord.ui.View):
    """Shared plumbing: invoker-only, edit-in-place, grey out on timeout."""

    def __init__(self, cog: "Fishing", invoker_id: int):
        super().__init__(timeout=FISHING_VIEW_TIMEOUT)
        self.cog = cog
        self.invoker_id = invoker_id
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                embed=h.info(
                    "Run `/fish cast` yourself — this one's showing their bag."
                ),
                ephemeral=True,
            )
            return False
        return True

    async def show(self, interaction: discord.Interaction, screen, view=None):
        """Put a screen on the message, or answer privately if it's a refusal.

        A refusal never overwrites what's on screen. Being told "still on
        cooldown" is not worth losing the Legendary you just caught, and the
        member pressed the button *because* that message was in front of them.
        """
        if not screen.ok:
            await interaction.followup.send(embed=screen.embed, ephemeral=True)
            return await screen.settle()
        target = self if view is None else view
        await interaction.edit_original_response(embed=screen.embed, view=target)
        if view is not None:
            view.message = self.message
            self.stop()
        await screen.settle()

    async def on_timeout(self):
        if self.message is None:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass


class FishingView(_FishingBase):
    """The buttons under a cast result and on the /fish hub card.

    Deliberately the *same* view in both places. A member who casts and one who
    opens the hub want the same six things next, and giving the cast reply a
    smaller set would mean the fastest way to reach the shop was to stop
    fishing and run another command.
    """

    @discord.ui.button(label="Cast", emoji="🎣", style=discord.ButtonStyle.primary)
    async def cast(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer()
        screen = await self.cog.cast_once(
            interaction.guild, interaction.user, interaction.channel
        )
        await self.show(interaction, screen)

    @discord.ui.button(label="Sell all", emoji="💰", style=discord.ButtonStyle.success)
    async def sell(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer()
        screen = await self.cog.sell_screen(interaction.guild, interaction.user)
        await self.show(interaction, screen)

    @discord.ui.button(label="Bag", emoji="🎒", style=discord.ButtonStyle.secondary)
    async def bag(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer()
        screen = await self.cog.bag_screen(interaction.guild, interaction.user)
        await self.show(interaction, screen)

    @discord.ui.button(
        label="Travel", emoji="🗺️", style=discord.ButtonStyle.secondary, row=1
    )
    async def travel(
        self, interaction: discord.Interaction, _button: discord.ui.Button
    ):
        await interaction.response.defer()
        screen, state = await self.cog.travel_screen(
            interaction.guild, interaction.user
        )
        await self.show(
            interaction, screen, TravelView(self.cog, self.invoker_id, state)
        )

    @discord.ui.button(
        label="Shop", emoji="🛒", style=discord.ButtonStyle.secondary, row=1
    )
    async def shop(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer()
        screen, stock, gear = await self.cog.shop_state(
            interaction.guild, interaction.user
        )
        await self.show(
            interaction, screen, TackleShopView(self.cog, self.invoker_id, stock, gear)
        )

    @discord.ui.button(
        label="Trap", emoji="🪤", style=discord.ButtonStyle.secondary, row=1
    )
    async def trap(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.defer()
        screen = await self.cog.trap_screen(interaction.guild, interaction.user)
        await self.show(interaction, screen)


class _BackButton(discord.ui.Button):
    """Return to the hub card. Every sub-screen carries one.

    Without it a member who taps Travel to look at the map has to re-run the
    command to get back to casting, which is the exact friction these views
    exist to remove.
    """

    def __init__(self, row: int = 4):
        super().__init__(
            label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, row=row
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        view = self.view
        screen = await view.cog.hub_screen(interaction.guild, interaction.user)
        hub = FishingView(view.cog, view.invoker_id)
        hub.message = view.message
        await interaction.edit_original_response(embed=screen.embed, view=hub)
        view.stop()


class _SpotButton(discord.ui.Button):
    """One spot on the travel map — travel to it, or buy its charter.

    The button's own job is only to say which spot it is and what pressing it
    would do; `cog.travel_to` re-checks the level, the coins and the charter,
    because this label was painted from a snapshot and the wallet moves.
    """

    def __init__(self, spot: str, state: dict, here: bool):
        info = spot_info(spot)
        label = info["name"]
        style = discord.ButtonStyle.secondary
        disabled = False
        if here:
            label = f"{label} (here)"
            disabled = True
        elif state["state"] == "open":
            style = discord.ButtonStyle.primary
        elif state["state"] == "buyable":
            label = f"{label} — charter"
            style = discord.ButtonStyle.success
        else:
            disabled = True
        super().__init__(
            label=label[:80], emoji=info["emoji"], style=style, disabled=disabled
        )
        self.spot = spot

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        view = self.view
        screen, state = await view.cog.travel_to(
            interaction.guild, interaction.user, self.spot
        )
        if not screen.ok:
            return await interaction.followup.send(embed=screen.embed, ephemeral=True)
        refreshed = TravelView(view.cog, view.invoker_id, state)
        refreshed.message = view.message
        await interaction.edit_original_response(embed=screen.embed, view=refreshed)
        view.stop()


class TravelView(_FishingBase):
    """The map: one button per spot, plus a way back."""

    def __init__(self, cog: "Fishing", invoker_id: int, state: dict):
        super().__init__(cog, invoker_id)
        for spot in SPOT_ORDER:
            self.add_item(
                _SpotButton(spot, state["spots"][spot], spot == state["current"])
            )
        self.add_item(_BackButton())


async def _repaint_shop(interaction: discord.Interaction, view, note: str):
    """Redraw the shelf under a result, and hand the message to a fresh view.

    Buying and arming both move something the shelf is displaying — the balance,
    and what's live — so each one repaints rather than leaving a member reading
    a card that has stopped being true.
    """
    shelf, stock, gear = await view.cog.shop_state(interaction.guild, interaction.user)
    shelf.embed.add_field(name="​", value=note, inline=False)
    refreshed = TackleShopView(view.cog, view.invoker_id, stock, gear)
    refreshed.message = view.message
    await interaction.edit_original_response(embed=shelf.embed, view=refreshed)
    view.stop()


class _BuyButton(discord.ui.Button):
    """Buy one of something from the tackle shop."""

    def __init__(self, item_key: str, label: str, emoji: str, affordable: bool):
        super().__init__(
            label=label[:80],
            emoji=emoji,
            style=(
                discord.ButtonStyle.success
                if affordable
                else discord.ButtonStyle.secondary
            ),
            disabled=not affordable,
        )
        self.item_key = item_key

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        view = self.view
        screen = await view.cog.buy_one(
            interaction.guild, interaction.user, self.item_key
        )
        if not screen.ok:
            return await interaction.followup.send(embed=screen.embed, ephemeral=True)
        # Repaint the shelf: a purchase moved the balance, so what's affordable
        # may have changed under the member's finger — and the thing just bought
        # is now armable from the menu below it.
        await _repaint_shop(interaction, view, screen.embed.description)


class _ArmSelect(discord.ui.Select):
    """Arm what you own, without leaving the shop.

    Bait that has been bought and not armed does nothing, and arming it meant
    another cog's command (`/inventory use glowgrub`) — so a member could buy
    the best bait in the shop and fish with none. Multi-value because a loadout
    is usually bait *and* a charm, and one command each is the friction the
    button row exists to remove.
    """

    def __init__(self, gear: list[dict]):
        options = [
            discord.SelectOption(
                label=f"{row['item'].name} ×{row['qty']:,}"[:100],
                value=row["item"].key,
                emoji=row["item"].emoji or None,
                description=("already armed — tops it up" if row["armed"] else None),
            )
            for row in gear[:25]
        ]
        super().__init__(
            placeholder="⚡ Arm bait or tackle…",
            min_values=1,
            max_values=len(options) or 1,
            options=options,
            row=3,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        view = self.view
        screen = await view.cog.arm_gear(
            interaction.guild, interaction.user, list(self.values)
        )
        if not screen.ok:
            return await interaction.followup.send(embed=screen.embed, ephemeral=True)
        await _repaint_shop(interaction, view, screen.embed.description)


class TackleShopView(_FishingBase):
    """Bait, tackle and consumables, one tap each — and a menu to arm them.

    Only the first three rows' worth is shown as buy buttons — Discord allows 25
    components and the catalogue is short, but a wall of buttons is its own
    kind of unusable. `/fish buy <item> <qty>` is still there for bulk, which
    is what a button can't do.

    The arm menu only appears when there's something to arm, so a shop opened
    on an empty inventory is the same shelf it always was.
    """

    def __init__(
        self,
        cog: "Fishing",
        invoker_id: int,
        stock: list[dict],
        gear: Optional[list[dict]] = None,
    ):
        super().__init__(cog, invoker_id)
        # Rows 0-2 for the shelf; the select claims row 3 and Back row 4, so the
        # buy buttons are capped at what fits above them.
        for entry in stock[:15]:
            self.add_item(
                _BuyButton(
                    entry["key"], entry["label"], entry["emoji"], entry["affordable"]
                )
            )
        if gear:
            self.add_item(_ArmSelect(gear))
        self.add_item(_BackButton())
