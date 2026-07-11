"""
cogs/tickets — private-thread support tickets.

Members open a ticket from a persistent panel button (or `/ticket open`); a
modal asks for a subject + optional details, then the bot creates a private
thread under the panel channel, adds the opener, and pings the staff role
(mentioning a role inside a private thread pulls its members in — no
permission overwrites, no per-ticket channels). Staff work the ticket in the
thread and press Close (or run `/ticket close`) when it's resolved; a text
transcript goes to the log channel and the thread is locked + archived.

Everything is restart-safe with zero per-ticket restore: the panel button and
the in-thread Close/Claim buttons use static custom_ids and resolve their
ticket from the clicked channel via SQLite (`tickets` / `ticket_config`).

/ticket group:
  everyone       — open, close (opener or staff), claim/add/remove (staff)
  Manage Server  — setup, panel, limit, config, enable, disable

Edge cases handled: per-user open-ticket cap, double-click open races (an
in-memory per-member guard + a recount after the modal), missing bot thread
permissions (checked before the modal ever opens), thread-name truncation,
crash between the DB reserve and thread creation (swept at startup), manual
thread deletion (row auto-closed), the opener leaving the guild (note posted
for staff), auto-archived tickets (un-archived to post the closing message),
and double-close/double-claim races (conditional UPDATEs in the DB layer).
"""

import io
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import db
from utils import helpers as h

from .constants import (
    DEFAULT_PANEL_MESSAGE,
    DEFAULT_PANEL_TITLE,
    SUBJECT_MAX,
    THREAD_AUTO_ARCHIVE_MINUTES,
    TRANSCRIPT_MESSAGE_LIMIT,
)
from .helpers import thread_name, transcript_line
from .views import TicketModal, TicketPanelView, TicketThreadView

log = logging.getLogger("NanoBot.tickets")


class LogChannelTransformer(app_commands.Transformer):
    """Text-channel option that survives a guild-cache miss.

    The built-in `discord.TextChannel` transform resolves the picked channel
    through the guild cache and raises TransformerError when it isn't there,
    which aborts `/ticket setup` with an unresolved-argument error even though
    the staff role was fine. Setup only needs the channel's id and mention,
    and both exist on the raw AppCommandChannel payload — so fall back to it
    instead of failing.
    """

    @property
    def type(self) -> discord.AppCommandOptionType:
        return discord.AppCommandOptionType.channel

    @property
    def channel_types(self) -> list[discord.ChannelType]:
        return [discord.ChannelType.text, discord.ChannelType.news]

    async def transform(
        self, interaction: discord.Interaction, value: app_commands.AppCommandChannel
    ):
        return value.resolve() or value


# Accepts what the picker sends even when the cache lookup misses; the command
# body only touches .id and .mention, which both variants carry.
LogChannelOption = app_commands.Transform[
    "discord.TextChannel | app_commands.AppCommandChannel", LogChannelTransformer
]


