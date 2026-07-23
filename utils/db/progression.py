"""utils/db.progression — achievements, weekly objectives, prestige storage.

Part of the utils/db package. Filled in by the progression cog work; the table
setup registers with _core so db.init() creates progression tables with the rest.
"""

from ._core import _conn, register_init


async def _ensure_progression_tables():
    pass


register_init(_ensure_progression_tables)
