"""Constants for the admin cog: repo root, the managed-cog list, log levels."""

import os

# Repo root = two levels up from this file (cogs/admin/ -> cogs/ -> root). Pin
# subprocess cwd to it so git/pip/restart always operate on the bot's own
# checkout regardless of the directory the process happened to be launched from.
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


# All cogs that NanoBot manages (admin reloads itself too — safe with discord.py 2.x)
_ALL_COGS = (
    "cogs.moderation",
    "cogs.tags",
    "cogs.utility",
    "cogs.reminders",
    "cogs.recurring",
    "cogs.warnings",
    "cogs.welcome",
    "cogs.admin",
    "cogs.votes",
    "cogs.auditlog",
    "cogs.automod",
    "cogs.roles",
    "cogs.eli5",
    "cogs.images",
    "cogs.fun",
    "cogs.music",
    "cogs.leveling",
    "cogs.economy",
    "cogs.inventory",
    "cogs.casino",
    "cogs.activities",
    "cogs.crafting",
    "cogs.progression",
    "cogs.fishing",
    "cogs.gatekeeper",
    "cogs.liverole",
    "cogs.birthday",
    "cogs.tickets",
    "cogs.debug",
)

_VALID_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
