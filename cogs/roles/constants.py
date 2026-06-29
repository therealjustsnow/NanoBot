"""Role-panel autogen palettes + the kind→(title, desc, mode, palette) config."""

# ── Autogen palettes ───────────────────────────────────────────────────────────
COLOUR_PALETTE: list[tuple[str, int | None]] = [
    ("🔴 Red", 0xE74C3C),
    ("🟠 Orange", 0xE67E22),
    ("🟡 Yellow", 0xF4D03F),
    ("🟢 Green", 0x2ECC71),
    ("🌿 Mint", 0x1ABC9C),
    ("🔵 Blue", 0x3498DB),
    ("🌊 Cyan", 0x00BCD4),
    ("💙 Navy", 0x1F618D),
    ("🟣 Purple", 0x9B59B6),
    ("🔮 Violet", 0x6C3483),
    ("🩷 Pink", 0xFF6EB4),
    ("🌸 Rose", 0xE91E8C),
    ("🤎 Brown", 0xA0522D),
    ("🧡 Amber", 0xF39C12),
    ("🌻 Gold", 0xD4AC0D),
    ("🩶 Silver", 0x95A5A6),
    ("⬜ White", 0xECF0F1),
    ("🖤 Charcoal", 0x546E7A),
]

PRONOUN_PALETTE: list[tuple[str, int | None]] = [
    ("She/Her", None),
    ("He/Him", None),
    ("They/Them", None),
    ("It/Its", None),
    ("Any/All", None),
]

AGE_PALETTE: list[tuple[str, int | None]] = [
    ("13-17", None),
    ("18-20", None),
    ("21-25", None),
    ("26-30", None),
    ("31+", None),
]

REGION_PALETTE: list[tuple[str, int | None]] = [
    ("🌎 North America", None),
    ("🌎 South America", None),
    ("🌍 Europe", None),
    ("🌍 Africa", None),
    ("🌍 Middle East", None),
    ("🌏 Asia", None),
    ("🌏 Oceania", None),
]

_AUTOGEN_CFG: dict[str, tuple[str, str, str, list]] = {
    "colors": (
        "🎨 Colours",
        "Pick a colour — choosing a new one removes the previous.",
        "single",
        COLOUR_PALETTE,
    ),
    "pronouns": (
        "🏳️‍🌈 Pronouns",
        "Select your pronouns — you can pick more than one.",
        "toggle",
        PRONOUN_PALETTE,
    ),
    "age": (
        "🎂 Age Range",
        "Pick your age range — you can only select one.",
        "single",
        AGE_PALETTE,
    ),
    "region": (
        "🌍 Region",
        "Select your region(s) — you can pick more than one.",
        "toggle",
        REGION_PALETTE,
    ),
}
