"""cogs/inventory — generic item inventory (package).

Re-exports the flat public API so `from cogs.inventory import chest_payout,
Inventory` works, and provides the `setup()` entry point for
`load_extension("cogs.inventory")`.
"""

from .constants import CHEST_COINS_MAX, CHEST_COINS_MIN  # noqa: F401
from .helpers import chest_payout  # noqa: F401
from .views import SellConfirmView  # noqa: F401
from .cog import Inventory, setup  # noqa: F401
