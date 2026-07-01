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

# How long a /teamup co-op reward waits for every teammate to confirm.
COOP_CONFIRM_TIMEOUT = 120

# How long an open /raid board stays joinable before it auto-expires unpaid.
RAID_TIMEOUT = 1800  # 30 min

# Starter shop catalogue. A fresh guild's shop is empty, which reads as broken,
# so /shop seed drops in this curated set of generic community rewards. They're
# all `custom` kind (a mod fulfils them) because role rewards need a guild's own
# role id, which we can't know ahead of time. Prices are scaled to the default
# 100-coin daily reward (a few days' to a couple weeks' saving). Mods are meant
# to edit prices, remove ones that don't fit, and add their own role rewards.
_DEFAULT_SHOP_ITEMS = [
    {
        "name": "Custom Color Role",
        "price": 1500,
        "description": "Pick your own name color.",
        "reward": "Tell a mod the hex color you want and they'll set up your "
        "personal colored role.",
        "limit": 1,
    },
    {
        "name": "Custom Voice Channel",
        "price": 800,
        "description": "Get a temporary personal voice channel.",
        "reward": "Tell a mod the name you want and they'll spin up a personal "
        "voice channel for you.",
    },
    {
        "name": "Server Shoutout",
        "price": 500,
        "description": "Get a shoutout in the announcements channel.",
        "reward": "A mod will post a shoutout for you in the announcements channel.",
    },
    {
        "name": "Pin a Message",
        "price": 400,
        "description": "Pin one message of your choice for a week.",
        "reward": "Link the message you want pinned and a mod will pin it for a week.",
    },
    {
        "name": "VIP for a Day",
        "price": 750,
        "description": "24 hours of VIP perks.",
        "reward": "A mod will grant you VIP perks for the next 24 hours.",
    },
    {
        "name": "Pick the Next Event",
        "price": 1000,
        "description": "Choose the theme of the next server event.",
        "reward": "Share your event idea — the next server event will run with "
        "your theme.",
    },
    {
        "name": "Movie Night Pick",
        "price": 600,
        "description": "Choose the next watch-party title.",
        "reward": "Tell a mod your pick and it becomes the next movie/watch-party.",
    },
    {
        "name": "Add a Server Emoji",
        "price": 2000,
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
