"""Role-panel UI: the persistent RoleButton plus the view/embed factories."""

import logging

import discord

from utils import db
from utils import helpers as h

from .helpers import _encode_cid

log = logging.getLogger("NanoBot.roles")


# ── Panel button ───────────────────────────────────────────────────────────────
class RoleButton(discord.ui.Button):
    def __init__(self, panel_id: str, entry: dict):
        label = entry.get("label") or "Role"
        emoji = entry.get("emoji") or None
        style_map = {
            "primary": discord.ButtonStyle.primary,
            "success": discord.ButtonStyle.success,
            "danger": discord.ButtonStyle.danger,
            "secondary": discord.ButtonStyle.secondary,
        }
        style = style_map.get(
            entry.get("style", "secondary"), discord.ButtonStyle.secondary
        )
        super().__init__(
            label=label,
            emoji=emoji,
            style=style,
            custom_id=_encode_cid(panel_id, entry["role_id"]),
        )
        self._role_id = entry["role_id"]
        self._panel_id = panel_id

    async def callback(self, interaction: discord.Interaction):
        # Defer immediately — this acknowledges the interaction within Discord's
        # 3-second window regardless of how long the DB/role ops take. Any
        # exception after this point still results in a visible error message
        # instead of a silent "interaction failed".
        await interaction.response.defer(ephemeral=True)

        try:
            panel = await db.get_role_panel(self._panel_id)
            if panel is None:
                await interaction.followup.send(
                    "This panel no longer exists.", ephemeral=True
                )
                return

            member = interaction.user
            guild = interaction.guild
            role = guild.get_role(self._role_id)

            if role is None:
                await interaction.followup.send(
                    "That role no longer exists — ask a mod to update this panel.",
                    ephemeral=True,
                )
                return

            if guild.me is None:
                await interaction.followup.send(
                    "Something went wrong — couldn't resolve bot member. Try again.",
                    ephemeral=True,
                )
                return

            if role >= guild.me.top_role:
                await interaction.followup.send(
                    f"I can't assign **{role.name}** — it's above my highest role. "
                    "Ask an admin to move my role up.",
                    ephemeral=True,
                )
                return

            has_role = role in member.roles

            if has_role:
                try:
                    await member.remove_roles(role, reason="Role panel self-remove")
                except discord.Forbidden:
                    await interaction.followup.send(
                        "I don't have permission to remove that role.", ephemeral=True
                    )
                    return
                await interaction.followup.send(
                    embed=h.ok(
                        f"Removed **{role.name}** from your roles.", "✅ Role Removed"
                    ),
                    ephemeral=True,
                )
                log.debug(
                    f"Role panel: removed {role} from {h.user_log(member)} in {guild}"
                )
                return

            # Single mode — remove all other panel roles before assigning
            if panel["mode"] == "single":
                panel_role_ids = {e["role_id"] for e in panel["entries"]}
                roles_to_remove = [
                    r
                    for r in member.roles
                    if r.id in panel_role_ids and r.id != self._role_id
                ]
                if roles_to_remove:
                    try:
                        await member.remove_roles(
                            *roles_to_remove, reason="Role panel single-mode swap"
                        )
                    except discord.Forbidden:
                        pass

            try:
                await member.add_roles(role, reason="Role panel self-assign")
            except discord.Forbidden:
                await interaction.followup.send(
                    "I don't have permission to assign that role.", ephemeral=True
                )
                return

            await interaction.followup.send(
                embed=h.ok(f"You now have **{role.name}**.", "✅ Role Assigned"),
                ephemeral=True,
            )
            log.debug(f"Role panel: added {role} to {h.user_log(member)} in {guild}")

        except Exception as exc:
            log.error(
                f"Role panel button error: panel={self._panel_id} role={self._role_id} "
                f"user={interaction.user} ({interaction.user.id}): {exc}",
                exc_info=exc,
            )
            try:
                await interaction.followup.send(
                    "Something went wrong. Check the bot logs.", ephemeral=True
                )
            except Exception:
                pass


# ── Panel view / embed factories ───────────────────────────────────────────────
def _build_view(panel: dict) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for entry in panel["entries"]:
        view.add_item(RoleButton(panel["id"], entry))
    return view


def _build_embed(panel: dict) -> discord.Embed:
    mode_note = (
        "_Pick one — choosing a new option removes the previous one._"
        if panel["mode"] == "single"
        else "_Click a button to add or remove a role._"
    )
    desc = (panel.get("description") or "") + f"\n\n{mode_note}"
    e = discord.Embed(
        title=panel["title"],
        description=desc.strip(),
        color=h.BLUE,
    )
    e.set_footer(text="NanoBot Role Panel")
    return e
