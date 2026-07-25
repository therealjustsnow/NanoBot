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


async def _badword_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for badword remove — the words this guild actually filters.

    Removing a word you can't see the spelling of is guesswork, and the list
    is admin-only anyway (this command needs Manage Server), so showing it
    here leaks nothing `/automod badword list` doesn't already show.
    """
    words = await db.get_automod_badwords(interaction.guild_id)
    return _word_choices(words, current)


async def _attachment_word_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Autocomplete for attachword remove — same idea, the other list."""
    words = await db.get_automod_attachment_words(interaction.guild_id)
    return _word_choices(words, current)


def _word_choices(words, current: str) -> list[app_commands.Choice[str]]:
    q = (current or "").lower()
    return [
        app_commands.Choice(name=word[:100], value=word[:100])
        for word in sorted(words)
        if q in word.lower()
    ][:25]


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
