"""
utils/checks.py
Reusable permission check decorators for NanoBot commands.

Each decorator checks BOTH the invoking user's permissions AND the bot's own
permissions, so you only need one decorator per command instead of two.
Errors surface as commands.MissingPermissions / BotMissingPermissions and
are caught by the global error handler in main.py.

Usage:
    from utils.checks import has_ban_perms, has_mod_perms

    @has_ban_perms()
    async def ban(self, ctx, ...):
        ...
"""

from discord.ext import commands


def _check(user_perm: str, bot_perm: str | None = None):
    """
    Build a combined user+bot permission check decorator.
    bot_perm defaults to user_perm if not specified.
    """
    bot_perm = bot_perm or user_perm

    def decorator(func):
        func = commands.has_permissions(**{user_perm: True})(func)
        func = commands.bot_has_permissions(**{bot_perm: True})(func)
        return func

    return decorator


# ── Moderation ─────────────────────────────────────────────────────────────────

def has_ban_perms():
    """Requires ban_members for both the user and the bot."""
    return _check("ban_members")


def has_kick_perms():
    """Requires kick_members for both the user and the bot."""
    return _check("kick_members")


def has_mod_perms():
    """Requires manage_messages for the user; the bot needs it too for purge etc."""
    return _check("manage_messages")


def has_channel_perms():
    """Requires manage_channels for both (slowmode, lock)."""
    return _check("manage_channels")


def has_timeout_perms():
    """Requires moderate_members for both (freeze / unfreeze)."""
    return _check("moderate_members")


def has_role_perms():
    """Requires manage_roles for both (addrole / removerole)."""
    return _check("manage_roles")


def has_admin_perms():
    """Requires administrator for the user (bot check omitted — bot is usually admin)."""
    def decorator(func):
        return commands.has_permissions(administrator=True)(func)
    return decorator
