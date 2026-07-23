"""Activities cog package. Constants (career ladder, ore/hunt catalogues, odds
tables, pickaxe ladder), pure helpers (deterministic roll → outcome logic),
item registrations, and the command surface. Every name is lifted into the
package namespace so flat imports (e.g. tests doing
`from cogs.activities import career_info, ORES`) work.
load_extension("cogs.activities") works via setup().
"""

from . import constants as _constants
from . import helpers as _helpers
from . import items as _items  # noqa: F401 - side-effect: registers item defs
from . import cog as _cog

# _cog is last so its Activities/setup win; every helper/constant name is
# lifted too, keeping the flat `from cogs.activities import career_info` API.
for _mod in (_constants, _helpers, _items, _cog):
    for _name in dir(_mod):
        if not _name.startswith("__"):
            globals().setdefault(_name, getattr(_mod, _name))
