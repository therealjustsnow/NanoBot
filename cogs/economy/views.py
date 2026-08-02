"""discord.ui views for the economy cog: /squad assembly + confirm, /raid join
board, the /coin grant|take mass-member picker, and the /shop browser."""

from __future__ import annotations

import io
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

import discord

from utils import db
from utils import globalxp
from utils import helpers as h

from .constants import COOP_CONFIRM_TIMEOUT, RAID_TIMEOUT, SHOP_BROWSE_TIMEOUT

if TYPE_CHECKING:
    from .cog import Economy


# Squad-confirm button custom_ids encode the squad id so a persistent
# (timeout=None) view can be rebuilt after a restart and route clicks correctly:
#   "squad:{squad_id}:{action}"  — action ∈ {confirm, decline}
_SQUAD_ACTIONS = ("confirm", "decline")


def _squad_cid(squad_id: int, action: str) -> str:
    return f"squad:{squad_id}:{action}"


def _decode_squad_cid(custom_id: str) -> tuple[int, str] | None:
    parts = custom_id.split(":")
    if len(parts) != 3 or parts[0] != "squad" or parts[2] not in _SQUAD_ACTIONS:
        return None
    try:
        return int(parts[1]), parts[2]
    except ValueError:
        return None


class SquadButton(discord.ui.Button):
    """A squad-confirm button carrying a per-squad custom_id (survives restarts)."""

    def __init__(
        self,
        squad_id: int,
        action: str,
        label: str,
        style: discord.ButtonStyle,
        emoji: str,
        handler: Callable,
    ):
        super().__init__(
            label=label,
            style=style,
            emoji=emoji,
            custom_id=_squad_cid(squad_id, action),
        )
        self._handler = handler

    async def callback(self, interaction: discord.Interaction):
        await self._handler(interaction)


