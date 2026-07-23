"""utils/db.casino — casino game storage (stats, config, jackpot).

Part of the utils/db package. Filled in by the casino cog work; the table
setup registers with _core so db.init() creates casino tables with the rest.
"""

from ._core import _conn, register_init


async def _ensure_casino_tables():
    pass


register_init(_ensure_casino_tables)
