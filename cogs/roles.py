"""
cogs/roles.py — v1.0.0
Button-based self-assignable role panels — designed for mobile.

Panels are persistent (survive bot restarts) and posted to any channel.

Modes:
  toggle  — click to add, click again to remove (default)
  single  — radio-style: picking a role removes any other role from the same panel

Commands (all /roles, require Manage Roles):
  /roles panel create    — Create a new panel (not yet posted)
  /roles panel post      — Post or re-post a panel to a channel
  /roles panel edit      — Edit the title / description / mode
  /roles panel delete    — Delete a panel and its message
  /roles panel list      — List all panels in this server

  /roles add             — Add a role to a panel
  /roles remove          — Remove a role from a panel

  /roles colorgen        — Auto-generate 18 cosmetic colour roles, create a
                           single-mode panel, auto-position roles below the
                           bot's own role, and post the panel.
"""

import asyncio
import logging
import random
import string
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils import db
from utils import helpers as h
from utils.checks import has_role_perms, has_admin_perms

log = logging.getLogger("NanoBot.roles")

# ── Colour palette for /roles colorgen ────────────────────────────────────────
# (name, hex_int) — chosen to look vibrant and distinct in Discord dark mode
COLOUR_PALETTE: list[tuple[str, int]] = [
    ("🔴 Red",        0xE74C3C),
    ("🟠 Orange",     0xE67E22),
    ("🟡 Yellow",     0xF4D03F),
    ("🟢 Green",      0x2ECC71),
    ("🌿 Mint",       0x1ABC9C),
    ("🔵 Blue",       0x3498DB),
    ("🌊 Cyan",       0x00BCD4),
    ("💙 Navy",       0x1F618D),
    ("🟣 Purple",     0x9B59B6),
    ("🔮 Violet",     0x6C3483),
    ("🩷 Pink",       0xFF6EB4),
    ("🌸 Rose",       0xE91E8C),
    ("🤎 Brown",      0xA0522D),
    ("🧡 Amber",      0xF39C12),
    ("🌻 Gold",       0xD4AC0D),
    ("🩶 Silver",     0x95A5A6),
    ("⬜ White",      0xECF0F1),
    ("🖤 Charcoal",   0x546E7A),
]

# ── ID generator ───────────────────────────────────────────────────────────────
def _new_id(length: int = 6) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


# ── Button custom_id encoding ──────────────────────────────────────────────────
# Format: "rp:{panel_id}:{role_id}"
# Survives restarts — no state lives in the view object itself.

def _encode_cid(panel_id: str, role_id: int) -> str:
    return f"rp:{panel_id}:{role_id}"


def _decode_cid(custom_id: str) -> tuple[str, int] | None:
    parts = custom_id.split(":")
    if len(parts) != 3 or parts[0] != "rp":
        return None
    try:
        return parts[1], int(parts[2])
    except ValueError:
        return None