class Tickets(commands.Cog):
    """Private-thread support tickets with a button panel."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Members mid-open, keyed (guild_id, user_id): a double-tap on the
        # panel button (or two modals racing) can't create two threads.
        self._opening: set[tuple[int, int]] = set()

    async def cog_load(self):
        # Static custom_ids: one registration revives every panel and every
        # open ticket's buttons after a restart.
        self.bot.add_view(TicketPanelView(self))
        self.bot.add_view(TicketThreadView(self))
        swept = await db.close_stale_tickets()
        if swept:
            log.info(f"Tickets: swept {swept} stale ticket row(s) with no thread")

    # ── Shared checks / helpers ───────────────────────────────────────────────
    def _is_staff(self, member: discord.Member, cfg: dict) -> bool:
        """Manage Server always counts as staff; the configured role also does.
        A deleted staff role therefore degrades to Manage-Server-only."""
        if member.guild_permissions.manage_guild:
            return True
        if cfg["staff_role_id"]:
            role = member.guild.get_role(int(cfg["staff_role_id"]))
            return role is not None and role in member.roles
        return False

    def _parent_channel(
        self, guild: discord.Guild, cfg: dict, interaction: discord.Interaction
    ) -> Optional[discord.TextChannel]:
        """Where new ticket threads are created: the panel channel when set,
        else the channel the command was used in (threads can't nest, so a
        thread falls back to its parent)."""
        if cfg["panel_channel_id"]:
            ch = guild.get_channel(int(cfg["panel_channel_id"]))
            if isinstance(ch, discord.TextChannel):
                return ch
        ch = interaction.channel
        if isinstance(ch, discord.Thread):
            ch = ch.parent
        return ch if isinstance(ch, discord.TextChannel) else None

    async def _precheck(
        self, interaction: discord.Interaction
    ) -> tuple[Optional[str], dict, Optional[discord.TextChannel]]:
        """Everything that can stop a ticket opening, as a user-facing error
        string (or None when the open may proceed)."""
        guild = interaction.guild
        cfg = await db.get_ticket_config(guild.id)
        if not cfg["enabled"]:
            return (
                "Tickets aren't set up on this server yet. "
                "Ask an admin to run `/ticket setup`.",
                cfg,
                None,
            )
        parent = self._parent_channel(guild, cfg, interaction)
        if parent is None:
            return (
                "I couldn't find a text channel to create the ticket in. "
                "Ask an admin to re-run `/ticket panel`.",
                cfg,
                None,
            )
        perms = parent.permissions_for(guild.me)
        if not (
            perms.view_channel
            and perms.create_private_threads
            and perms.send_messages_in_threads
        ):
            return (
                f"I'm missing the **Create Private Threads** or **Send Messages "
                f"in Threads** permission in {parent.mention}, so I can't open "
                f"a ticket right now. Please let the staff know!",
                cfg,
                parent,
            )
        open_count = await db.count_open_tickets(guild.id, interaction.user.id)
        if open_count >= cfg["max_open"]:
            return (
                f"You already have **{open_count}** open ticket(s) — please "
                f"finish those before opening another.",
                cfg,
                parent,
            )
        return None, cfg, parent

    async def precheck_open(self, interaction: discord.Interaction) -> Optional[str]:
        """Panel-button entry: validate before showing the modal so members
        don't type out their issue only to hit an error."""
        error, _cfg, _parent = await self._precheck(interaction)
        return error

    async def _log(
        self,
        guild: discord.Guild,
        cfg: dict,
        embed: discord.Embed,
        file: Optional[discord.File] = None,
    ) -> None:
        """Best-effort post to the configured log channel."""
        if not cfg["log_channel_id"]:
            return
        channel = guild.get_channel(int(cfg["log_channel_id"]))
        if channel is None:
            return
        perms = channel.permissions_for(guild.me)
        if not (perms.view_channel and perms.send_messages):
            return
        if file is not None and not perms.attach_files:
            file = None
        try:
            if file is not None:
                await channel.send(embed=embed, file=file)
            else:
                await channel.send(embed=embed)
        except discord.HTTPException as exc:
            log.debug(f"ticket log post failed in guild {guild.id}: {exc}")

    async def _resolve_ticket_thread(
        self, interaction: discord.Interaction
    ) -> tuple[Optional[str], Optional[dict], Optional[discord.Thread]]:
        """For in-thread commands/buttons: (error, ticket, thread)."""
        thread = interaction.channel
        if not isinstance(thread, discord.Thread):
            return "This only works inside a ticket thread.", None, None
        ticket = await db.get_ticket_by_thread(thread.id)
        if ticket is None:
            return "This thread isn't a ticket.", None, None
        return None, ticket, thread

    # ── Opening ───────────────────────────────────────────────────────────────
    async def open_ticket(
        self, interaction: discord.Interaction, subject: str, details: str
    ) -> None:
        """Create the ticket row + private thread. Entered from the panel
        modal and from `/ticket open <subject>`."""
        guild = interaction.guild
        user = interaction.user
        key = (guild.id, user.id)
        if key in self._opening:
            await interaction.response.send_message(
                embed=h.warn("Your ticket is already being created — one moment!"),
                ephemeral=True,
            )
            return
        self._opening.add(key)
        try:
            # Re-check everything: the modal may have sat open for a while,
            # and this is also the only check on the `/ticket open` path.
            error, cfg, parent = await self._precheck(interaction)
            if error is not None:
                await interaction.response.send_message(
                    embed=h.err(error), ephemeral=True
                )
                return
            await interaction.response.defer(ephemeral=True)

            ticket = await db.create_ticket(guild.id, user.id, subject or None)
            try:
                thread = await parent.create_thread(
                    name=thread_name(ticket["number"], user.name),
                    type=discord.ChannelType.private_thread,
                    invitable=False,
                    auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES,
                    reason=f"Ticket #{ticket['number']} opened by {user} ({user.id})",
                )
            except discord.HTTPException as exc:
                # Roll the reserved row back so it doesn't count against the
                # member's open-ticket cap.
                await db.delete_ticket(ticket["id"])
                log.warning(f"ticket thread create failed in guild {guild.id}: {exc}")
                await interaction.followup.send(
                    embed=h.err(
                        "I couldn't create the ticket thread — please let the "
                        "staff know, or try again in a moment."
                    ),
                    ephemeral=True,
                )
                return
            await db.set_ticket_thread(ticket["id"], thread.id)

            # The role mention below is what pulls staff in, but add the
            # opener explicitly — their own mention isn't guaranteed to.
            try:
                await thread.add_user(user)
            except discord.HTTPException:
                pass

            staff_role = (
                guild.get_role(int(cfg["staff_role_id"]))
                if cfg["staff_role_id"]
                else None
            )
            mention_line = user.mention + (
                f" {staff_role.mention}" if staff_role else ""
            )
            embed = h.embed(
                f"🎫 Ticket #{ticket['number']}",
                "Thanks for reaching out! A staff member will be with you "
                "soon. Describe your issue below with as much detail as you "
                "can.\n\nWhen everything's resolved, press **Close** (or run "
                "`/ticket close`).",
                h.BLUE,
            )
            embed.add_field(name="Subject", value=subject or "*(none)*", inline=False)
            if details:
                embed.add_field(name="Details", value=details, inline=False)
            embed.add_field(name="Opened by", value=user.mention, inline=False)
            allowed = discord.AllowedMentions(
                users=True,
                roles=[staff_role] if staff_role else False,
                everyone=False,
            )
            try:
                await thread.send(
                    content=mention_line,
                    embed=embed,
                    view=TicketThreadView(self),
                    allowed_mentions=allowed,
                )
            except discord.HTTPException as exc:
                log.warning(f"ticket opening message failed in {guild.id}: {exc}")

            await self._log(
                guild,
                cfg,
                h.info(
                    f"🎫 Ticket **#{ticket['number']}** opened by {user.mention} "
                    f"— {thread.mention}\n**Subject:** {subject or '*(none)*'}",
                    "Ticket Opened",
                ),
            )
            await interaction.followup.send(
                embed=h.ok(f"Your ticket is ready: {thread.mention}"),
                ephemeral=True,
            )
        finally:
            self._opening.discard(key)

    # ── Closing / claiming (shared by buttons and slash commands) ────────────
    async def handle_close(
        self, interaction: discord.Interaction, reason: Optional[str]
    ) -> None:
        error, ticket, thread = await self._resolve_ticket_thread(interaction)
        if error is not None:
            await interaction.response.send_message(embed=h.err(error), ephemeral=True)
            return
        guild = interaction.guild
        cfg = await db.get_ticket_config(guild.id)
        is_opener = interaction.user.id == int(ticket["user_id"])
        if not (is_opener or self._is_staff(interaction.user, cfg)):
            await interaction.response.send_message(
                embed=h.err("Only the ticket opener or staff can close this ticket."),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        # The conditional UPDATE makes exactly one closer win, so a double
        # click / two staff closing at once can't double-post transcripts.
        if not await db.close_ticket(ticket["id"], interaction.user.id, reason):
            await interaction.followup.send(
                embed=h.warn("This ticket is already closed."), ephemeral=True
            )
            return

        transcript = None
        if cfg["log_channel_id"]:
            transcript = await self._build_transcript(thread, ticket)
        if thread.archived:
            # Auto-archive may have shelved it; wake it to post the closer.
            try:
                await thread.edit(archived=False)
            except discord.HTTPException:
                pass
        body = f"Closed by {interaction.user.mention}."
        if reason:
            body += f"\n**Reason:** {reason}"
        try:
            await thread.send(embed=h.embed("🔒 Ticket Closed", body, h.RED))
        except discord.HTTPException:
            pass
        log_body = (
            f"🔒 Ticket **#{ticket['number']}** (opened by <@{ticket['user_id']}>) "
            f"closed by {interaction.user.mention}."
        )
        if reason:
            log_body += f"\n**Reason:** {reason}"
        await self._log(guild, cfg, h.info(log_body, "Ticket Closed"), transcript)
        # Lock + archive; locking needs Manage Threads, so fall back to a
        # plain archive when the bot doesn't have it.
        try:
            await thread.edit(locked=True, archived=True)
        except discord.Forbidden:
            try:
                await thread.edit(archived=True)
            except discord.HTTPException:
                pass
        except discord.HTTPException:
            pass
        await interaction.followup.send(
            embed=h.ok(f"Ticket #{ticket['number']} closed."), ephemeral=True
        )

    async def handle_claim(self, interaction: discord.Interaction) -> None:
        error, ticket, _thread = await self._resolve_ticket_thread(interaction)
        if error is not None:
            await interaction.response.send_message(embed=h.err(error), ephemeral=True)
            return
        cfg = await db.get_ticket_config(interaction.guild.id)
        if not self._is_staff(interaction.user, cfg):
            await interaction.response.send_message(
                embed=h.err("Only staff can claim tickets."), ephemeral=True
            )
            return
        if ticket["status"] != "open":
            await interaction.response.send_message(
                embed=h.warn("This ticket is already closed."), ephemeral=True
            )
            return
        if not await db.claim_ticket(ticket["id"], interaction.user.id):
            fresh = await db.get_ticket(ticket["id"])
            claimer = (
                f"<@{fresh['claimed_by']}>"
                if fresh and fresh["claimed_by"]
                else "someone"
            )
            await interaction.response.send_message(
                embed=h.warn(f"This ticket is already claimed by {claimer}."),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=h.ok(
                f"🙋 {interaction.user.mention} claimed this ticket and will "
                f"be helping you.",
                "Ticket Claimed",
            )
        )

    async def _build_transcript(
        self, thread: discord.Thread, ticket: dict
    ) -> Optional[discord.File]:
        """Plain-text transcript of the thread (oldest first), or None when
        history can't be read."""
        lines = [
            f"Ticket #{ticket['number']} — {ticket['subject'] or '(no subject)'}",
            f"Opened by user id {ticket['user_id']}",
            "-" * 40,
        ]
        try:
            async for msg in thread.history(
                limit=TRANSCRIPT_MESSAGE_LIMIT, oldest_first=True
            ):
                content = msg.content or ("[embed]" if msg.embeds else "")
                lines.append(
                    transcript_line(
                        msg.created_at.strftime("%Y-%m-%d %H:%M"),
                        f"{msg.author} ({msg.author.id})",
                        content,
                        [a.url for a in msg.attachments],
                    )
                )
        except discord.HTTPException as exc:
            log.debug(f"transcript fetch failed for thread {thread.id}: {exc}")
            return None
        data = "\n".join(lines).encode("utf-8")
        return discord.File(
            io.BytesIO(data),
            filename=f"ticket-{ticket['number']:04d}-transcript.txt",
        )

    # ── Listeners ─────────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_raw_thread_delete(self, payload: discord.RawThreadDeleteEvent):
        """A mod deleting the thread out from under an open ticket shouldn't
        leave a phantom row eating the member's open-ticket cap."""
        ticket = await db.get_ticket_by_thread(payload.thread_id)
        if ticket is None or ticket["status"] != "open":
            return
        await db.close_ticket(ticket["id"], None, "thread deleted")
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        cfg = await db.get_ticket_config(guild.id)
        await self._log(
            guild,
            cfg,
            h.warn(
                f"🗑️ The thread for ticket **#{ticket['number']}** (opened by "
                f"<@{ticket['user_id']}>) was deleted — the ticket has been "
                f"marked closed. No transcript could be saved.",
                "Ticket Thread Deleted",
            ),
        )

    @commands.Cog.listener()
    async def on_raw_member_remove(self, payload: discord.RawMemberRemoveEvent):
        """Note in each of the leaver's open tickets so staff know why it went
        quiet — the ticket stays open for staff to close."""
        tickets = await db.get_open_tickets_for_user(payload.guild_id, payload.user.id)
        if not tickets:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        for ticket in tickets:
            if not ticket["thread_id"]:
                continue
            thread = guild.get_thread(int(ticket["thread_id"]))
            if thread is None:
                try:
                    thread = await guild.fetch_channel(int(ticket["thread_id"]))
                except discord.HTTPException:
                    continue
            if not isinstance(thread, discord.Thread):
                continue
            try:
                await thread.send(
                    embed=h.warn(
                        f"<@{payload.user.id}> (the ticket opener) left the "
                        f"server. Staff can close this ticket with "
                        f"`/ticket close`.",
                        "Opener Left",
                    )
                )
            except discord.HTTPException:
                pass

    # ══════════════════════════════════════════════════════════════════════════
    #  /ticket command group
    # ══════════════════════════════════════════════════════════════════════════
    ticket = app_commands.Group(
        name="ticket",
        description="Private support tickets between members and staff.",
        guild_only=True,
    )

    @ticket.command(name="open", description="Open a private support ticket.")
    @app_commands.describe(subject="A short summary of your issue")
    async def ticket_open(
        self,
        interaction: discord.Interaction,
        subject: Optional[app_commands.Range[str, 1, SUBJECT_MAX]] = None,
    ):
        if subject is not None:
            await self.open_ticket(interaction, subject.strip(), "")
            return
        error = await self.precheck_open(interaction)
        if error is not None:
            await interaction.response.send_message(embed=h.err(error), ephemeral=True)
            return
        await interaction.response.send_modal(TicketModal(self))

    @ticket.command(
        name="close", description="Close this ticket (opener or staff only)."
    )
    @app_commands.describe(reason="Why the ticket is being closed")
    async def ticket_close(
        self,
        interaction: discord.Interaction,
        reason: Optional[app_commands.Range[str, 1, 200]] = None,
    ):
        await self.handle_close(interaction, reason)

    @ticket.command(name="claim", description="Claim this ticket (staff only).")
    async def ticket_claim(self, interaction: discord.Interaction):
        await self.handle_claim(interaction)

    @ticket.command(name="add", description="Add a member to this ticket (staff only).")
    @app_commands.describe(member="The member to add to the ticket")
    async def ticket_add(
        self, interaction: discord.Interaction, member: discord.Member
    ):
        error, ticket, thread = await self._resolve_ticket_thread(interaction)
        if error is not None:
            await interaction.response.send_message(embed=h.err(error), ephemeral=True)
            return
        cfg = await db.get_ticket_config(interaction.guild.id)
        if not self._is_staff(interaction.user, cfg):
            await interaction.response.send_message(
                embed=h.err("Only staff can add members to a ticket."),
                ephemeral=True,
            )
            return
        if member.bot:
            await interaction.response.send_message(
                embed=h.err("Bots can't take part in tickets."), ephemeral=True
            )
            return
        try:
            await thread.add_user(member)
        except discord.HTTPException:
            await interaction.response.send_message(
                embed=h.err(f"I couldn't add {member.mention} to this ticket."),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=h.ok(f"Added {member.mention} to the ticket.")
        )

    @ticket.command(
        name="remove", description="Remove a member from this ticket (staff only)."
    )
    @app_commands.describe(member="The member to remove from the ticket")
    async def ticket_remove(
        self, interaction: discord.Interaction, member: discord.Member
    ):
        error, ticket, thread = await self._resolve_ticket_thread(interaction)
        if error is not None:
            await interaction.response.send_message(embed=h.err(error), ephemeral=True)
            return
        cfg = await db.get_ticket_config(interaction.guild.id)
        if not self._is_staff(interaction.user, cfg):
            await interaction.response.send_message(
                embed=h.err("Only staff can remove members from a ticket."),
                ephemeral=True,
            )
            return
        if member.id == int(ticket["user_id"]):
            await interaction.response.send_message(
                embed=h.err(
                    "You can't remove the ticket opener — close the ticket "
                    "with `/ticket close` instead."
                ),
                ephemeral=True,
            )
            return
        try:
            await thread.remove_user(member)
        except discord.HTTPException:
            await interaction.response.send_message(
                embed=h.err(f"I couldn't remove {member.mention} from this ticket."),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=h.ok(f"Removed {member.mention} from the ticket.")
        )

    @ticket.command(
        name="setup",
        description="Set the staff role (and optionally a log channel) for tickets.",
    )
    @app_commands.describe(
        staff_role="Role that can claim, manage, and close tickets",
        log_channel="Channel that receives ticket events and transcripts",
        max_open="How many tickets one member can have open at once (default 3)",
    )
    async def ticket_setup(
        self,
        interaction: discord.Interaction,
        staff_role: discord.Role,
        log_channel: Optional[LogChannelOption] = None,
        max_open: Optional[app_commands.Range[int, 1, 10]] = None,
    ):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                embed=h.err("You need **Manage Server** to set up tickets."),
                ephemeral=True,
            )
            return
        if staff_role.is_default():
            await interaction.response.send_message(
                embed=h.err(
                    "@everyone can't be the staff role — that would make every "
                    "member staff. Pick the role your support team holds."
                ),
                ephemeral=True,
            )
            return
        if staff_role.managed:
            await interaction.response.send_message(
                embed=h.err(
                    f"{staff_role.mention} is managed by an integration, so members "
                    "can't hold it. Pick the role your support team holds."
                ),
                ephemeral=True,
            )
            return
        updates: dict = {"enabled": True, "staff_role_id": str(staff_role.id)}
        if log_channel is not None:
            updates["log_channel_id"] = str(log_channel.id)
        if max_open is not None:
            updates["max_open"] = max_open
        await db.set_ticket_config(interaction.guild.id, **updates)
        lines = [f"**Staff role:** {staff_role.mention}"]
        if log_channel is not None:
            lines.append(f"**Log channel:** {log_channel.mention}")
        if max_open is not None:
            lines.append(f"**Open-ticket limit:** {max_open} per member")
        lines.append(
            "\nNow post the panel with `/ticket panel` so members have a "
            "button to press!"
        )
        await interaction.response.send_message(
            embed=h.ok("\n".join(lines), "🎫 Tickets Configured"), ephemeral=True
        )

    @ticket.command(
        name="panel",
        description="Post the Open Ticket button panel in a channel.",
    )
    @app_commands.describe(
        channel="Where to post the panel (default: this channel)",
        title="Custom panel title",
        message="Custom panel text",
    )
    async def ticket_panel(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        title: Optional[app_commands.Range[str, 1, 100]] = None,
        message: Optional[app_commands.Range[str, 1, 1000]] = None,
    ):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                embed=h.err("You need **Manage Server** to post the ticket panel."),
                ephemeral=True,
            )
            return
        guild = interaction.guild
        target = channel or interaction.channel
        if isinstance(target, discord.Thread):
            target = target.parent
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(
                embed=h.err("The panel needs to go in a regular text channel."),
                ephemeral=True,
            )
            return
        perms = target.permissions_for(guild.me)
        missing = []
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            missing.append("**Send Messages** / **Embed Links**")
        if not perms.create_private_threads:
            missing.append("**Create Private Threads**")
        if not perms.send_messages_in_threads:
            missing.append("**Send Messages in Threads**")
        if missing:
            await interaction.response.send_message(
                embed=h.err(
                    f"I'm missing {', '.join(missing)} in {target.mention} — "
                    f"grant those and try again."
                ),
                ephemeral=True,
            )
            return
        cfg = await db.get_ticket_config(guild.id)
        panel_title = title or cfg["panel_title"] or DEFAULT_PANEL_TITLE
        panel_message = message or cfg["panel_message"] or DEFAULT_PANEL_MESSAGE
        embed = h.embed(panel_title, panel_message, h.BLUE)
        try:
            posted = await target.send(embed=embed, view=TicketPanelView(self))
        except discord.HTTPException:
            await interaction.response.send_message(
                embed=h.err(f"I couldn't post the panel in {target.mention}."),
                ephemeral=True,
            )
            return
        await db.set_ticket_config(
            guild.id,
            enabled=True,
            panel_channel_id=str(target.id),
            panel_message_id=str(posted.id),
            panel_title=panel_title,
            panel_message=panel_message,
        )
        note = ""
        if not cfg["staff_role_id"]:
            note = (
                "\n⚠️ No staff role is set yet — run `/ticket setup` so staff "
                "get pulled into new tickets."
            )
        await interaction.response.send_message(
            embed=h.ok(f"Panel posted in {target.mention}.{note}"), ephemeral=True
        )

    @ticket.command(
        name="limit",
        description="Set how many tickets one member can have open at once.",
    )
    @app_commands.describe(amount="Open tickets allowed per member (1-10)")
    async def ticket_limit(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[int, 1, 10],
    ):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                embed=h.err("You need **Manage Server** to change the ticket limit."),
                ephemeral=True,
            )
            return
        await db.set_ticket_config(interaction.guild.id, max_open=amount)
        await interaction.response.send_message(
            embed=h.ok(f"Members can now have up to **{amount}** ticket(s) open."),
            ephemeral=True,
        )

    @ticket.command(name="config", description="Show the ticket settings.")
    async def ticket_config(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                embed=h.err("You need **Manage Server** to view the ticket config."),
                ephemeral=True,
            )
            return
        guild = interaction.guild
        cfg = await db.get_ticket_config(guild.id)
        staff = f"<@&{cfg['staff_role_id']}>" if cfg["staff_role_id"] else "*not set*"
        if cfg["staff_role_id"] and guild.get_role(int(cfg["staff_role_id"])) is None:
            staff += " ⚠️ (role deleted — only Manage Server counts as staff)"
        log_ch = f"<#{cfg['log_channel_id']}>" if cfg["log_channel_id"] else "*not set*"
        panel = (
            f"<#{cfg['panel_channel_id']}>"
            if cfg["panel_channel_id"]
            else "*not posted*"
        )
        embed = h.embed(
            "🎫 Ticket Settings",
            f"**Enabled:** {'yes' if cfg['enabled'] else 'no'}\n"
            f"**Staff role:** {staff}\n"
            f"**Log channel:** {log_ch}\n"
            f"**Panel:** {panel}\n"
            f"**Open-ticket limit:** {cfg['max_open']} per member",
            h.BLUE,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ticket.command(name="enable", description="Turn the ticket system on.")
    async def ticket_enable(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                embed=h.err("You need **Manage Server** to enable tickets."),
                ephemeral=True,
            )
            return
        await db.set_ticket_config(interaction.guild.id, enabled=True)
        await interaction.response.send_message(
            embed=h.ok("Tickets are enabled."), ephemeral=True
        )

    @ticket.command(
        name="disable",
        description="Turn the ticket system off (open tickets stay usable).",
    )
    async def ticket_disable(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                embed=h.err("You need **Manage Server** to disable tickets."),
                ephemeral=True,
            )
            return
        await db.set_ticket_config(interaction.guild.id, enabled=False)
        await interaction.response.send_message(
            embed=h.ok(
                "Tickets are disabled — the panel button will politely turn "
                "members away. Existing tickets can still be worked and closed."
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
