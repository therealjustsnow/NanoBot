"""AutoMod cog package. Split from the former single-file cogs/automod.py into
constants, pure rule-check helpers, action side-effects, autocompletes, and the
command surface. load_extension("cogs.automod") works via setup() below.
"""

from .cog import AutoMod, setup

__all__ = ["AutoMod", "setup"]
