"""Item definitions the activities cog contributes to the shared catalogue
(utils/items.py). Registered at import time — see utils/items.py's docstring
for the registration pattern. Generic items already registered elsewhere
(treasure_key, treasure_chest, lucky_charm) are reused, not redefined.

Item keys here never start with "bait_" or "fish_" (reserved for fishing).
"""

from utils import items

from .constants import HUNT_CATCHES, ORES

for _key, _ore in ORES.items():
    items.register(
        items.ItemDef(
            key=_key,
            name=_ore["name"],
            emoji=_ore["emoji"],
            category="material",
            description=f"{_ore['name']} pulled up from a mine shaft.",
            value=_ore["value"],
        )
    )

for _key, _catch in HUNT_CATCHES.items():
    _category = "treasure" if _key == "golden_antler" else "material"
    items.register(
        items.ItemDef(
            key=_key,
            name=_catch["name"],
            emoji=_catch["emoji"],
            category=_category,
            description=f"{_catch['name']} brought back from a hunt.",
            value=_catch["value"],
        )
    )

items.register(
    items.ItemDef(
        key="padlock",
        name="Padlock",
        emoji="🔒",
        category="consumable",
        description="Use it to lock up your coins for a day, blocking /rob attempts.",
        value=0,
        price=0,  # not buyable — only found while hunting
        effect={"key": "rob_shield", "magnitude": 1, "duration": 86_400},
    )
)
