"""Casino cog package. Constants (bet bounds, paytables), pure helpers
(deterministic roll/card-list → outcome logic), the Hit/Stand blackjack view,
and the command surface. Every name is lifted into the package namespace so
flat imports (e.g. tests doing `from cogs.casino import resolve_flip`) work.
load_extension("cogs.casino") works via setup().
"""

from . import constants as _constants
from . import helpers as _helpers
from . import views as _views
from . import cog as _cog

# _cog is last so its Casino/setup win; every helper/constant/view name is
# lifted too, keeping the flat `from cogs.casino import resolve_flip` API.
for _mod in (_constants, _helpers, _views, _cog):
    for _name in dir(_mod):
        if not _name.startswith("__"):
            globals().setdefault(_name, getattr(_mod, _name))