# ── Panel button ───────────────────────────────────────────────────────────────
class RoleButton(discord.ui.Button):
    def __init__(self, panel_id: str, entry: dict):
        label = entry.get("label") or "Role"
        emoji = entry.get("emoji") or None
        style_map = {
            "primary":   discord.ButtonStyle.primary,
            "success":   discord.ButtonStyle.success,
            "danger":    discord.ButtonStyle.danger,
            "secondary": discord.ButtonStyle.secondary,
        }
        style = style_map.get(entry.get("style", "secondary"), discord.ButtonStyle.secondary)
        super().__init__(
            label=label,
            emoji=emoji,
            style=style,
            custom_id=_encode_cid(panel_id, entry["role_id"]),
        )
        self._role_id  = entry["role_id"]
        self._panel_id = panel_id

    async def callback(self, interaction: discord.Interaction):
        # Re-fetch panel from DB (always fresh — no stale cache risk)
        panel = await db.get_role_panel(self._panel_id)
        if panel is None:
            await interaction.response.send_message(
                "This panel no longer exists.", ephemeral=True
            )
            return

        member = interaction.user
        guild  = interaction.guild
        role   = guild.get_role(self._role_id)

        if role is None:
            await interaction.response.send_message(
                "That role no longer exists — ask a mod to update this panel.",
                ephemeral=True,
            )
            return

        # Hierarchy check — bot must be above the role
        if role >= guild.me.top_role:
            await interaction.response.send_message(
                f"I can't assign **{role.name}** — it's above my highest role. "
                "Ask an admin to move my role up.",
                ephemeral=True,
            )
            return

        has_role = role in member.roles

        if has_role:
            # Toggle off
            try:
                await member.remove_roles(role, reason="Role panel self-remove")
            except discord.Forbidden:
                await interaction.response.send_message(
                    "I don't have permission to remove that role.", ephemeral=True
                )
                return
            await interaction.response.send_message(
                embed=h.ok(f"Removed **{role.name}** from your roles.", "✅ Role Removed"),
                ephemeral=True,
            )
            log.debug(f"Role panel: removed {role} from {member} ({member.id}) in {guild}")
            return

        # Adding the role
        # In single mode — remove all other roles from this panel first
        if panel["mode"] == "single":
            panel_role_ids = {e["role_id"] for e in panel["entries"]}
            roles_to_remove = [
                r for r in member.roles
                if r.id in panel_role_ids and r.id != self._role_id
            ]
            if roles_to_remove:
                try:
                    await member.remove_roles(*roles_to_remove, reason="Role panel single-mode swap")
                except discord.Forbidden:
                    pass

        try:
            await member.add_roles(role, reason="Role panel self-assign")
        except discord.Forbidden:
            await interaction.response.send_message(
                "I don't have permission to assign that role.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=h.ok(f"You now have **{role.name}**.", "✅ Role Assigned"),
            ephemeral=True,
        )
        log.debug(f"Role panel: added {role} to {member} ({member.id}) in {guild}")


# ── Panel view factory ─────────────────────────────────────────────────────────
def _build_view(panel: dict) -> discord.ui.View:
    """Build a persistent View from a panel dict."""
    view = discord.ui.View(timeout=None)
    for entry in panel["entries"]:
        view.add_item(RoleButton(panel["id"], entry))
    return view


# ── Panel embed factory ────────────────────────────────────────────────────────
def _build_embed(panel: dict) -> discord.Embed:
    mode_note = (
        "_Pick one — choosing a new option removes the previous one._"
        if panel["mode"] == "single"
        else "_Click a button to add or remove a role._"
    )
    desc = (panel.get("description") or "") + f"\n\n{mode_note}"
    e = discord.Embed(
        title       = panel["title"],
        description = desc.strip(),
        color       = h.BLUE,
    )
    e.set_footer(text="NanoBot Role Panel")
    return e


# ── Autocomplete helpers ───────────────────────────────────────────────────────
async def _panel_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    panels = await db.get_role_panels_for_guild(interaction.guild_id)
    return [
        app_commands.Choice(name=f"{p['title']} ({p['id']})", value=p["id"])
        for p in panels
        if current.lower() in p["title"].lower() or current.lower() in p["id"]
    ][:25]


