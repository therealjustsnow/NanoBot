"""cogs/crafting — turn materials into crafted items (package).

The recipe registry lives in recipes.py, crafted item registrations in
items.py (registered into utils/items.py at import time — see that module's
docstring for the registration pattern), pure helpers in helpers.py, and the
command surface in cog.py. Every name is lifted into the package namespace so
flat imports (e.g. tests doing `from cogs.crafting import RECIPES,
find_recipe`) work. load_extension("cogs.crafting") works via setup().
"""

from . import recipes as _recipes
from . import items as _items  # noqa: F401 - registers craft_* ItemDefs
from . import helpers as _helpers
from . import views as _views
from . import cog as _cog

# _cog is last so its Crafting/setup win; every helper/recipe name is lifted
# too, keeping the flat `from cogs.crafting import RECIPES` API.
for _mod in (_recipes, _items, _helpers, _views, _cog):
    for _name in dir(_mod):
        if not _name.startswith("__"):
            globals().setdefault(_name, getattr(_mod, _name))