class SquadView(discord.ui.View):
    """Squad-confirm gate for a co-op activity reward.

    The author tags one or more teammates; every tagged teammate presses
    Confirm (clicking is their own confirmation). Coins + contribution are
    awarded to the whole party — author included — only once *everyone* has
    confirmed. Any involved member can Decline to scrap it. Persisted in SQLite
    (see utils/db economy_squads) and restored on startup, so a restart while a
    confirm is pending doesn't orphan the button or drop the payout. Auto-expires
    COOP_CONFIRM_TIMEOUT after creation via a cog-owned timer (the view itself is
    persistent/timeout=None so it can be re-registered after a restart).
    """

    def __init__(
        self,
        cog: "Economy",
        squad_id: int,
        author_id: int,
        partner_ids: list[int],
        activity: str,
        confirmed: Optional[set[int]] = None,
        created_at: Optional[float] = None,
    ):
        super().__init__(timeout=None)
        self.cog = cog
        self.squad_id = squad_id
        self.author_id = author_id
        self.partner_ids = partner_ids
        self.activity = activity
        # Teammates who've pressed Confirm; payout fires when this covers all.
        self.confirmed: set[int] = confirmed if confirmed is not None else set()
        self.created_at = created_at if created_at is not None else time.time()
        self.message: Optional[discord.abc.Message] = None
        self.resolved = False
        for action, label, style, emoji, handler in (
            ("confirm", "Confirm", discord.ButtonStyle.success, "🤝", self._confirm),
            ("decline", "Decline", discord.ButtonStyle.secondary, "✖️", self._decline),
        ):
            self.add_item(SquadButton(squad_id, action, label, style, emoji, handler))

    def _party_size(self) -> int:
        return 1 + len(self.partner_ids)  # author + teammates

    def _roster_lines(self) -> str:
        lines = []
        for uid in self.partner_ids:
            mark = "✅" if uid in self.confirmed else "⏳"
            lines.append(f"{mark} <@{uid}>")
        return "\n".join(lines)

    def _expired(self) -> bool:
        return time.time() - self.created_at >= COOP_CONFIRM_TIMEOUT

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

    async def _reject_stale(self, interaction: discord.Interaction) -> bool:
        """If the squad already resolved/expired, ack the click + tidy up."""
        if self.resolved:
            await interaction.response.send_message(
                embed=h.warn("This squad has already ended."), ephemeral=True
            )
            return True
        if self._expired():
            await interaction.response.send_message(
                embed=h.warn("This squad has expired."), ephemeral=True
            )
            await self.expire()
            return True
        return False

    async def expire(self):
        """Disable the confirm and drop it from persistence (called by the timer)."""
        if self.resolved:
            return
        self.resolved = True
        for child in self.children:
            child.disabled = True
        if self.message is not None:
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
        await self.cog._end_squad(self.squad_id)
        self.stop()

    async def _confirm(self, interaction: discord.Interaction):
        if await self._reject_stale(interaction):
            return
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

        # Still waiting on someone — persist progress, update the board, bail.
        if len(self.confirmed) < len(self.partner_ids):
            cfg = await self.cog._cfg(interaction.guild.id)
            await db.set_squad_confirmed(self.squad_id, list(self.confirmed))
            if self.resolved:
                # A concurrent final confirm resolved the squad while we were
                # persisting — don't repaint the payout board with a waiting one.
                return await interaction.response.defer()
            return await interaction.response.edit_message(
                embed=await self._embed(cfg), view=self
            )

        # Everyone's in — claim the payout before the first await so a second
        # near-simultaneous final confirm can't pay the party twice.
        if self.resolved:
            return await interaction.response.send_message(
                embed=h.warn("This squad has already ended."), ephemeral=True
            )
        self.resolved = True
        cfg = await self.cog._cfg(interaction.guild.id)
        reward = cfg["coop_reward"]
        party = [self.author_id, *self.partner_ids]
        # Drop the persisted row BEFORE paying: a crash mid-payout then
        # under-pays (a support ping) instead of leaving a restorable squad
        # whose re-confirm would pay the whole party a second time.
        await self.cog._end_squad(self.squad_id)
        paid, skipped = await self.cog._pay_party(party, "coop", reward)
        for child in self.children:
            child.disabled = True
        activity = f" for **{self.activity}**" if self.activity else ""
        roster = ", ".join(f"<@{uid}>" for uid in paid) or "nobody"
        body = (
            f"🤝 The squad teamed up{activity}! **{len(paid)}** earned "
            f"{self.cog._money(cfg, reward)} and **+{reward:,}** "
            f"contribution.\n\n{roster}"
        )
        if skipped:
            body += "\n\nStill on co-op cooldown, so nothing this time: " + ", ".join(
                f"<@{uid}>" for uid in skipped
            )
        await interaction.response.edit_message(
            embed=h.ok(body, "Squad Confirmed"), view=self
        )
        self.stop()

    async def _decline(self, interaction: discord.Interaction):
        if await self._reject_stale(interaction):
            return
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
        await self.cog._end_squad(self.squad_id)
        self.stop()