# ══════════════════════════════════════════════════════════════════════════════
class Roles(commands.Cog):
    """Button-based self-assignable role panels."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        """Re-register all persistent views on startup."""
        panels = await db.get_all_role_panels()
        registered = 0
        for panel in panels:
            if not panel.get("message_id"):
                continue
            view = _build_view(panel)
            try:
                self.bot.add_view(view, message_id=int(panel["message_id"]))
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

    @panel_group.command(name="create", description="Create a new role panel (not posted yet).")
    @app_commands.describe(
        title       = "Panel title shown to members",
        description = "Optional subtitle / instructions",
        mode        = "toggle = add/remove freely | single = only one role at a time",
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="Toggle (add or remove freely)", value="toggle"),
        app_commands.Choice(name="Single (radio — one role at a time)", value="single"),
    ])
    @has_role_perms()
    async def panel_create(
        self,
        interaction: discord.Interaction,
        title:       str,
        description: Optional[str] = None,
        mode:        str            = "toggle",
    ):
        panel_id = _new_id()
        await db.create_role_panel(
            panel_id       = panel_id,
            guild_id       = interaction.guild_id,
            title          = title,
            description    = description,
            mode           = mode,
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

    @panel_group.command(name="post", description="Post (or re-post) a panel to a channel.")
    @app_commands.describe(
        panel_id = "Panel to post",
        channel  = "Channel to post in (default: current channel)",
    )
    @app_commands.autocomplete(panel_id=_panel_autocomplete)
    @has_role_perms()
    async def panel_post(
        self,
        interaction: discord.Interaction,
        panel_id:    str,
        channel:     Optional[discord.TextChannel] = None,
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

        # Delete old message if it still exists
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

        self.bot.add_view(view, message_id=msg.id)
        await db.update_role_panel_message(panel_id, target_ch.id, msg.id)

        await interaction.response.send_message(
            embed=h.ok(
                f"Panel **{panel['title']}** posted in {target_ch.mention}.",
                "📋 Panel Posted",
            ),
            ephemeral=True,
        )

    @panel_group.command(name="edit", description="Edit a panel's title, description, or mode.")
    @app_commands.describe(
        panel_id    = "Panel to edit",
        title       = "New title",
        description = "New description",
        mode        = "toggle or single",
    )
    @app_commands.autocomplete(panel_id=_panel_autocomplete)
    @app_commands.choices(mode=[
        app_commands.Choice(name="Toggle (add or remove freely)", value="toggle"),
        app_commands.Choice(name="Single (radio — one role at a time)", value="single"),
    ])
    @has_role_perms()
    async def panel_edit(
        self,
        interaction: discord.Interaction,
        panel_id:    str,
        title:       Optional[str] = None,
        description: Optional[str] = None,
        mode:        Optional[str] = None,
    ):
        panel = await db.get_role_panel(panel_id)
        if not panel or panel["guild_id"] != str(interaction.guild_id):
            return await interaction.response.send_message(
                embed=h.err(f"No panel with ID `{panel_id}` found."), ephemeral=True
            )

        await db.edit_role_panel(
            panel_id,
            title       = title       or panel["title"],
            description = description if description is not None else panel.get("description"),
            mode        = mode        or panel["mode"],
        )

        # Refresh the live message if it exists
        updated_panel = await db.get_role_panel(panel_id)
        await self._refresh_panel_message(interaction.guild, updated_panel)

        await interaction.response.send_message(
            embed=h.ok(f"Panel `{panel_id}` updated.", "✅ Panel Edited"),
            ephemeral=True,
        )

    @panel_group.command(name="delete", description="Delete a panel and remove its message.")
    @app_commands.describe(panel_id="Panel to delete")
    @app_commands.autocomplete(panel_id=_panel_autocomplete)
    @has_role_perms()
    async def panel_delete(self, interaction: discord.Interaction, panel_id: str):
        panel = await db.get_role_panel(panel_id)
        if not panel or panel["guild_id"] != str(interaction.guild_id):
            return await interaction.response.send_message(
                embed=h.err(f"No panel with ID `{panel_id}` found."), ephemeral=True
            )

        # Delete the posted message
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
            embed=h.ok(f"Panel **{panel['title']}** (`{panel_id}`) deleted.", "🗑️ Panel Deleted"),
            ephemeral=True,
        )

    @panel_group.command(name="list", description="List all role panels in this server.")
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
            role_count = len(p["entries"])
            ch_mention = f"<#{p['channel_id']}>" if p.get("channel_id") else "_not posted_"
            lines.append(
                f"**{p['title']}** · `{p['id']}` · {role_count} role(s) · "
                f"mode: {p['mode']} · {ch_mention}"
            )

        e = h.embed("📋 Role Panels", "\n".join(lines), h.BLUE)
        await interaction.response.send_message(embed=e, ephemeral=True)

    # ── /roles add ─────────────────────────────────────────────────────────────
    @roles_group.command(name="add", description="Add a role to a panel.")
    @app_commands.describe(
        panel_id = "Panel to add the role to",
        role     = "The role to add",
        label    = "Button label (defaults to role name)",
        emoji    = "Button emoji e.g. 🔴 (optional)",
        style    = "Button colour",
    )
    @app_commands.autocomplete(panel_id=_panel_autocomplete)
    @app_commands.choices(style=[
        app_commands.Choice(name="Grey (default)",  value="secondary"),
        app_commands.Choice(name="Blue (blurple)",  value="primary"),
        app_commands.Choice(name="Green",           value="success"),
        app_commands.Choice(name="Red",             value="danger"),
    ])
    @has_role_perms()
    async def roles_add(
        self,
        interaction: discord.Interaction,
        panel_id:    str,
        role:        discord.Role,
        label:       Optional[str] = None,
        emoji:       Optional[str] = None,
        style:       str           = "secondary",
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
                embed=h.warn(f"**{role.name}** is already on this panel."), ephemeral=True
            )
        if role >= interaction.guild.me.top_role:
            return await interaction.response.send_message(
                embed=h.err(
                    f"**{role.name}** is above my highest role — I won't be able to assign it.\n"
                    "Move my role above it first, then add it to the panel."
                ),
                ephemeral=True,
            )

        entry = {
            "role_id": role.id,
            "label":   label or role.name,
            "emoji":   emoji,
            "style":   style,
        }
        await db.add_role_to_panel(panel_id, entry)

        # Refresh live message
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
        panel_id = "Panel to remove the role from",
        role     = "The role to remove",
    )
    @app_commands.autocomplete(panel_id=_panel_autocomplete)
    @has_role_perms()
    async def roles_remove(
        self,
        interaction: discord.Interaction,
        panel_id:    str,
        role:        discord.Role,
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

    # ── /roles colorgen ────────────────────────────────────────────────────────
    @roles_group.command(
        name="colorgen",
        description="Auto-generate cosmetic colour roles, position them, and create a panel.",
    )
    @app_commands.describe(
        channel = "Channel to post the colour panel in",
        prefix  = "Optional prefix for role names e.g. '🎨' → '🎨 Red'",
    )
    @has_admin_perms()
    async def colorgen(
        self,
        interaction: discord.Interaction,
        channel:     discord.TextChannel,
        prefix:      Optional[str] = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        existing_names = {r.name.lower() for r in guild.roles}

        # ── 1. Create roles ───────────────────────────────────────────────────
        # Target ~120s total (well within the 15-minute followup window).
        # 18 creates × 3.0s = ~54s, 3s pause, 18 positions × 3.5s = ~63s → ~120s.
        # Conservative enough that multiple servers running simultaneously won't
        # saturate Discord's upstream proxy.
        _CREATE_DELAY  = 3.0
        _RETRY_BACKOFF = 5.0
        _MAX_RETRIES   = 2

        created: list[discord.Role] = []
        skipped: list[str]          = []

        for name, colour in COLOUR_PALETTE:
            full_name = f"{prefix} {name}" if prefix else name
            if full_name.lower() in existing_names:
                skipped.append(full_name)
                continue

            role = None
            for attempt in range(1, _MAX_RETRIES + 1):
                try:
                    role = await guild.create_role(
                        name   = full_name,
                        colour = discord.Colour(colour),
                        reason = f"NanoBot colorgen by {interaction.user}",
                    )
                    break
                except discord.Forbidden:
                    await interaction.followup.send(
                        embed=h.err("I don't have Manage Roles permission."),
                        ephemeral=True,
                    )
                    return
                except discord.HTTPException as exc:
                    if attempt < _MAX_RETRIES:
                        log.warning(
                            f"colorgen: role '{full_name}' attempt {attempt} failed "
                            f"({exc}) — retrying in {_RETRY_BACKOFF}s"
                        )
                        await asyncio.sleep(_RETRY_BACKOFF)
                    else:
                        log.warning(
                            f"colorgen: failed to create '{full_name}' "
                            f"after {_MAX_RETRIES} attempts: {exc}"
                        )

            if role:
                created.append(role)
            await asyncio.sleep(_CREATE_DELAY)

        if not created:
            await interaction.followup.send(
                embed=h.warn(
                    "All colour roles already exist on this server. "
                    "No new roles were created.",
                    "⚠️ Nothing to do",
                ),
                ephemeral=True,
            )
            return

        # ── 2. Auto-position roles below the bot's top role ───────────────────
        # Longer pause after bulk creation before touching positions.
        # 3.5s between each edit — see timing breakdown above.
        await asyncio.sleep(3.0)

        bot_pos    = guild.me.top_role.position
        positioned = 0
        pos_failed = False

        for i, role in enumerate(created):
            target_pos = max(1, bot_pos - 1 - i)
            try:
                await role.edit(
                    position = target_pos,
                    reason   = "NanoBot colorgen: auto-positioning colour roles",
                )
                positioned += 1
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.warning(f"colorgen: could not position '{role.name}': {exc}")
                pos_failed = True
                break   # if one fails the rest will too — stop trying
            await asyncio.sleep(3.5)

        # ── 3. Create panel ───────────────────────────────────────────────────
        panel_id = _new_id()
        await db.create_role_panel(
            panel_id    = panel_id,
            guild_id    = guild.id,
            title       = "🎨 Pick a Colour",
            description = "Choose a colour role — you can only have one at a time.",
            mode        = "single",
        )

        for role in created:
            # Strip the prefix from the button label for a cleaner look
            label = role.name.replace(f"{prefix} ", "", 1) if prefix else role.name
            await db.add_role_to_panel(panel_id, {
                "role_id": role.id,
                "label":   label,
                "emoji":   None,
                "style":   "secondary",
            })

        # ── 4. Post the panel ─────────────────────────────────────────────────
        panel = await db.get_role_panel(panel_id)
        view  = _build_view(panel)
        try:
            msg = await channel.send(embed=_build_embed(panel), view=view)
            self.bot.add_view(view, message_id=msg.id)
            await db.update_role_panel_message(panel_id, channel.id, msg.id)
        except discord.Forbidden:
            await interaction.followup.send(
                embed=h.err(f"Roles created but I can't post in {channel.mention}."),
                ephemeral=True,
            )
            return

        # ── 5. Summary ────────────────────────────────────────────────────────
        lines = [
            f"Created **{len(created)}** colour role(s) and posted a panel in {channel.mention}.",
            "",
            f"🎨 Roles: {', '.join(r.mention for r in created[:10])}"
            + (f" … and {len(created) - 10} more" if len(created) > 10 else ""),
        ]
        if not pos_failed:
            lines.append(f"📐 Roles auto-positioned just below **{guild.me.top_role.name}**.")
        else:
            lines.append(
                f"⚠️ Positioned {positioned}/{len(created)} role(s) before hitting a permission "
                "error — move any remaining roles above your member roles manually so colours "
                "show in the member list."
            )
        if skipped:
            lines.append(f"⏭️ Skipped {len(skipped)} already-existing role(s).")

        await interaction.followup.send(
            embed=h.ok("\n".join(lines), "🎨 Colour Roles Generated"),
            ephemeral=True,
        )
        log.info(
            f"colorgen: created {len(created)} roles in {guild} ({guild.id}) "
            f"by {interaction.user} — positioned {positioned}/{len(created)}"
        )

    # ── Internal: refresh a live panel message ─────────────────────────────────
    async def _refresh_panel_message(
        self,
        guild:  discord.Guild,
        panel:  dict,
    ) -> None:
        """Edit the posted panel message to reflect current entries/title."""
        if not panel.get("message_id") or not panel.get("channel_id"):
            return
        ch = guild.get_channel(int(panel["channel_id"]))
        if not ch:
            return
        try:
            msg  = await ch.fetch_message(int(panel["message_id"]))
            view = _build_view(panel)
            await msg.edit(embed=_build_embed(panel), view=view)
            # Re-register the updated view
            self.bot.add_view(view, message_id=msg.id)
        except (discord.NotFound, discord.HTTPException) as exc:
            log.debug(f"Could not refresh panel message {panel['id']}: {exc}")


# ── Setup ──────────────────────────────────────────────────────────────────────
async def setup(bot: commands.Bot):
    await bot.add_cog(Roles(bot))
