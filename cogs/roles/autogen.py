"""Role-panel command support: the panel autocomplete and the shared autogen
engine behind the four /roles autogen commands.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands

from utils import db
from utils import helpers as h

from .helpers import _get_autogen_lock, _new_id
from .views import _build_embed, _build_view

if TYPE_CHECKING:
    from .cog import Roles

log = logging.getLogger("NanoBot.roles")


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


# ── Shared autogen engine ──────────────────────────────────────────────────────
async def _run_autogen(
    cog: "Roles",
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    palette: list[tuple[str, int | None]],
    panel_title: str,
    panel_desc: str,
    mode: str,
    prefix: str | None,
    extra_roles: list[discord.Role],
    kind: str,
) -> None:
    """
    Core autogen logic shared by all four /roles autogen commands.
    Must be called after interaction.response.defer().
    """
    lock = _get_autogen_lock(interaction.guild_id)

    if lock.locked():
        await interaction.followup.send(
            embed=h.err(
                "An autogen is already running on this server.\n"
                "Wait for it to finish before starting another.",
                "⏳ Already Running",
            ),
            ephemeral=True,
        )
        return

    async with lock:
        guild = interaction.guild
        existing_names = {r.name.lower() for r in guild.roles}

        _CREATE_DELAY = 3.0
        _POS_DELAY = 3.5
        _PAUSE_BEFORE_POS = 3.0
        _RETRY_BACKOFF = 5.0
        _MAX_RETRIES = 2

        # ── 1. Create roles ────────────────────────────────────────────────────
        created: list[discord.Role] = []
        skipped: list[str] = []

        for name, colour in palette:
            full_name = f"{prefix} {name}" if prefix else name
            if full_name.lower() in existing_names:
                skipped.append(full_name)
                continue

            role = None
            for attempt in range(1, _MAX_RETRIES + 1):
                try:
                    kwargs: dict = dict(
                        name=full_name,
                        reason=f"NanoBot autogen ({kind}) by {interaction.user}",
                    )
                    if colour is not None:
                        kwargs["colour"] = discord.Colour(colour)
                    role = await guild.create_role(**kwargs)
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
                            f"autogen({kind}): role '{full_name}' attempt {attempt} "
                            f"failed ({exc}) — retrying in {_RETRY_BACKOFF}s"
                        )
                        await asyncio.sleep(_RETRY_BACKOFF)
                    else:
                        log.warning(
                            f"autogen({kind}): failed to create '{full_name}' "
                            f"after {_MAX_RETRIES} attempts: {exc}"
                        )

            if role:
                created.append(role)
            await asyncio.sleep(_CREATE_DELAY)

        if not created and not extra_roles:
            await interaction.followup.send(
                embed=h.warn(
                    "All roles for this autogen already exist on this server.\n"
                    "No new roles were created.",
                    "⚠️ Nothing to do",
                ),
                ephemeral=True,
            )
            return

        # ── 2. Auto-position created roles below existing panel roles ──────────
        await asyncio.sleep(_PAUSE_BEFORE_POS)

        await guild.fetch_roles()

        bot_pos = guild.me.top_role.position
        created_ids = {r.id for r in created}

        guild_panels = await db.get_role_panels_for_guild(guild.id)
        existing_panel_role_ids = {
            entry["role_id"]
            for panel in guild_panels
            for entry in panel.get("entries", [])
        } - created_ids

        existing_panel_roles = [
            r
            for r in guild.roles
            if r.id in existing_panel_role_ids and 0 < r.position < bot_pos
        ]

        if existing_panel_roles:
            insert_floor = min(r.position for r in existing_panel_roles)
        else:
            insert_floor = bot_pos

        positioned = 0
        pos_failed = False

        for i, role in enumerate(created):
            target_pos = max(1, insert_floor - 1 - i)
            try:
                await role.edit(
                    position=target_pos,
                    reason=f"NanoBot autogen ({kind}): auto-positioning",
                )
                positioned += 1
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.warning(f"autogen({kind}): could not position '{role.name}': {exc}")
                pos_failed = True
                break
            await asyncio.sleep(_POS_DELAY)

        # ── 3. Create panel ────────────────────────────────────────────────────
        panel_id = _new_id()
        await db.create_role_panel(
            panel_id=panel_id,
            guild_id=guild.id,
            title=panel_title,
            description=panel_desc,
            mode=mode,
        )

        for role in created:
            label = role.name.replace(f"{prefix} ", "", 1) if prefix else role.name
            await db.add_role_to_panel(
                panel_id,
                {
                    "role_id": role.id,
                    "label": label,
                    "emoji": None,
                    "style": "secondary",
                },
            )

        for role in extra_roles:
            await db.add_role_to_panel(
                panel_id,
                {
                    "role_id": role.id,
                    "label": role.name,
                    "emoji": None,
                    "style": "secondary",
                },
            )

        # ── 4. Post the panel ──────────────────────────────────────────────────
        panel = await db.get_role_panel(panel_id)
        view = _build_view(panel)
        try:
            msg = await channel.send(embed=_build_embed(panel), view=view)
            cog.bot.add_view(view)
            await db.update_role_panel_message(panel_id, channel.id, msg.id)
        except discord.Forbidden:
            await interaction.followup.send(
                embed=h.err(
                    f"Roles created but I can't post in {channel.mention}.\n"
                    f"Panel ID `{panel_id}` was saved — use `/roles panel post` manually."
                ),
                ephemeral=True,
            )
            return

        # ── 5. Summary ─────────────────────────────────────────────────────────
        lines = [
            f"Created **{len(created)}** role(s) and posted **{panel_title}** in {channel.mention}.",
        ]
        if created:
            preview = ", ".join(r.mention for r in created[:8])
            if len(created) > 8:
                preview += f" … and {len(created) - 8} more"
            lines.append(preview)
        if extra_roles:
            lines.append(
                f"➕ Added {len(extra_roles)} extra role(s): "
                + ", ".join(r.mention for r in extra_roles)
            )
        if not pos_failed and created:
            lines.append(
                f"📐 Auto-positioned below existing roles "
                f"(stacked under **{guild.me.top_role.name}**)."
            )
        elif pos_failed:
            lines.append(
                f"⚠️ Positioned {positioned}/{len(created)} role(s) — "
                "move any remaining ones above your member roles manually."
            )
        if skipped:
            lines.append(f"⏭️ Skipped {len(skipped)} already-existing role(s).")

        await interaction.followup.send(
            embed=h.ok("\n".join(lines), f"✅ {panel_title} Generated"),
            ephemeral=True,
        )
        log.info(
            f"autogen({kind}): created {len(created)} roles in {guild} ({guild.id}) "
            f"by {interaction.user} — positioned {positioned}/{len(created)}, "
            f"extras: {len(extra_roles)}"
        )


# ══════════════════════════════════════════════════════════════════════════════
