"""Fishing cog package. Constants (fish catalogue, rods, rarity odds), pure
helpers (deterministic roll → catch logic), and the command surface. Every name
is lifted into the package namespace so flat imports (e.g. tests doing
`from cogs.fishing import pick_rarity, FISH`) work.
load_extension("cogs.fishing") works via setup().
"""

from . import constants as _constants
from . import helpers as _helpers
from . import items as _items  # registers bait/consumable ItemDefs on import
from . import cog as _cog

# _cog is last so its Fishing/setup win; every helper/constant name is lifted
# too, keeping the flat `from cogs.fishing import pick_rarity` API.
for _mod in (_constants, _helpers, _items, _cog):
    for _name in dir(_mod):
        if not _name.startswith("__"):
            globals().setdefault(_name, getattr(_mod, _name))
