"""AutoMod app-command autocompletes (rule, action, stored regex pattern)."""

import discord
from discord import app_commands

from utils import db

from .constants import ACTION_LABELS, RULE_LABELS


async def _rule_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=label, value=key)
        for key, label in RULE_LABELS.items()
        if current.lower() in key or current.lower() in label.lower()
    ]


async def _action_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=label, value=key)
        for key, label in ACTION_LABELS.items()
        if current.lower() in key or current.lower() in label.lower()
    ]


async def _regex_pattern_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for regex remove — display label (or pattern) as name, pattern as value."""
    patterns = await db.get_automod_regex_patterns(interaction.guild_id)
    choices = []
    for p in patterns:
        display = p["label"] or p["pattern"]
        if (
            current.lower() in display.lower()
            or current.lower() in p["pattern"].lower()
        ):
            choices.append(
                app_commands.Choice(name=display[:100], value=p["pattern"][:100])
            )
    return choices[:25]
