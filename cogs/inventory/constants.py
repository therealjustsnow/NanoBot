"""cogs/inventory/constants.py — inventory cog constants."""

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

# Bulk-using effect items multiplies the granted duration/charges by qty, so
# without a separate ceiling one command could bank a near-permanent buff
# (e.g. 1000 lucky charms = 500 hours of luck, or a years-long rob shield).
# A single use of an item whose base duration/uses already exceeds the cap
# still works — the cap only limits *stacking*.
EFFECT_MAX_DURATION = 86400  # granted seconds per use command (24h)
EFFECT_MAX_USES = 50  # granted charges per use command
