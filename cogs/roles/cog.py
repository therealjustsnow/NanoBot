"""
cogs/roles.py — v1.1.1
Button-based self-assignable role panels — designed for mobile.

Panels are persistent (survive bot restarts) and posted to any channel.

Modes:
  toggle  — click to add, click again to remove (default)
  single  — radio-style: picking a role removes any other role from the same panel

Commands (all /roles, require Manage Roles):
  /roles panel create          — Create a new panel (not yet posted)
  /roles panel post            — Post or re-post a panel to a channel
  /roles panel edit            — Edit the title / description / mode
  /roles panel delete          — Delete a panel and its message
  /roles panel list            — List all panels in this server
  /roles panel reload          — Re-post all panels to refresh their messages

  /roles add                   — Add a role to a panel
  /roles remove                — Remove a role from a panel

  /roles autogen colors        — Generate 18 cosmetic colour roles + panel
  /roles autogen pronouns      — Generate She/Her, He/Him, They/Them, It/Its, Any/All + panel
  /roles autogen age           — Generate age-range roles + panel
  /roles autogen region        — Generate 7 world-region roles + panel

  All autogen commands accept up to 5 extra existing roles to append to the panel.
  Only one autogen can run at a time per server — a second attempt is rejected
  immediately rather than queuing.
"""

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import db
from utils import helpers as h
from utils.checks import has_admin_perms, has_role_perms

from .constants import _AUTOGEN_CFG
from .helpers import _new_id
from .views import _build_embed, _build_view
from .autogen import _panel_autocomplete, _run_autogen

log = logging.getLogger("NanoBot.roles")


