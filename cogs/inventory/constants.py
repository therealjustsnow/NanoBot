"""cogs/inventory/constants.py — inventory cog constants."""

# The stacking ceilings live in utils/consumables.py now that the tackle shop
# arms items too, and are re-exported here because this is where the inventory
# has always read them from.
from utils.consumables import EFFECT_MAX_DURATION, EFFECT_MAX_USES  # noqa: F401

# Coin range a treasure chest pays when opened with a key.
CHEST_COINS_MIN = 250
CHEST_COINS_MAX = 750

# Cap on how many of one item can be used/sold in a single command call.
MAX_BULK = 1000

# /inventory sell accepts a bulk target instead of one item, so clearing out an
# inventory isn't one command per stack. Mirrors /fish sell all.
#   "all"        → every sellable stack you own
#   "cat:<name>" → every sellable stack in one catalogue category
# (a bare category name works too — no item is named after a category).
SELL_ALL_ALIASES = ("all", "everything", "*")
CATEGORY_SELL_PREFIX = "cat:"

# A bulk sell clears whole stacks, so it shows a preview and waits for a button
# press. Nothing is consumed until then, so an unanswered confirmation just
# expires — long enough to read the list, short enough not to linger.
SELL_CONFIRM_TIMEOUT = 60.0

# A multi-item use is one press from the gear picker, so the reply stops being
# "here's your buff" and becomes a list. Long enough to read, short enough that
# nobody scrolls a phone through it.
USE_PICKER_TIMEOUT = 120.0
