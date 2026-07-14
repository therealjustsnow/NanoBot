"""
cogs/liverole.py
Streaming presence reactions — live role + go-live notifications.

Driven entirely by PRESENCE_UPDATE (the privileged presences intent). When a
member starts streaming (Twitch / YouTube live, or any "Streaming" status):
  • optionally gains a configured role for the duration of the stream, and
  • optionally triggers a go-live announcement in a configured channel.
When the member stops streaming the role is removed again.

Commands (all /liverole, require Manage Roles):
  /liverole setup [role] [channel]  — set the live role and/or announce channel
  /liverole enable / disable        — master on/off switch
  /liverole role <role>             — set the role granted while live
  /liverole channel <channel>       — set the go-live announcement channel
  /liverole announce <on/off>       — toggle go-live announcements
  /liverole message <text>          — customize the announcement template
  /liverole status                  — show current configuration
"""

import logging

import discord
from discord import app_commands
from discord.ext import commands

from utils import db
from utils import helpers as h
from utils.checks import has_role_perms
from utils.converters import SafeTextChannel

log = logging.getLogger("NanoBot.liverole")

# Template variables supported in the go-live announcement.
_VARS_HELP = (
    "`{mention}` — ping  ·  `{user}` — display name  ·  "
    "`{title}` — stream title  ·  `{url}` — stream link  ·  `{game}` — game / category"
)
_DEFAULT_MESSAGE = "🔴 {mention} is now **live**! {title}\n{url}"


def _is_streaming(member: discord.Member) -> bool:
    """True when any of the member's activities is a Streaming activity."""
    return any(
        getattr(a, "type", None) is discord.ActivityType.streaming
        for a in member.activities
    )


def _stream_activity(member: discord.Member):
    """Return the member's Streaming activity, or None."""
    for a in member.activities:
        if getattr(a, "type", None) is discord.ActivityType.streaming:
            return a
    return None


def _fill(template: str, member: discord.Member, activity) -> str:
    title = getattr(activity, "name", None) or "Live now"
    url = getattr(activity, "url", None) or ""
    game = getattr(activity, "game", None) or getattr(activity, "details", None) or ""
    return (
        template.replace("{mention}", member.mention)
        .replace("{user}", member.display_name)
        .replace("{title}", title)
        .replace("{url}", url)
        .replace("{game}", game)
    )


