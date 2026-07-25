"""Identity cog package — the /profile card, cosmetics, and global levels.

`load_extension("cogs.identity")` works via setup(). Every name is lifted into
the package namespace so flat imports (`from cogs.identity import equip_result`)
work the way the other packages do.
"""

from . import helpers as _helpers
from . import cog as _cog

for _mod in (_helpers, _cog):
    for _name in dir(_mod):
        if not _name.startswith("__"):
            globals().setdefault(_name, getattr(_mod, _name))

setup = _cog.setup
