"""URL/colour/scraper constants and compiled patterns for the fun cog."""

import re

_NEKOS_BASE = "https://nekos.best/api/v2"
_NEKOSIA_BASE = "https://api.nekosia.cat/api/v1/images"
_WYR_URL = "https://api.truthordarebot.xyz/api/wyr"
_THIGH_TAGS = (
    "thighs",
    "thigh-high-socks",
    "white-thigh-high-socks",
    "black-thigh-high-socks",
    "knee-high-socks",
)
_PINK = 0xFF6EB4
_FML_URL = "https://www.fmylife.com/random"
_FML_BLUE = 0x00B2FF

# ── Kaggle WYR dataset (one-time seed) ────────────────────────────────────────
_KAGGLE_WYR_URL = (
    "https://www.kaggle.com/api/v1/datasets/download/charlieray668/would-you-rather"
)

# ── Groq WYR generation ──────────────────────────────────────────────────────
_GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
_GROQ_MODEL = "llama-3.1-8b-instant"
_GROQ_WYR_COUNT = 20  # questions to generate per daily scrape

# ── WYR API ratings to scrape (separate question pools) ──────────────────────
_WYR_RATINGS = ("pg", "pg13")

# ── Scraper knobs ────────────────────────────────────────────────────────────
# Everything below is read from [scraper] in config.ini. The fallback dict only
# kicks in if a key is blank, missing, or the cog is somehow loaded without a
# bot.config attribute. Keys stay in sync with utils/config.DEFAULTS.
_SCRAPER_DEFAULTS = {
    "fml_pages_per_scrape": 500,
    "wyr_requests_per_scrape": 500,
    "nekos_per_endpoint": 400,
    "nekosia_per_tag": 400,
    "revalidate_age": 7 * 86400,
    "revalidate_batch": 1000,
    "groq_wyr_system": (
        "You generate Would You Rather questions for a Discord bot. "
        "Return ONLY a JSON array of strings. Each string must start with "
        '"Would you rather" and contain exactly two options separated by " or ". '
        "End each with a question mark. Make them fun, creative, and varied -- "
        "mix silly, deep, gross, impossible, and everyday scenarios. "
        "No numbered lists, no markdown, no explanation. Just the JSON array."
    ),
}


_FML_RE = re.compile(r"Today,\s.+?\sFML\b", re.DOTALL)
_FML_TAG_RE = re.compile(r"<[^>]+>")


_MAX_CONSEC_FAILS = 10  # bail out of a scrape if the source keeps coming back empty


_WYR_SPLIT_RE = re.compile(
    r"^would you rather\s+(.+?)\s+or\s+(.+?)\??$",
    re.IGNORECASE,
)


_DURATION_RE = re.compile(
    r"(?:(\d+)\s*h(?:ours?|r)?)?[\s,]*(?:(\d+)\s*m(?:in(?:utes?)?)?)?",
    re.IGNORECASE,
)