class Roles(commands.Cog):
    """Button-based self-assignable role panels."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        """Re-register all persistent views on startup.

        We register without message_id so discord.py routes by custom_id rather
        than message_id. Our custom_ids are globally unique (rp:{panel_id}:{role_id})
        so there's no ambiguity, and this avoids silent misses caused by stale
        message_ids in the database.
        """
        panels = await db.get_all_role_panels()
        registered = 0
        for panel in panels:
            view = _build_view(panel)
            try:
                self.bot.add_view(view)
                registered += 1
            except Exception as exc:
                log.warning(f"Could not register role panel view {panel['id']}: {exc}")
        log.info(f"Registered {registered} persistent role panel view(s)")

    # ── /roles group ───────────────────────────────────────────────────────────
    roles_group = app_commands.Group(
        name="roles",
        description="Self-assignable role panels.",
        default_permissions=discord.Permissions(manage_roles=True),
        guild_only=True,
    )

    # ── /roles panel subgroup ──────────────────────────────────────────────────
    panel_group = app_commands.Group(
        name="panel",
        description="Create and manage role panels.",
        parent=roles_group,
    )

    @panel_group.command(
        name="create", description="Create a new role panel (not posted yet)."
    )
    @app_commands.describe(
        title="Panel title shown to members",
        description="Optional subtitle / instructions",
        mode="toggle = add/remove freely | single = only one role at a time",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Toggle (add or remove freely)", value="toggle"),
            app_commands.Choice(
                name="Single (radio — one role at a time)", value="single"
            ),
        ]
    )
    @has_role_perms()
    async def panel_create(
        self,
        interaction: discord.Interaction,
        title: str,
        description: Optional[str] = None,
        mode: str = "toggle",
    ):
        panel_id = _new_id()
        await db.create_role_panel(
            panel_id=panel_id,
            guild_id=interaction.guild_id,
            title=title,
            description=description,
            mode=mode,
        )
        await interaction.response.send_message(
            embed=h.ok(
                f"Panel **{title}** created (ID: `{panel_id}`).\n\n"
                f"Add roles with `/roles add panel_id:{panel_id} role:@Role`\n"
                f"Then post it with `/roles panel post panel_id:{panel_id}`",
                "✅ Panel Created",
            ),
            ephemeral=True,
        )

    @panel_group.command(
        name="post", description="Post (or re-post) a panel to a channel."
    )
    @app_commands.describe(
        panel_id="Panel to post",
        channel="Channel to post in (default: current channel)",
    )
    @app_commands.autocomplete(panel_id=_panel_autocomplete)
    @has_role_perms()
    async def panel_post(
        self,
        interaction: discord.Interaction,
        panel_id: str,
        channel: Optional[discord.TextChannel] = None,
    ):
        panel = await db.get_role_panel(panel_id)
        if not panel or panel["guild_id"] != str(interaction.guild_id):
            return await interaction.response.send_message(
                embed=h.err(f"No panel with ID `{panel_id}` found."), ephemeral=True
            )
        if not panel["entries"]:
            return await interaction.response.send_message(
                embed=h.err(
                    f"Panel `{panel_id}` has no roles yet.\n"
                    f"Add some with `/roles add panel_id:{panel_id} role:@Role`"
                ),
                ephemeral=True,
            )

        target_ch = channel or interaction.channel

        if panel.get("message_id") and panel.get("channel_id"):
            old_ch = interaction.guild.get_channel(int(panel["channel_id"]))
            if old_ch:
                try:
                    old_msg = await old_ch.fetch_message(int(panel["message_id"]))
                    await old_msg.delete()
                except (discord.NotFound, discord.HTTPException):
                    pass

        view = _build_view(panel)
        try:
            msg = await target_ch.send(embed=_build_embed(panel), view=view)
        except discord.Forbidden:
            return await interaction.response.send_message(
                embed=h.err(f"I don't have permission to post in {target_ch.mention}."),
                ephemeral=True,
            )

        self.bot.add_view(view)
        await db.update_role_panel_message(panel_id, target_ch.id, msg.id)

        await interaction.response.send_message(
            embed=h.ok(
                f"Panel **{panel['title']}** posted in {target_ch.mention}.",
                "📋 Panel Posted",
            ),
            ephemeral=True,
        )

    @panel_group.command(
        name="edit", description="Edit a panel's title, description, or mode."
    )
    @app_commands.describe(
        panel_id="Panel to edit",
        title="New title",
        description="New description",
        mode="toggle or single",
    )
    @app_commands.autocomplete(panel_id=_panel_autocomplete)
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Toggle (add or remove freely)", value="toggle"),
            app_commands.Choice(
                name="Single (radio — one role at a time)", value="single"
            ),
        ]
    )
    @has_role_perms()
    async def panel_edit(
        self,
        interaction: discord.Interaction,
        panel_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        mode: Optional[str] = None,
    ):
        panel = await db.get_role_panel(panel_id)
        if not panel or panel["guild_id"] != str(interaction.guild_id):
            return await interaction.response.send_message(
                embed=h.err(f"No panel with ID `{panel_id}` found."), ephemeral=True
            )

        await db.edit_role_panel(
            panel_id,
            title=title or panel["title"],
            description=(
                description if description is not None else panel.get("description")
            ),
            mode=mode or panel["mode"],
        )
        updated_panel = await db.get_role_panel(panel_id)
        await self._refresh_panel_message(interaction.guild, updated_panel)

        await interaction.response.send_message(
            embed=h.ok(f"Panel `{panel_id}` updated.", "✅ Panel Edited"),
            ephemeral=True,
        )

    @panel_group.command(
        name="delete", description="Delete a panel and remove its message."
    )
    @app_commands.describe(panel_id="Panel to delete")
    @app_commands.autocomplete(panel_id=_panel_autocomplete)
    @has_role_perms()
    async def panel_delete(self, interaction: discord.Interaction, panel_id: str):
        panel = await db.get_role_panel(panel_id)
        if not panel or panel["guild_id"] != str(interaction.guild_id):
            return await interaction.response.send_message(
                embed=h.err(f"No panel with ID `{panel_id}` found."), ephemeral=True
            )

        if panel.get("message_id") and panel.get("channel_id"):
            ch = interaction.guild.get_channel(int(panel["channel_id"]))
            if ch:
                try:
                    msg = await ch.fetch_message(int(panel["message_id"]))
                    await msg.delete()
                except (discord.NotFound, discord.HTTPException):
                    pass

        await db.delete_role_panel(panel_id)
        await interaction.response.send_message(
            embed=h.ok(
                f"Panel **{panel['title']}** (`{panel_id}`) deleted.",
                "🗑️ Panel Deleted",
            ),
            ephemeral=True,
        )

    @panel_group.command(
        name="list", description="List all role panels in this server."
    )
    @has_role_perms()
    async def panel_list(self, interaction: discord.Interaction):
        panels = await db.get_role_panels_for_guild(interaction.guild_id)
        if not panels:
            return await interaction.response.send_message(
                embed=h.info(
                    "No panels yet.\nCreate one with `/roles panel create`.",
                    "📋 Role Panels",
                ),
                ephemeral=True,
            )

        lines = []
        for p in panels:
            ch_mention = (
                f"<#{p['channel_id']}>" if p.get("channel_id") else "_not posted_"
            )
            lines.append(
                f"**{p['title']}** · `{p['id']}` · {len(p['entries'])} role(s) · "
                f"mode: {p['mode']} · {ch_mention}"
            )

        await interaction.response.send_message(
            embed=h.embed("📋 Role Panels", "\n".join(lines), h.BLUE),
            ephemeral=True,
        )

    @panel_group.command(
        name="reload",
        description="Re-post all panels in this server to refresh their messages.",
    )
    @has_role_perms()
    async def panel_reload(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        panels = await db.get_role_panels_for_guild(interaction.guild_id)
        if not panels:
            return await interaction.followup.send(
                embed=h.info("No panels found for this server.", "📋 No Panels"),
                ephemeral=True,
            )

        posted, skipped, failed = [], [], []

        for panel in panels:
            if not panel.get("entries"):
                skipped.append(f"`{panel['id']}` **{panel['title']}** — no roles")
                continue

            # Delete old message if we know where it is
            if panel.get("message_id") and panel.get("channel_id"):
                old_ch = interaction.guild.get_channel(int(panel["channel_id"]))
                if old_ch:
                    try:
                        old_msg = await old_ch.fetch_message(int(panel["message_id"]))
                        await old_msg.delete()
                    except (discord.NotFound, discord.HTTPException):
                        pass

            # Figure out which channel to post in
            target_ch = None
            if panel.get("channel_id"):
                target_ch = interaction.guild.get_channel(int(panel["channel_id"]))
            if target_ch is None:
                # Fall back to the channel this command was run in
                target_ch = interaction.channel

            view = _build_view(panel)
            try:
                msg = await target_ch.send(embed=_build_embed(panel), view=view)
                self.bot.add_view(view)
                await db.update_role_panel_message(panel["id"], target_ch.id, msg.id)
                posted.append(
                    f"`{panel['id']}` **{panel['title']}** → {target_ch.mention}"
                )
                log.info(
                    f"panel reload: posted {panel['id']} to #{target_ch} in {interaction.guild}"
                )
            except discord.Forbidden:
                failed.append(
                    f"`{panel['id']}` **{panel['title']}** — no permission in {target_ch.mention}"
                )
            except Exception as exc:
                failed.append(f"`{panel['id']}` **{panel['title']}** — error: {exc}")
                log.error(
                    f"panel reload: failed to post {panel['id']}: {exc}", exc_info=exc
                )

        lines = []
        if posted:
            lines.append(f"✅ **Posted {len(posted)}:**\n" + "\n".join(posted))
        if skipped:
            lines.append(
                f"⏭️ **Skipped {len(skipped)} (no roles yet):**\n" + "\n".join(skipped)
            )
        if failed:
            lines.append(f"❌ **Failed {len(failed)}:**\n" + "\n".join(failed))

        await interaction.followup.send(
            embed=(
                h.ok("\n\n".join(lines), "📋 Panels Reloaded")
                if not failed
                else h.warn("\n\n".join(lines), "📋 Panels Reloaded")
            ),
            ephemeral=True,
        )

    # ── /roles add ─────────────────────────────────────────────────────────────
    @roles_group.command(name="add", description="Add a role to a panel.")
    @app_commands.describe(
        panel_id="Panel to add the role to",
        role="The role to add",
        label="Button label (defaults to role name)",
        emoji="Button emoji e.g. 🔴 (optional)",
        style="Button colour",
    )
    @app_commands.autocomplete(panel_id=_panel_autocomplete)
    @app_commands.choices(
        style=[
            app_commands.Choice(name="Grey (default)", value="secondary"),
            app_commands.Choice(name="Blue (blurple)", value="primary"),
            app_commands.Choice(name="Green", value="success"),
            app_commands.Choice(name="Red", value="danger"),
        ]
    )
    @has_role_perms()
    async def roles_add(
        self,
        interaction: discord.Interaction,
        panel_id: str,
        role: discord.Role,
        label: Optional[str] = None,
        emoji: Optional[str] = None,
        style: str = "secondary",
    ):
        panel = await db.get_role_panel(panel_id)
        if not panel or panel["guild_id"] != str(interaction.guild_id):
            return await interaction.response.send_message(
                embed=h.err(f"No panel with ID `{panel_id}` found."), ephemeral=True
            )
        if len(panel["entries"]) >= 25:
            return await interaction.response.send_message(
                embed=h.err("Panels support a maximum of 25 roles."), ephemeral=True
            )
        if any(e["role_id"] == role.id for e in panel["entries"]):
            return await interaction.response.send_message(
                embed=h.warn(f"**{role.name}** is already on this panel."),
                ephemeral=True,
            )
        if role >= interaction.guild.me.top_role:
            return await interaction.response.send_message(
                embed=h.err(
                    f"**{role.name}** is above my highest role — I won't be able to assign it.\n"
                    "Move my role above it first, then add it to the panel."
                ),
                ephemeral=True,
            )
        # Anyone can click a posted panel, so the button effectively hands the
        # role to the whole server. A role's *position* doesn't matter here —
        # colour roles often sit above mods (Discord shows the highest role's
        # colour), and a position-only role grants no power. What matters is
        # permissions: refuse any role carrying server-/member-control perms so a
        # panel can never be turned into a self-serve admin button.
        risky = h.dangerous_role_perms(role)
        if risky:
            pretty = ", ".join(p.replace("_", " ").title() for p in risky)
            return await interaction.response.send_message(
                embed=h.err(
                    f"**{role.name}** grants powerful permissions ({pretty}) and can't be "
                    "added to a self-assign panel. Anyone could click to grant it themselves."
                ),
                ephemeral=True,
            )

        await db.add_role_to_panel(
            panel_id,
            {
                "role_id": role.id,
                "label": label or role.name,
                "emoji": emoji,
                "style": style,
            },
        )
        updated_panel = await db.get_role_panel(panel_id)
        await self._refresh_panel_message(interaction.guild, updated_panel)

        await interaction.response.send_message(
            embed=h.ok(
                f"Added **{role.name}** to panel **{panel['title']}**.",
                "✅ Role Added to Panel",
            ),
            ephemeral=True,
        )

    # ── /roles remove ──────────────────────────────────────────────────────────
    @roles_group.command(name="remove", description="Remove a role from a panel.")
    @app_commands.describe(
        panel_id="Panel to remove the role from",
        role="The role to remove",
    )
    @app_commands.autocomplete(panel_id=_panel_autocomplete)
    @has_role_perms()
    async def roles_remove(
        self,
        interaction: discord.Interaction,
        panel_id: str,
        role: discord.Role,
    ):
        panel = await db.get_role_panel(panel_id)
        if not panel or panel["guild_id"] != str(interaction.guild_id):
            return await interaction.response.send_message(
                embed=h.err(f"No panel with ID `{panel_id}` found."), ephemeral=True
            )
        if not any(e["role_id"] == role.id for e in panel["entries"]):
            return await interaction.response.send_message(
                embed=h.warn(f"**{role.name}** is not on this panel."), ephemeral=True
            )

        await db.remove_role_from_panel(panel_id, role.id)
        updated_panel = await db.get_role_panel(panel_id)
        await self._refresh_panel_message(interaction.guild, updated_panel)

        await interaction.response.send_message(
            embed=h.ok(
                f"Removed **{role.name}** from panel **{panel['title']}**.",
                "✅ Role Removed from Panel",
            ),
            ephemeral=True,
        )

    # ── /roles autogen subgroup ────────────────────────────────────────────────
    autogen_group = app_commands.Group(
        name="autogen",
        description="Auto-generate common role sets and panels.",
        parent=roles_group,
    )

    @autogen_group.command(
        name="colors",
        description="Generate 18 cosmetic colour roles and a single-choice colour panel.",
    )
    @app_commands.describe(
        channel="Channel to post the panel in",
        prefix="Optional prefix for role names e.g. '🎨' → '🎨 Red'",
        extra_role_1="Extra existing role to append to the panel",
        extra_role_2="Extra existing role to append to the panel",
        extra_role_3="Extra existing role to append to the panel",
        extra_role_4="Extra existing role to append to the panel",
        extra_role_5="Extra existing role to append to the panel",
    )
    @has_admin_perms()
    async def autogen_colors(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        prefix: Optional[str] = None,
        extra_role_1: Optional[discord.Role] = None,
        extra_role_2: Optional[discord.Role] = None,
        extra_role_3: Optional[discord.Role] = None,
        extra_role_4: Optional[discord.Role] = None,
        extra_role_5: Optional[discord.Role] = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        title, desc, mode, palette = _AUTOGEN_CFG["colors"]
        extras = [
            r
            for r in [
                extra_role_1,
                extra_role_2,
                extra_role_3,
                extra_role_4,
                extra_role_5,
            ]
            if r
        ]
        await _run_autogen(
            self,
            interaction,
            channel,
            palette,
            title,
            desc,
            mode,
            prefix,
            extras,
            "colors",
        )

    @autogen_group.command(
        name="pronouns",
        description="Generate She/Her, He/Him, They/Them, It/Its, Any/All roles and a panel.",
    )
    @app_commands.describe(
        channel="Channel to post the panel in",
        extra_role_1="Extra existing role to append to the panel",
        extra_role_2="Extra existing role to append to the panel",
        extra_role_3="Extra existing role to append to the panel",
        extra_role_4="Extra existing role to append to the panel",
        extra_role_5="Extra existing role to append to the panel",
    )
    @has_admin_perms()
    async def autogen_pronouns(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        extra_role_1: Optional[discord.Role] = None,
        extra_role_2: Optional[discord.Role] = None,
        extra_role_3: Optional[discord.Role] = None,
        extra_role_4: Optional[discord.Role] = None,
        extra_role_5: Optional[discord.Role] = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        title, desc, mode, palette = _AUTOGEN_CFG["pronouns"]
        extras = [
            r
            for r in [
                extra_role_1,
                extra_role_2,
                extra_role_3,
                extra_role_4,
                extra_role_5,
            ]
            if r
        ]
        await _run_autogen(
            self,
            interaction,
            channel,
            palette,
            title,
            desc,
            mode,
            None,
            extras,
            "pronouns",
        )

    @autogen_group.command(
        name="age",
        description="Generate age-range roles (13-17, 18-20, 21-25, 26-30, 31+) and a panel.",
    )
    @app_commands.describe(
        channel="Channel to post the panel in",
        extra_role_1="Extra existing role to append to the panel",
        extra_role_2="Extra existing role to append to the panel",
        extra_role_3="Extra existing role to append to the panel",
        extra_role_4="Extra existing role to append to the panel",
        extra_role_5="Extra existing role to append to the panel",
    )
    @has_admin_perms()
    async def autogen_age(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        extra_role_1: Optional[discord.Role] = None,
        extra_role_2: Optional[discord.Role] = None,
        extra_role_3: Optional[discord.Role] = None,
        extra_role_4: Optional[discord.Role] = None,
        extra_role_5: Optional[discord.Role] = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        title, desc, mode, palette = _AUTOGEN_CFG["age"]
        extras = [
            r
            for r in [
                extra_role_1,
                extra_role_2,
                extra_role_3,
                extra_role_4,
                extra_role_5,
            ]
            if r
        ]
        await _run_autogen(
            self, interaction, channel, palette, title, desc, mode, None, extras, "age"
        )

    @autogen_group.command(
        name="region",
        description="Generate 7 world-region roles (N. America, Europe, Asia…) and a panel.",
    )
    @app_commands.describe(
        channel="Channel to post the panel in",
        extra_role_1="Extra existing role to append to the panel",
        extra_role_2="Extra existing role to append to the panel",
        extra_role_3="Extra existing role to append to the panel",
        extra_role_4="Extra existing role to append to the panel",
        extra_role_5="Extra existing role to append to the panel",
    )
    @has_admin_perms()
    async def autogen_region(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        extra_role_1: Optional[discord.Role] = None,
        extra_role_2: Optional[discord.Role] = None,
        extra_role_3: Optional[discord.Role] = None,
        extra_role_4: Optional[discord.Role] = None,
        extra_role_5: Optional[discord.Role] = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        title, desc, mode, palette = _AUTOGEN_CFG["region"]
        extras = [
            r
            for r in [
                extra_role_1,
                extra_role_2,
                extra_role_3,
                extra_role_4,
                extra_role_5,
            ]
            if r
        ]
        await _run_autogen(
            self,
            interaction,
            channel,
            palette,
            title,
            desc,
            mode,
            None,
            extras,
            "region",
        )

    # ── Internal: refresh a live panel message ─────────────────────────────────
    async def _refresh_panel_message(self, guild: discord.Guild, panel: dict) -> None:
        """Edit the posted panel message to reflect current entries / title."""
        if not panel.get("message_id") or not panel.get("channel_id"):
            return
        ch = guild.get_channel(int(panel["channel_id"]))
        if not ch:
            return
        try:
            msg = await ch.fetch_message(int(panel["message_id"]))
            view = _build_view(panel)
            await msg.edit(embed=_build_embed(panel), view=view)
            self.bot.add_view(view)
        except (discord.NotFound, discord.HTTPException) as exc:
            log.debug(f"Could not refresh panel message {panel['id']}: {exc}")


# ── Setup ──────────────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(Roles(bot))