class SquadBuilderView(discord.ui.View):
    """Host-only assembly menu for /squad when no teammates are tagged.

    A UserSelect lets the host pick up to 25 teammates (Discord's per-select
    cap — far past a raid party), then **Start** converts the message into a
    SquadView confirmation board. Only the host can drive it. Short-lived and
    in-memory — the assembly step carries no payout, so it simply expires on
    timeout or bot restart (the SquadView it launches *is* persisted).
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
        if self.resolved:
            # A double-click raced the first Start — one squad is enough.
            return await interaction.response.defer()
        if not self.selected:
            return await interaction.response.send_message(
                embed=h.err("Pick at least one teammate first."), ephemeral=True
            )
        self.resolved = True
        cfg = await self.cog._cfg(interaction.guild.id)
        # Persist the confirmation board so a restart mid-confirm keeps working.
        view = await self.cog._make_squad(
            interaction.guild.id,
            interaction.channel_id,
            self.host_id,
            self.selected,
            self.activity,
        )
        pings = " ".join(f"<@{uid}>" for uid in self.selected)
        await interaction.response.edit_message(
            content=pings, embed=await view._embed(cfg), view=view
        )
        # The confirmation board lives on the same message we've been editing.
        view.message = self.message
        if self.message is not None:
            await db.set_squad_message(view.squad_id, self.message.id)
        self.cog._arm_squad(view)
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
# Button custom_ids encode the raid id so a persistent (timeout=None) view can be
# rebuilt after a restart and route clicks to the right raid:
#   "raid:{raid_id}:{action}"  — action ∈ {join, leave, finish, cancel}
_RAID_ACTIONS = ("join", "leave", "finish", "cancel")


def _raid_cid(raid_id: int, action: str) -> str:
    return f"raid:{raid_id}:{action}"


def _decode_raid_cid(custom_id: str) -> tuple[int, str] | None:
    parts = custom_id.split(":")
    if len(parts) != 3 or parts[0] != "raid" or parts[2] not in _RAID_ACTIONS:
        return None
    try:
        return int(parts[1]), parts[2]
    except ValueError:
        return None


class RaidButton(discord.ui.Button):
    """A raid-board button carrying a per-raid custom_id so it survives restarts."""

    def __init__(
        self,
        raid_id: int,
        action: str,
        label: str,
        style: discord.ButtonStyle,
        emoji: str,
        handler: Callable,
    ):
        super().__init__(
            label=label, style=style, emoji=emoji, custom_id=_raid_cid(raid_id, action)
        )
        self._handler = handler

    async def callback(self, interaction: discord.Interaction):
        await self._handler(interaction)


class RaidView(discord.ui.View):
    """Open join board for a group co-op (raid, event, big dungeon).

    Anyone in the server can Join (clicking is their own confirmation) up to the
    guild's party cap; the host or a Manage-Server mod presses Finish to pay
    everyone who joined, or Cancel to scrap it. Persisted in SQLite (see
    utils/db economy_raids) and restored on startup, so a restart doesn't orphan
    the buttons. Auto-expires RAID_TIMEOUT after creation via a cog-owned timer
    (the view itself is persistent/timeout=None, so it can be re-registered after
    a restart).
    """

    def __init__(
        self,
        cog: "Economy",
        raid_id: int,
        host_id: int,
        activity: str,
        participants: Optional[dict[int, None]] = None,
        created_at: Optional[float] = None,
    ):
        super().__init__(timeout=None)
        self.cog = cog
        self.raid_id = raid_id
        self.host_id = host_id
        self.activity = activity
        # Host counts as the first participant; dict keeps stable join order.
        self.participants: dict[int, None] = (
            participants if participants is not None else {host_id: None}
        )
        self.created_at = created_at if created_at is not None else time.time()
        self.message: Optional[discord.abc.Message] = None
        self.resolved = False
        for action, label, style, emoji, handler in (
            ("join", "Join", discord.ButtonStyle.primary, "⚔️", self._join),
            ("leave", "Leave", discord.ButtonStyle.secondary, "🚪", self._leave),
            ("finish", "Finish", discord.ButtonStyle.success, "✅", self._finish),
            ("cancel", "Cancel", discord.ButtonStyle.danger, "✖️", self._cancel),
        ):
            self.add_item(RaidButton(raid_id, action, label, style, emoji, handler))

    def _can_manage(self, user: discord.Member) -> bool:
        return user.id == self.host_id or user.guild_permissions.manage_guild

    def _expired(self) -> bool:
        return time.time() - self.created_at >= RAID_TIMEOUT

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

    async def _reject_stale(self, interaction: discord.Interaction) -> bool:
        """If the raid already ended/expired, ack the click and tidy up. True if so."""
        if self.resolved:
            await interaction.response.send_message(
                embed=h.warn("This raid has already ended."), ephemeral=True
            )
            return True
        if self._expired():
            await interaction.response.send_message(
                embed=h.warn("This raid has expired."), ephemeral=True
            )
            await self.expire()
            return True
        return False

    async def expire(self):
        """Disable the board and drop it from persistence (called by the cog timer)."""
        if self.resolved:
            return
        self.resolved = True
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(
                    embed=h.warn(
                        "Raid expired — host didn't finish it in time.", "⏳ Expired"
                    ),
                    view=self,
                )
            except discord.HTTPException:
                pass
        await self.cog._end_raid(self.raid_id)
        self.stop()

    async def _join(self, interaction: discord.Interaction):
        if interaction.user.bot:
            return
        if await self._reject_stale(interaction):
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
        await db.set_raid_participants(self.raid_id, list(self.participants))
        await self._refresh(interaction, cfg)

    async def _leave(self, interaction: discord.Interaction):
        if await self._reject_stale(interaction):
            return
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
        await db.set_raid_participants(self.raid_id, list(self.participants))
        await self._refresh(interaction, cfg)

    async def _finish(self, interaction: discord.Interaction):
        if await self._reject_stale(interaction):
            return
        if not self._can_manage(interaction.user):
            return await interaction.response.send_message(
                embed=h.err("Only the host or a server manager can finish the raid."),
                ephemeral=True,
            )
        cfg = await self.cog._cfg(interaction.guild.id)
        # Re-check after the await: a second near-simultaneous Finish (host and
        # a mod clicking together) could have already paid the party.
        if self.resolved:
            return await interaction.response.send_message(
                embed=h.warn("This raid has already ended."), ephemeral=True
            )
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
        # Drop the persisted row BEFORE paying (same crash-safety trade as
        # SquadView: under-pay beats a restorable board that double-pays).
        await self.cog._end_raid(self.raid_id)
        paid, skipped = await self.cog._pay_party(
            list(self.participants), "raid", reward
        )
        for child in self.children:
            child.disabled = True
        what = f" for **{self.activity}**" if self.activity else ""
        roster = ", ".join(f"<@{uid}>" for uid in paid) or "nobody"
        body = (
            f"⚔️ Raid complete{what}! **{len(paid)}** members each earned "
            f"{self.cog._money(cfg, reward)} + **{reward:,}** "
            f"contribution.\n\n{roster}"
        )
        if skipped:
            body += "\n\nStill on raid cooldown, so nothing this time: " + ", ".join(
                f"<@{uid}>" for uid in skipped
            )
        await interaction.response.edit_message(
            embed=h.ok(body, "Raid Rewards Paid"), view=self
        )
        self.stop()

    async def _cancel(self, interaction: discord.Interaction):
        if await self._reject_stale(interaction):
            return
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
        await self.cog._end_raid(self.raid_id)
        self.stop()


# ══════════════════════════════════════════════════════════════════════════════
#  /coin grant | /coin take  mass-member picker
# ══════════════════════════════════════════════════════════════════════════════


class MassCoinPickerView(discord.ui.View):
    """Manage-Server-only picker for /coin grant or /coin take with no members tagged.

    Mirrors the /squad assembly menu: a UserSelect lets the admin pick up to 25
    members (Discord's per-select cap), then **Confirm** applies the coin
    change to everyone picked at once. Unlike a squad, there's no confirmation
    step for the targets — a mod granting/taking coins doesn't need the
    recipients to opt in — so this simply applies immediately and edits the
    message with a receipt. Short-lived and in-memory; it just expires on
    timeout since there's no payout state to persist across a restart.
    """

    def __init__(self, cog: "Economy", host_id: int, action: str, amount: int):
        super().__init__(timeout=COOP_CONFIRM_TIMEOUT)
        self.cog = cog
        self.host_id = host_id
        self.action = action  # "grant" or "take"
        self.amount = amount
        self.selected: list[int] = []
        self.message: Optional[discord.Message] = None
        self.resolved = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.host_id:
            await interaction.response.send_message(
                embed=h.err("Only the mod who ran this command can use it."),
                ephemeral=True,
            )
            return False
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                embed=h.err("You need Manage Server to do this."), ephemeral=True
            )
            return False
        return True

    def _embed(self, cfg: dict) -> discord.Embed:
        verb = "grant" if self.action == "grant" else "take"
        prep = "to" if self.action == "grant" else "from"
        money = self.cog._money(cfg, self.amount)
        if self.selected:
            roster = "\n".join(f"• <@{uid}>" for uid in self.selected)
            picks = f"**Picked ({len(self.selected)}):**\n{roster}"
        else:
            picks = "*No members picked yet.*"
        body = (
            f"Pick up to 25 members to {verb} {money} {prep} each.\n\n"
            f"Use the menu below, then press **Confirm** to apply.\n\n{picks}"
        )
        title = "🪙 Mass Grant" if self.action == "grant" else "🪙 Mass Take"
        return h.embed(title, body, h.BLUE)

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="Pick members…",
        min_values=1,
        max_values=25,
    )
    async def pick(
        self, interaction: discord.Interaction, select: discord.ui.UserSelect
    ):
        chosen: list[int] = []
        for user in select.values:
            if user.bot:
                continue
            if user.id not in chosen:
                chosen.append(user.id)
        self.selected = chosen
        cfg = await self.cog._cfg(interaction.guild.id)
        await interaction.response.edit_message(embed=self._embed(cfg), view=self)

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self.resolved:
            # A double-click raced the first Confirm — don't apply twice.
            return await interaction.response.defer()
        if not self.selected:
            return await interaction.response.send_message(
                embed=h.err("Pick at least one member first."), ephemeral=True
            )
        self.resolved = True
        cfg = await self.cog._cfg(interaction.guild.id)
        delta = self.amount if self.action == "grant" else -self.amount
        lines = []
        for uid in self.selected:
            new_bal = await db.add_coins(uid, delta)
            lines.append(f"<@{uid}> → {self.cog._money(cfg, new_bal)}")
        for child in self.children:
            child.disabled = True
        verb = "Granted" if self.action == "grant" else "Took"
        prep = "to" if self.action == "grant" else "from"
        await interaction.response.edit_message(
            embed=h.ok(
                f"{verb} {self.cog._money(cfg, self.amount)} {prep} "
                f"**{len(self.selected)}** member(s).\n\n" + "\n".join(lines),
                "🪙 Mass Coin Update",
            ),
            view=self,
        )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.resolved = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            embed=h.warn("Mass coin update cancelled.", "✖️ Cancelled"), view=self
        )
        self.stop()

    async def on_timeout(self):
        if self.resolved or self.message is None:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(
                embed=h.warn("Mass coin update expired.", "⏳ Expired"), view=self
            )
        except discord.HTTPException:
            pass


# ══════════════════════════════════════════════════════════════════════════════
#  /shop  browser
# ══════════════════════════════════════════════════════════════════════════════
# The aisles, in the order they appear on the switcher row: key, label, emoji.
# "browse" is the hub — the landing screen the group's fallback renders.
SHOP_AISLES: tuple[tuple[str, str, str], ...] = (
    ("browse", "Shop", "🛍️"),
    ("profile", "Profile", "🎨"),
    ("wallet", "Wallet", "💳"),
    ("server", "Server", "🏠"),
)


@dataclass
class ShopPage:
    """One rendered shop screen, and where it sits in its aisle.

    The cog builds these; the view only moves between them. `image` is the
    cosmetic aisles' preview sheet as raw bytes rather than a discord.File,
    because a File's buffer is consumed on send and a page a member scrolls
    back to has to be sendable again.
    """

    embed: discord.Embed
    page: int = 1
    pages: int = 1
    image: Optional[bytes] = None
    filename: Optional[str] = None

    def file(self) -> Optional[discord.File]:
        if self.image is None or self.filename is None:
            return None
        return discord.File(fp=io.BytesIO(self.image), filename=self.filename)


class _ShopNavButton(discord.ui.Button):
    """A page arrow. Moves `delta` pages within the aisle being viewed."""

    def __init__(self, delta: int, emoji: str, disabled: bool):
        super().__init__(
            emoji=emoji, style=discord.ButtonStyle.secondary, disabled=disabled, row=0
        )
        self.delta = delta

    async def callback(self, interaction: discord.Interaction):
        view: "ShopView" = self.view
        await view.go(interaction, view.aisle, view.page + self.delta)


class _ShopAisleButton(discord.ui.Button):
    """A switcher button — opens another aisle at its first page."""

    def __init__(self, aisle: str, label: str, emoji: str, current: bool):
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
        self.aisle = aisle

    async def callback(self, interaction: discord.Interaction):
        await self.view.go(interaction, self.aisle, 1)


class ShopView(discord.ui.View):
    """Tap-to-browse navigation for /shop.

    Every aisle was page-numbered but had no way to turn a page: seeing the
    next six cosmetics meant re-running the whole command with `page:5`, which
    on a phone is retyping it. Arrows move within an aisle, the second row
    switches between them, and the hub is one of those aisles rather than a
    separate screen you have to back out to.

    Transient by design — no persistent custom_ids, the way the /squad and
    /raid boards have. Nothing is bought or charged here, so a restart that
    orphans these buttons costs a member nothing but a re-run, and the listing
    is personalised (your coins, what you own) so it shouldn't outlive the
    person who asked for it either. For the same reason only the invoker can
    press: everyone else's `/shop` shows their own balance.
    """

    def __init__(self, cog: "Economy", invoker_id: int, aisle: str, page: ShopPage):
        super().__init__(timeout=SHOP_BROWSE_TIMEOUT)
        self.cog = cog
        self.invoker_id = invoker_id
        self.aisle = aisle
        self.page = page.page
        self.pages = page.pages
        self.message: Optional[discord.Message] = None
        # Rendered preview sheets, keyed (aisle, page). A page turn back to one
        # already seen shouldn't pay for the render twice — including back to
        # the page the command itself rendered, which is why the screen the
        # view was built around is remembered here rather than only in `go`.
        self._art: dict[tuple[str, int], bytes] = {}
        self._remember(aisle, page)
        self._build()

    def _remember(self, aisle: str, page: ShopPage):
        if page.image is not None:
            self._art[(aisle, page.page)] = page.image

    # ── layout ────────────────────────────────────────────────────────────────
    def _build(self):
        """Rebuild both rows for the aisle/page currently on screen."""
        self.clear_items()
        if self.pages > 1:
            self.add_item(_ShopNavButton(-1, "◀️", self.page <= 1))
            indicator = discord.ui.Button(
                label=f"{self.page}/{self.pages}",
                style=discord.ButtonStyle.secondary,
                disabled=True,
                row=0,
            )
            self.add_item(indicator)
            self.add_item(_ShopNavButton(1, "▶️", self.page >= self.pages))
        for aisle, label, emoji in SHOP_AISLES:
            self.add_item(_ShopAisleButton(aisle, label, emoji, aisle == self.aisle))

    # ── interaction ───────────────────────────────────────────────────────────
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                embed=h.info("Run `/shop browse` yourself — it shows your own coins."),
                ephemeral=True,
            )
            return False
        return True

    async def go(self, interaction: discord.Interaction, aisle: str, page: int):
        """Repaint the message with another aisle/page."""
        # A cosmetic page re-renders its preview sheet, which is real work, so
        # acknowledge the press first and edit once the page is ready.
        await interaction.response.defer()
        result = await self.cog._shop_page(
            interaction.guild,
            self.invoker_id,
            aisle,
            page,
            cached_art=self._art.get((aisle, page)),
        )
        self._remember(aisle, result)
        self.aisle, self.page, self.pages = aisle, result.page, result.pages
        self._build()
        # attachments= is always passed: leaving it off keeps the *previous*
        # page's preview sheet attached to a screen that no longer describes it.
        await interaction.edit_original_response(
            embed=result.embed,
            attachments=[f] if (f := result.file()) else [],
            view=self,
        )

    async def on_timeout(self):
        if self.message is None:
            return
        for child in self.children:
            child.disabled = True
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass
