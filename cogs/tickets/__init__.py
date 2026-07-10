"""Tickets cog package: constants, pure helpers (thread naming + transcript
lines), the panel/modal/thread views, and the command surface. Every name is
lifted into the package namespace so flat imports (e.g. tests doing
`from cogs.tickets import thread_name`) work. load_extension("cogs.tickets")
works via setup().
"""

from . import constants as _constants
from . import helpers as _helpers
from . import views as _views
from . import cog as _cog

# _cog is last so its Tickets/setup win; every helper/constant/view name is
# lifted too, keeping the flat `from cogs.tickets import thread_name` API.
for _mod in (_constants, _helpers, _views, _cog):
    for _name in dir(_mod):
        if not _name.startswith("__"):
            globals().setdefault(_name, getattr(_mod, _name))
