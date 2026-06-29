"""Gatekeeper cog package. Split from the former single-file cogs/gatekeeper.py
into constants, pure helpers (SSRF guard + perceptual hash), the verification
views, and the command surface. Every name is lifted into the package namespace
so existing flat imports (e.g. tests doing `from cogs import gatekeeper as gk;
gk._dhash`) keep working. load_extension("cogs.gatekeeper") works via setup().
"""

from . import constants as _constants
from . import helpers as _helpers
from . import views as _views
from . import cog as _cog

# _cog is last so its Gatekeeper/setup win; every helper/constant/view name is
# lifted too, keeping the flat `from cogs import gatekeeper as gk; gk._dhash` API.
for _mod in (_constants, _helpers, _views, _cog):
    for _name in dir(_mod):
        if not _name.startswith("__"):
            globals().setdefault(_name, getattr(_mod, _name))
