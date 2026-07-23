"""Progression cog package: achievement/weekly-objective registries, the
stat-provider registry, pure helpers, and the command surface. Every name is
lifted into the package namespace so flat imports (e.g. tests doing
`from cogs.progression import ACHIEVEMENTS, period_key`) work.
load_extension("cogs.progression") works via setup().
"""

from . import constants as _constants
from . import definitions as _definitions
from . import helpers as _helpers
from . import stats as _stats
from . import cog as _cog

# _cog is last so its Progression/setup win; every helper/constant/definition
# name is lifted too, keeping the flat `from cogs.progression import X` API.
for _mod in (_constants, _definitions, _helpers, _stats, _cog):
    for _name in dir(_mod):
        if not _name.startswith("__"):
            globals().setdefault(_name, getattr(_mod, _name))