class LiveRole(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # on_ready can fire more than once (reconnects); reconcile only the first.
        self._reconciled = False

    # ── Startup reconciliation ────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # A member can start/stop streaming while the bot is down, so the
        # PRESENCE_UPDATE transition is never seen. Sweep every guild once at
        # startup and bring the live role in line with current presence. Roles
        # only — no go-live announcements fire here, or a restart would re-ping
        # everyone who is already live.
        if self._reconciled:
            return
        self._reconciled = True
        for guild in self.bot.guilds:
            try:
                await self._reconcile_guild(guild)
            except Exception:
                log.exception("Live-role reconcile failed for guild %s", guild.id)

    async def _reconcile_guild(self, guild: discord.Guild) -> None:
        cfg = await db.get_liverole_config(guild.id)
        if not cfg or not cfg["enabled"]:
            return
        role = self._resolve_role(guild, cfg)
        if role is None:
            return
        for member in guild.members:
            if member.bot:
                continue
            live = _is_streaming(member)
            has_role = role in member.roles
            if live and not has_role:
                try:
                    await member.add_roles(role, reason="Live-role reconcile (startup)")
                except (discord.Forbidden, discord.HTTPException):
                    pass
            elif has_role and not live:
                try:
                    await member.remove_roles(
                        role, reason="Live-role reconcile (startup)"
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass

    # ── Presence listener ─────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_presence_update(
        self, before: discord.Member, after: discord.Member
    ) -> None:
        if after.bot or after.guild is None:
            return

        was_live = _is_streaming(before)
        is_live = _is_streaming(after)
        if was_live == is_live:
            return  # no streaming transition — ignore

        cfg = await db.get_liverole_config(after.guild.id)
        if not cfg or not cfg["enabled"]:
            return

        if is_live:
            await self._on_go_live(after, cfg)
        else:
            await self._on_stop_live(after, cfg)

    async def _on_go_live(self, member: discord.Member, cfg: dict) -> None:
        # Grant the live role
        role = self._resolve_role(member.guild, cfg)
        if role and role not in member.roles:
            try:
                await member.add_roles(role, reason="Started streaming")
            except discord.Forbidden:
                log.warning(
                    "Missing permission to add live role in guild %s", member.guild.id
                )
            except discord.HTTPException:
                log.exception("Failed to add live role in guild %s", member.guild.id)

        # Announce
        if not cfg["announce"] or not cfg["channel_id"]:
            return
        channel = member.guild.get_channel(int(cfg["channel_id"]))
        if channel is None:
            return
        activity = _stream_activity(member)
        template = cfg["message"] or _DEFAULT_MESSAGE
        content = _fill(template, member, activity)
        try:
            await channel.send(
                content,
                allowed_mentions=discord.AllowedMentions(
                    users=[member], everyone=False, roles=False
                ),
            )
        except (discord.Forbidden, discord.HTTPException):
            log.warning(
                "Could not post go-live announcement in guild %s", member.guild.id
            )

    async def _on_stop_live(self, member: discord.Member, cfg: dict) -> None:
        role = self._resolve_role(member.guild, cfg)
        if role and role in member.roles:
            try:
                await member.remove_roles(role, reason="Stopped streaming")
            except discord.Forbidden:
                log.warning(
                    "Missing permission to remove live role in guild %s",
                    member.guild.id,
                )
            except discord.HTTPException:
                log.exception("Failed to remove live role in guild %s", member.guild.id)

    @staticmethod
    def _resolve_role(guild: discord.Guild, cfg: dict):
        if not cfg["role_id"]:
            return None
        return guild.get_role(int(cfg["role_id"]))

    # ── Slash group ───────────────────────────────────────────────────────────
    liverole = app_commands.Group(
        name="liverole",
        description="Live role + go-live notifications for streamers",
        guild_only=True,
        default_permissions=discord.Permissions(manage_roles=True),
    )

    @liverole.command(name="setup", description="Set the live role and/or channel")
    @app_commands.describe(
        role="Role granted to members while they are live",
        channel="Channel for go-live announcements",
    )
    @has_role_perms()
    async def lr_setup(
        self,
        interaction: discord.Interaction,
        role: discord.Role = None,
        channel: SafeTextChannel = None,
    ) -> None:
        if role is None and channel is None:
            return await interaction.response.send_message(
                embed=h.err("Provide a role, a channel, or both."), ephemeral=True
            )
        if role is not None:
            if not self._role_assignable(interaction.guild, role):
                return await interaction.response.send_message(
                    embed=h.err(
                        "I can't manage that role — it's above my highest role or managed by an integration."
                    ),
                    ephemeral=True,
                )
            await db.set_liverole_role(interaction.guild.id, role.id)
        if channel is not None:
            await db.set_liverole_channel(interaction.guild.id, channel.id)

        parts = []
        if role is not None:
            parts.append("Live role set to " + role.mention)
        if channel is not None:
            parts.append("Announcements set to " + channel.mention)
        parts.append("\nRun `/liverole enable` to turn it on.")
        await interaction.response.send_message(
            embed=h.ok("\n".join(parts)), ephemeral=True
        )

    @liverole.command(name="enable", description="Enable live-role tracking")
    @has_role_perms()
    async def lr_enable(self, interaction: discord.Interaction) -> None:
        cfg = await db.get_liverole_config(interaction.guild.id)
        if not cfg or (not cfg["role_id"] and not cfg["channel_id"]):
            return await interaction.response.send_message(
                embed=h.err("Nothing configured yet — run `/liverole setup` first."),
                ephemeral=True,
            )
        await db.set_liverole_enabled(interaction.guild.id, True)
        await interaction.response.send_message(
            embed=h.ok("Live-role tracking is now **on**."), ephemeral=True
        )

    @liverole.command(name="disable", description="Disable live-role tracking")
    @has_role_perms()
    async def lr_disable(self, interaction: discord.Interaction) -> None:
        await db.set_liverole_enabled(interaction.guild.id, False)
        await interaction.response.send_message(
            embed=h.ok("Live-role tracking is now **off**. Settings are preserved."),
            ephemeral=True,
        )

    @liverole.command(name="role", description="Set the role granted while live")
    @app_commands.describe(role="The role to grant while a member is streaming")
    @has_role_perms()
    async def lr_role(
        self, interaction: discord.Interaction, role: discord.Role
    ) -> None:
        if not self._role_assignable(interaction.guild, role):
            return await interaction.response.send_message(
                embed=h.err(
                    "I can't manage that role — it's above my highest role or managed by an integration."
                ),
                ephemeral=True,
            )
        await db.set_liverole_role(interaction.guild.id, role.id)
        await interaction.response.send_message(
            embed=h.ok("Live role set to " + role.mention + "."), ephemeral=True
        )

    @liverole.command(
        name="channel", description="Set the go-live announcement channel"
    )
    @app_commands.describe(channel="Channel that receives go-live announcements")
    @has_role_perms()
    async def lr_channel(
        self, interaction: discord.Interaction, channel: SafeTextChannel
    ) -> None:
        await db.set_liverole_channel(interaction.guild.id, channel.id)
        await interaction.response.send_message(
            embed=h.ok("Go-live announcements will post to " + channel.mention + "."),
            ephemeral=True,
        )

    @liverole.command(name="announce", description="Toggle go-live announcements")
    @app_commands.describe(enabled="Turn go-live announcements on or off")
    @has_role_perms()
    async def lr_announce(
        self, interaction: discord.Interaction, enabled: bool
    ) -> None:
        await db.set_liverole_announce(interaction.guild.id, enabled)
        state = "on" if enabled else "off"
        await interaction.response.send_message(
            embed=h.ok("Go-live announcements are now **" + state + "**."),
            ephemeral=True,
        )

    @liverole.command(name="message", description="Customize the announcement text")
    @app_commands.describe(text="Template — variables: " + _VARS_HELP)
    @has_role_perms()
    async def lr_message(self, interaction: discord.Interaction, text: str) -> None:
        if len(text) > 1500:
            return await interaction.response.send_message(
                embed=h.err("Message is too long (max 1500 characters)."),
                ephemeral=True,
            )
        await db.set_liverole_message(interaction.guild.id, text)
        await interaction.response.send_message(
            embed=h.ok(
                "Announcement message updated.\n\nPreview variables: " + _VARS_HELP
            ),
            ephemeral=True,
        )

    @liverole.command(name="status", description="Show the current configuration")
    @has_role_perms()
    async def lr_status(self, interaction: discord.Interaction) -> None:
        cfg = await db.get_liverole_config(interaction.guild.id)
        if not cfg:
            return await interaction.response.send_message(
                embed=h.info("Live-role is not set up. Run `/liverole setup`."),
                ephemeral=True,
            )
        role = self._resolve_role(interaction.guild, cfg)
        channel = (
            interaction.guild.get_channel(int(cfg["channel_id"]))
            if cfg["channel_id"]
            else None
        )
        e = h.embed(title="🔴 Live Role Status", color=h.BLUE)
        e.add_field(
            name="Status",
            value="🟢 Enabled" if cfg["enabled"] else "🔴 Disabled",
            inline=True,
        )
        e.add_field(
            name="Live role",
            value=role.mention if role else "_not set_",
            inline=True,
        )
        e.add_field(
            name="Announcements",
            value=("🟢 On" if cfg["announce"] else "🔴 Off")
            + (" → " + channel.mention if channel else " (no channel)"),
            inline=False,
        )
        e.add_field(
            name="Message",
            value="`" + (cfg["message"] or _DEFAULT_MESSAGE) + "`",
            inline=False,
        )
        await interaction.response.send_message(embed=e, ephemeral=True)

    @staticmethod
    def _role_assignable(guild: discord.Guild, role: discord.Role) -> bool:
        """The bot can grant/revoke a role only if it's below the bot's top role
        and not integration-managed."""
        me = guild.me
        if me is None:
            return False
        return not role.managed and not role.is_default() and role < me.top_role


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LiveRole(bot))
