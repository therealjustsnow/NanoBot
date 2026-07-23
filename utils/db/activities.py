"""utils/db.activities — economy activities storage (/work, /mine, /hunt, …).

Part of the utils/db package. Filled in by the activities cog work; the table
setup registers with _core so db.init() creates activity tables with the rest.
"""

from ._core import _conn, register_init


async def _ensure_activities_tables():
    pass


register_init(_ensure_activities_tables)
