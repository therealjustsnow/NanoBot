"""Birthday cog package. Split from the former single-file cogs/birthday.py into
constants, pure helpers (date math + FFmpeg song builder), the timezone-picker
view, and the command surface. Every name is lifted into the package namespace so
the flat test imports (`from cogs.birthday import parse_birthday, _TZ_CHOICES,
_ffmpeg_song_cmd, …`) keep working. load_extension("cogs.birthday") works via setup().
"""

from . import constants as _constants
from . import helpers as _helpers
from . import views as _views
from . import cog as _cog

# _cog is last so its Birthday/setup win; every constant/helper/view name is
# lifted too, keeping the flat `from cogs.birthday import …` API for tests.
for _mod in (_constants, _helpers, _views, _cog):
    for _name in dir(_mod):
        if not _name.startswith("__"):
            globals().setdefault(_name, getattr(_mod, _name))
