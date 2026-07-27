"""Economy cog constants: cooldowns, gamble odds, caps, and the starter shop catalogue."""

DAILY_COOLDOWN = 86_400  # 24h between claims
STREAK_WINDOW = 172_800  # claim within 48h of the last to keep the streak

# A daily reject arriving within this many seconds of the last successful claim
# isn't a genuine "come back tomorrow" — it's the same claim dispatched twice
# (e.g. a gateway redelivery, or two bot processes briefly online during a
# restart). Flagged as a duplicate so the command can swallow the contradictory
# second reply instead of telling the user they already claimed.
DAILY_DUP_WINDOW = 10

# Gamble odds: win chance under 0.5 gives the "house" a slight edge so coins
# aren't trivially farmed. A win pays the bet back plus (multiplier - 1)x.
GAMBLE_WIN_CHANCE = 0.45
GAMBLE_MULTIPLIER = 2.0

# Sanity ceiling for any single admin coin amount (grant / daily reward / streak
# bonus). Stops a fat-fingered "give 1e18" from wrecking the economy or pushing
# values toward integer limits. A billion is far above any real use.
COIN_MAX = 1_000_000_000

# ══════════════════════════════════════════════════════════════════════════════
#  Coin faucets — bot-wide, owner-only
# ══════════════════════════════════════════════════════════════════════════════
# Every amount here MINTS into a global wallet, so none of them can be a
# server's setting: one /daily claim per account, one wallet in every server, so
# a guild raising its daily would be paying its members coins that spend
# everywhere else too. They live in `bot_settings` (utils/db/settings.py) and
# are set with the owner-only `!econ`; these are the defaults used until the
# owner overrides one. Migration 5 dropped the old per-guild columns.
#
# What a guild still owns is everything that affects only its own members: its
# currency name/emoji, its raid party size, and its **shop prices** — a shop
# purchase is a pure *sink*, destroying coins in exchange for that guild's own
# role or mod-fulfilled perk, so pricing it badly can't hurt anyone outside it.
REWARD_DEFAULTS: dict[str, int] = {
    "daily": 100,  # /daily base payout
    "streak_bonus": 0,  # extra coins per consecutive day, off by default
    "coop": 50,  # /squad, per confirmed member (0 disables /squad)
    "raid": 100,  # /raid, per participant     (0 disables /raid)
    "level_coin": 0,  # level-up payout x new level, off by default
}

# One-line explanations for `!econ`'s listing, in display order.
REWARD_LABELS: dict[str, str] = {
    "daily": "/daily base payout",
    "streak_bonus": "extra coins per consecutive /daily day",
    "coop": "/squad reward, per confirmed member (0 disables it)",
    "raid": "/raid reward, per participant (0 disables it)",
    "level_coin": "level-up payout, multiplied by the new level (0 = off)",
}

# How long a /squad co-op reward (and its picker menu, and the /coin grant|take
# mass picker) waits before expiring. Generous — a big squad can be 25 people
# who all have to press Confirm — but not unlimited, so an abandoned board
# doesn't stay clickable (and its persisted row doesn't linger) forever.
COOP_CONFIRM_TIMEOUT = 1800  # 30 min, matches RAID_TIMEOUT

# How long an open /raid board stays joinable before it auto-expires unpaid.
RAID_TIMEOUT = 1800  # 30 min

# Starter shop catalogue. A fresh guild's shop is empty, which reads as broken,
# so /shop seed drops in this curated set of generic community rewards. They're
# all `custom` kind (a mod fulfils them) because role rewards need a guild's own
# role id, which we can't know ahead of time. Prices are scaled to the default
# 100-coin daily reward (a few days' to a couple weeks' saving). Mods are meant
# to edit prices, remove ones that don't fit, and add their own role rewards.
# Starter shop, dropped in by /shop seed. Prices follow the same staged curve
# as the rod ladder: the cheap social rewards are x2 (about twice as fast to
# reach as in the 60s economy, so a new member buys something in their first
# session) and the two aspirational ones x2.5. Only *new* seeds use these — an
# existing shop keeps whatever prices its mods set.
_DEFAULT_SHOP_ITEMS = [
    {
        "name": "Custom Color Role",
        "price": 4000,
        "description": "Pick your own name color.",
        "reward": "Tell a mod the hex color you want and they'll set up your "
        "personal colored role.",
        "limit": 1,
    },
    {
        "name": "Custom Voice Channel",
        "price": 1600,
        "description": "Get a temporary personal voice channel.",
        "reward": "Tell a mod the name you want and they'll spin up a personal "
        "voice channel for you.",
    },
    {
        "name": "Server Shoutout",
        "price": 1000,
        "description": "Get a shoutout in the announcements channel.",
        "reward": "A mod will post a shoutout for you in the announcements channel.",
    },
    {
        "name": "Pin a Message",
        "price": 800,
        "description": "Pin one message of your choice for a week.",
        "reward": "Link the message you want pinned and a mod will pin it for a week.",
    },
    {
        "name": "VIP for a Day",
        "price": 1500,
        "description": "24 hours of VIP perks.",
        "reward": "A mod will grant you VIP perks for the next 24 hours.",
    },
    {
        "name": "Pick the Next Event",
        "price": 2000,
        "description": "Choose the theme of the next server event.",
        "reward": "Share your event idea — the next server event will run with "
        "your theme.",
    },
    {
        "name": "Movie Night Pick",
        "price": 1200,
        "description": "Choose the next watch-party title.",
        "reward": "Tell a mod your pick and it becomes the next movie/watch-party.",
    },
    {
        "name": "Add a Server Emoji",
        "price": 5000,
        "description": "Submit an emoji to be added to the server.",
        "reward": "Send a mod the image and name for an emoji to add to the server.",
        "limit": 1,
    },
]


# Contribution rank titles, awarded by leaderboard position. The first match
# (lowest threshold the rank meets) wins; everyone ranked gets at least Member.
_RANK_TITLES = [
    (1, "🏆 Guild Legend"),
    (3, "💎 Veteran"),
    (10, "⭐ Trusted"),
    (25, "🤝 Contributor"),
]
