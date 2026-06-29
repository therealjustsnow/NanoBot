"""Role-panel cog package. Split from the former single-file cogs/roles.py into
constants (autogen palettes), helpers (locks/id/cid), views (RoleButton +
factories), the autogen engine, and the command surface. load_extension(
"cogs.roles") works via setup() below.
"""

from .cog import Roles, setup

__all__ = ["Roles", "setup"]
