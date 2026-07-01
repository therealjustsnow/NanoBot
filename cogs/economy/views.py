"""discord.ui views for the economy cog: /squad assembly + confirm + /raid join board."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import discord

from utils import db
from utils import helpers as h

from .constants import COOP_CONFIRM_TIMEOUT, RAID_TIMEOUT

if TYPE_CHECKING:
    from .cog import Economy


class SquadView(discord.ui.View):
    """Squad-confirm gate for a co-op activity reward.

    The author tags one or more teammates; every tagged teammate presses
    Confirm (clicking is their own confirmation). Coins + contribution are
    awarded to the whole party — author included — only once *everyone* has
    confirmed. Any involved member can Decline to scrap it. Short-lived (no
    persistence) — a pending squad simply expires on bot restart.
    """

    def __init__(
        self,
        cog: "Economy",
        author_id: int,
        partner_ids: list[int],
        activity: str,
    ):
        super().__init__(timeout=COOP_CONFIRM_TIMEOUT)
        self.cog = cog
        self.author_id = author_id
        self.partner_ids = partner_ids
        self.activity = activity
        # Teammates who've pressed Confirm; payout fires when this covers all.
        self.confirmed: set[int] = set()
        self.message: Optional[discord.Message] = None
        self.resolved = False

    def _party_size(self) -> int:
        return 1 + len(self.partner_ids)  # author + teammates

    def _roster_lines(self) -> str:
        lines = []
        for uid in self.partner_ids:
            mark = "✅" if uid in self.confirmed else "⏳"
            lines.append(f"{mark} <@{uid}>")
        return "\n".join(lines)

    async def _embed(self, cfg: dict) -> discord.Embed:
        what = f"\n**Activity:** {self.activity}" if self.activity else ""
        reward = self.cog._money(cfg, cfg["coop_reward"])
        pending = len(self.partner_ids) - len(self.confirmed)
        note = (
            f"Waiting on **{pending}** more to confirm…"
            if pending
            else "Everyone's confirmed!"
        )
        body = (
            f"<@{self.author_id}> squadded up with the crew below!{what}\n\n"
            f"Each teammate presses **Confirm**. Once everyone's in, all "
            f"**{self._party_size()}** earn {reward} + contribution points.\n"
            f"*{note}*\n\n"
            f"**Squad:**\n{self._roster_lines()}"
        )
        return h.embed("🤝 Co-op Squad", body, h.BLUE)

    async def on_timeout(self):
        if self.resolved or self.message is None:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(
                embed=h.warn(
                    "Squad expired — not everyone confirmed in time.",
                    "⏳ Expired",
                ),
                view=self,
            )
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success, emoji="🤝")
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id not in self.partner_ids:
            return await interaction.response.send_message(
                embed=h.err("Only a tagged teammate can confirm this squad."),
                ephemeral=True,
            )
        if interaction.user.id in self.confirmed:
            return await interaction.response.send_message(
                embed=h.warn("You've already confirmed — waiting on the others."),
                ephemeral=True,
            )
        self.confirmed.add(interaction.user.id)
        cfg = await self.cog._cfg(interaction.guild.id)

        # Still waiting on someone — update the progress board and bail.
        if len(self.confirmed) < len(self.partner_ids):
            return await interaction.response.edit_message(
                embed=await self._embed(cfg), view=self
            )

        # Everyone's in — pay the whole party.
        self.resolved = True
        guild_id = interaction.guild.id
        reward = cfg["coop_reward"]
        party = [self.author_id, *self.partner_ids]
        for uid in party:
            await db.add_coins(guild_id, uid, reward)
            await db.add_contribution(guild_id, uid, reward)
        for child in self.children:
            child.disabled = True
        activity = f" for **{self.activity}**" if self.activity else ""
        roster = ", ".join(f"<@{uid}>" for uid in party)
        await interaction.response.edit_message(
            embed=h.ok(
                f"🤝 The squad teamed up{activity}! All **{len(party)}** earned "
                f"{self.cog._money(cfg, reward)} and **+{reward:,}** "
                f"contribution.\n\n{roster}",
                "Squad Confirmed",
            ),
            view=self,
        )
        self.stop()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def decline(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id not in (self.author_id, *self.partner_ids):
            return await interaction.response.send_message(
                embed=h.err("Only the people involved can decline this squad."),
                ephemeral=True,
            )
        self.resolved = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            embed=h.warn(f"Squad declined by <@{interaction.user.id}>.", "✖️ Declined"),
            view=self,
        )
        self.stop()


class SquadBuilderView(discord.ui.View):
    """Host-only assembly menu for /squad when no teammates are tagged.

    A UserSelect lets the host pick up to 25 teammates (Discord's per-select
    cap — far past a raid party), then **Start** converts the message into a
    SquadView confirmation board. Only the host can drive it. Short-lived and
    in-memory — it simply expires on timeout or bot restart.
    """

    def __init__(self, cog: "Economy", host_id: int, activity: str):
        super().__init__(timeout=COOP_CONFIRM_TIMEOUT)
        self.cog = cog
        self.host_id = host_id
        self.activity = activity
        self.selected: list[int] = []
        self.message: Optional[discord.Message] = None
        self.resolved = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.host_id:
            await interaction.response.send_message(
                embed=h.err("Only the host can build this squad."), ephemeral=True
            )
            return False
        return True

    def _embed(self) -> discord.Embed:
        what = f"\n**Activity:** {self.activity}" if self.activity else ""
        if self.selected:
            roster = "\n".join(f"• <@{uid}>" for uid in self.selected)
            picks = f"**Squad ({len(self.selected)}):**\n{roster}"
        else:
            picks = "*No teammates picked yet.*"
        body = (
            f"<@{self.host_id}>, pick everyone who was in on it.{what}\n\n"
            f"Use the menu to add teammates (up to 25), then press **Start** to "
            f"send it for confirmation.\n\n{picks}"
        )
        return h.embed("🤝 Assemble Your Squad", body, h.BLUE)

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="Pick your teammates…",
        min_values=1,
        max_values=25,
    )
    async def pick(
        self, interaction: discord.Interaction, select: discord.ui.UserSelect
    ):
        chosen: list[int] = []
        for user in select.values:
            # Drop bots and the host; a squad is other people who joined in.
            if user.bot or user.id == self.host_id:
                continue
            if user.id not in chosen:
                chosen.append(user.id)
        self.selected = chosen
        await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="Start", style=discord.ButtonStyle.success, emoji="🚀")
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected:
            return await interaction.response.send_message(
                embed=h.err("Pick at least one teammate first."), ephemeral=True
            )
        self.resolved = True
        cfg = await self.cog._cfg(interaction.guild.id)
        view = SquadView(self.cog, self.host_id, self.selected, self.activity)
        pings = " ".join(f"<@{uid}>" for uid in self.selected)
        await interaction.response.edit_message(
            content=pings, embed=await view._embed(cfg), view=view
        )
        # The confirmation board lives on the same message we've been editing.
        view.message = self.message
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.resolved = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            embed=h.warn("Squad assembly cancelled.", "✖️ Cancelled"), view=self
        )
        self.stop()

    async def on_timeout(self):
        if self.resolved or self.message is None:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(
                embed=h.warn("Squad assembly expired.", "⏳ Expired"), view=self
            )
        except discord.HTTPException:
            pass


# ══════════════════════════════════════════════════════════════════════════════
#  /raid  group-co-op join board
# ══════════════════════════════════════════════════════════════════════════════
class RaidView(discord.ui.View):
    """Open join board for a group co-op (raid, event, big dungeon).

    Anyone in the server can Join (clicking is their own confirmation) up to the
    guild's party cap; the host or a Manage-Server mod presses Finish to pay
    everyone who joined, or Cancel to scrap it. Short-lived and in-memory — an
    open board simply expires on bot restart or after RAID_TIMEOUT.
    """

    def __init__(self, cog: "Economy", host_id: int, activity: str):
        super().__init__(timeout=RAID_TIMEOUT)
        self.cog = cog
        self.host_id = host_id
        self.activity = activity
        # Host counts as the first participant; dict keeps stable join order.
        self.participants: dict[int, None] = {host_id: None}
        self.message: Optional[discord.Message] = None
        self.resolved = False

    def _can_manage(self, user: discord.Member) -> bool:
        return user.id == self.host_id or user.guild_permissions.manage_guild

    async def _embed(self, cfg: dict) -> discord.Embed:
        what = f"\n**Activity:** {self.activity}" if self.activity else ""
        names = "\n".join(f"• <@{uid}>" for uid in self.participants)
        reward = self.cog._money(cfg, cfg["raid_reward"])
        need = cfg["raid_min"]
        body = (
            f"Hosted by <@{self.host_id}>.{what}\n\n"
            f"Press **Join** to take part — everyone who joins earns {reward} "
            f"+ contribution when the host presses **Finish**.\n"
            f"*Need at least {need} members · {len(self.participants)}/"
            f"{cfg['raid_max']} joined.*\n\n"
            f"**Party ({len(self.participants)}):**\n{names}"
        )
        return h.embed("⚔️ Raid Party", body, h.BLUE)

    async def _refresh(self, interaction: discord.Interaction, cfg: dict):
        await interaction.response.edit_message(embed=await self._embed(cfg), view=self)

    async def on_timeout(self):
        if self.resolved or self.message is None:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(
                embed=h.warn(
                    "Raid expired — host didn't finish it in time.", "⏳ Expired"
                ),
                view=self,
            )
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Join", style=discord.ButtonStyle.primary, emoji="⚔️")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.bot:
            return
        cfg = await self.cog._cfg(interaction.guild.id)
        if interaction.user.id in self.participants:
            return await interaction.response.send_message(
                embed=h.warn("You're already in the party."), ephemeral=True
            )
        if len(self.participants) >= cfg["raid_max"]:
            return await interaction.response.send_message(
                embed=h.err(f"Party is full ({cfg['raid_max']})."), ephemeral=True
            )
        self.participants[interaction.user.id] = None
        await self._refresh(interaction, cfg)

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.secondary, emoji="🚪")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = await self.cog._cfg(interaction.guild.id)
        if interaction.user.id == self.host_id:
            return await interaction.response.send_message(
                embed=h.warn("The host can't leave — use **Cancel** to scrap it."),
                ephemeral=True,
            )
        if interaction.user.id not in self.participants:
            return await interaction.response.send_message(
                embed=h.warn("You're not in the party."), ephemeral=True
            )
        del self.participants[interaction.user.id]
        await self._refresh(interaction, cfg)

    @discord.ui.button(label="Finish", style=discord.ButtonStyle.success, emoji="✅")
    async def finish(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._can_manage(interaction.user):
            return await interaction.response.send_message(
                embed=h.err("Only the host or a server manager can finish the raid."),
                ephemeral=True,
            )
        cfg = await self.cog._cfg(interaction.guild.id)
        if len(self.participants) < cfg["raid_min"]:
            return await interaction.response.send_message(
                embed=h.err(
                    f"Need at least {cfg['raid_min']} members to pay out "
                    f"(only {len(self.participants)} joined)."
                ),
                ephemeral=True,
            )
        self.resolved = True
        reward = cfg["raid_reward"]
        guild_id = interaction.guild.id
        for uid in self.participants:
            await db.add_coins(guild_id, uid, reward)
            await db.add_contribution(guild_id, uid, reward)
        for child in self.children:
            child.disabled = True
        what = f" for **{self.activity}**" if self.activity else ""
        roster = ", ".join(f"<@{uid}>" for uid in self.participants)
        await interaction.response.edit_message(
            embed=h.ok(
                f"⚔️ Raid complete{what}! **{len(self.participants)}** members "
                f"each earned {self.cog._money(cfg, reward)} + "
                f"**{reward:,}** contribution.\n\n{roster}",
                "Raid Rewards Paid",
            ),
            view=self,
        )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._can_manage(interaction.user):
            return await interaction.response.send_message(
                embed=h.err("Only the host or a server manager can cancel the raid."),
                ephemeral=True,
            )
        self.resolved = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            embed=h.warn("Raid cancelled — no coins awarded.", "✖️ Cancelled"),
            view=self,
        )
        self.stop()
