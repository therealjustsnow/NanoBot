"""Network layer: nekos.best / Nekosia GIF + image fetches, FML/WYR scrapers,
the Kaggle WYR seed, and Groq WYR generation. All cached via cache_db."""

import asyncio
import csv
import io
import logging
import re
from html import unescape

import aiohttp

from utils import cache_db

from .constants import (
    _NEKOS_BASE,
    _NEKOSIA_BASE,
    _WYR_URL,
    _FML_URL,
    _KAGGLE_WYR_URL,
    _GROQ_API_URL,
    _GROQ_MODEL,
    _GROQ_WYR_COUNT,
    _GROQ_MAX_TOKENS,
    _WYR_RATINGS,
    _FML_RE,
    _FML_TAG_RE,
    _MAX_CONSEC_FAILS,
)
from .helpers import parse_wyr_json

log = logging.getLogger("NanoBot.fun")


async def _fetch_nekos_single(
    session: aiohttp.ClientSession, endpoint: str
) -> dict | None:
    """Fetch one result from nekos.best. Returns full result dict or None."""
    try:
        async with session.get(
            f"{_NEKOS_BASE}/{endpoint}",
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                results = data.get("results", [])
                if results:
                    return results[0]
    except Exception as exc:
        log.debug(f"nekos.best fetch failed for '{endpoint}': {exc}")
    return None


async def _fetch_nekos_batch(
    session: aiohttp.ClientSession, endpoint: str, amount: int
) -> list[dict]:
    """Fetch up to `amount` results from nekos.best in one request (API supports amount param)."""
    # nekos.best supports ?amount=N (max 20 per request)
    results: list[dict] = []
    remaining = amount
    while remaining > 0:
        batch = min(remaining, 20)
        try:
            async with session.get(
                f"{_NEKOS_BASE}/{endpoint}",
                params={"amount": batch},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    batch_results = data.get("results", [])
                    results.extend(batch_results)
                    remaining -= len(batch_results)
                    if len(batch_results) < batch:
                        break  # API gave us less than requested, stop
                else:
                    break
        except Exception as exc:
            log.debug(f"nekos.best batch fetch failed for '{endpoint}': {exc}")
            break
        if remaining > 0:
            await asyncio.sleep(0.3)
    return results


async def _fetch_nekosia_single(
    session: aiohttp.ClientSession, category: str
) -> tuple[str | None, str | None]:
    """Fetch a random SFW image from Nekosia. Returns (image_url, source_url)."""
    try:
        async with session.get(
            f"{_NEKOSIA_BASE}/{category}",
            timeout=aiohttp.ClientTimeout(total=6),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("success"):
                    img = data.get("image", {}).get("compressed", {}).get(
                        "url"
                    ) or data.get("image", {}).get("original", {}).get("url")
                    src = data.get("source", {}).get("url")
                    return img, src
    except Exception as exc:
        log.debug(f"Nekosia fetch failed for '{category}': {exc}")
    return None, None


# ── FML story scraper (bulk, for daily cache refresh) ─────────────────────────
async def _scrape_fml_page(session: aiohttp.ClientSession) -> list[str]:
    """Scrape fmylife.com/random and return a list of story strings."""
    try:
        async with session.get(
            _FML_URL,
            headers={"User-Agent": "Mozilla/5.0 (compatible; NanoBot)"},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as resp:
            if resp.status != 200:
                return []
            html = await resp.text()
    except Exception as exc:
        log.debug(f"FML scrape failed: {exc}")
        return []

    clean = _FML_TAG_RE.sub(" ", html)
    clean = unescape(clean)

    raw_matches = _FML_RE.findall(clean)
    stories: list[str] = []
    seen: set[str] = set()
    for raw in raw_matches:
        text = re.sub(r"\s+", " ", raw).strip()
        if len(text) > 40 and text not in seen:
            seen.add(text)
            stories.append(text)
    return stories


async def _scrape_fml_bulk(session: aiohttp.ClientSession, pages: int) -> list[str]:
    """Hit fmylife.com/random multiple times and return all unique stories."""
    all_stories: list[str] = []
    seen: set[str] = set()
    consecutive_empty = 0
    for i in range(pages):
        page_stories = await _scrape_fml_page(session)
        if page_stories:
            consecutive_empty = 0
            for s in page_stories:
                if s not in seen:
                    seen.add(s)
                    all_stories.append(s)
        else:
            consecutive_empty += 1
            if consecutive_empty >= _MAX_CONSEC_FAILS:
                log.warning(
                    f"FML scrape: {consecutive_empty} empty pages in a row — "
                    f"aborting after {i + 1}/{pages} pages"
                )
                break
        if i < pages - 1:
            await asyncio.sleep(1)
    return all_stories


# ── WYR question fetcher (bulk, for daily cache refresh) ──────────────────────
async def _fetch_wyr_single(
    session: aiohttp.ClientSession, rating: str = "pg13"
) -> str | None:
    """Fetch a single Would You Rather question from truthordarebot.xyz."""
    try:
        async with session.get(
            _WYR_URL,
            params={"rating": rating},
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("question")
    except Exception as exc:
        log.debug(f"WYR fetch failed: {exc}")
    return None


async def _scrape_wyr_bulk(session: aiohttp.ClientSession, count: int) -> list[str]:
    """Fetch many WYR questions across all ratings, deduplicating as we go."""
    questions: list[str] = []
    seen: set[str] = set()
    for rating in _WYR_RATINGS:
        consecutive_fail = 0
        for i in range(count):
            q = await _fetch_wyr_single(session, rating=rating)
            if q:
                consecutive_fail = 0
                if q not in seen:
                    seen.add(q)
                    questions.append(q)
            else:
                consecutive_fail += 1
                if consecutive_fail >= _MAX_CONSEC_FAILS:
                    log.warning(
                        f"WYR scrape ({rating}): {consecutive_fail} failures in a "
                        f"row — skipping rest of this rating"
                    )
                    break
            if i < count - 1:
                await asyncio.sleep(0.5)
    return questions


# ── Kaggle WYR dataset seed (one-time bulk import) ────────────────────────────
async def _seed_kaggle_wyr(session: aiohttp.ClientSession) -> list[str]:
    """Download the Kaggle WYR CSV and return formatted questions.

    The CSV has columns: option_a, votes_a, option_b, votes_b.
    We format each row as 'Would you rather X or Y?'.
    """
    try:
        async with session.get(
            _KAGGLE_WYR_URL,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                log.warning(f"Kaggle WYR download failed: HTTP {resp.status}")
                return []
            zip_bytes = await resp.read()
    except Exception as exc:
        log.warning(f"Kaggle WYR download error: {exc}")
        return []

    # The zip contains all_unique.csv
    import zipfile

    questions: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for name in zf.namelist():
                if name.endswith(".csv"):
                    with zf.open(name) as csvfile:
                        reader = csv.DictReader(io.TextIOWrapper(csvfile, "utf-8"))
                        for row in reader:
                            a = row.get("option_a", "").strip()
                            b = row.get("option_b", "").strip()
                            if a and b:
                                # Lowercase the first letter of each option
                                a_fmt = (
                                    a[0].lower() + a[1:] if len(a) > 1 else a.lower()
                                )
                                b_fmt = (
                                    b[0].lower() + b[1:] if len(b) > 1 else b.lower()
                                )
                                questions.append(
                                    f"Would you rather {a_fmt} or {b_fmt}?"
                                )
                    break
    except Exception as exc:
        log.warning(f"Kaggle WYR parse error: {exc}")
        return []

    return questions


# ── Groq WYR generation ──────────────────────────────────────────────────────
async def _generate_wyr_groq(
    session: aiohttp.ClientSession,
    api_key: str,
    system_prompt: str,
    count: int = _GROQ_WYR_COUNT,
) -> list[str]:
    """Use Groq LLM to generate fresh WYR questions. Returns list of strings."""
    try:
        payload = {
            "model": _GROQ_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"Generate {count} unique Would You Rather questions.",
                },
            ],
            "temperature": 1.0,
            "max_tokens": _GROQ_MAX_TOKENS,
        }
        async with session.post(
            _GROQ_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                log.warning(
                    f"Groq WYR generation failed: HTTP {resp.status} {body[:200]}"
                )
                return []
            data = await resp.json()

        choice = data["choices"][0]
        text = (choice["message"].get("content") or "").strip()
        truncated = choice.get("finish_reason") == "length"

        questions = parse_wyr_json(text)
        if not questions:
            why = "reply was cut off" if truncated else "no usable questions"
            log.warning(f"Groq WYR: {why} ({len(text)} chars, none salvaged)")
            return []
        if truncated:
            # Expected now and then; the point is that it costs a couple of
            # questions rather than the whole batch.
            log.info(
                f"Groq WYR: reply was cut off, kept {len(questions)}/{count} questions"
            )
        return questions

    except Exception as exc:
        log.warning(f"Groq WYR generation error: {exc}")
        return []


# ══════════════════════════════════════════════════════════════════════════════
#  Cache-aware image getter -- used by commands
# ══════════════════════════════════════════════════════════════════════════════


async def _get_gif(session: aiohttp.ClientSession | None, endpoint: str) -> str | None:
    """Get a GIF URL for a nekos.best endpoint, cache-first with live fallback."""
    cached = await cache_db.get_random_image("nekos", endpoint)
    if cached:
        return cached["url"]

    # Cache miss -- fall back to live API
    if not session:
        return None
    result = await _fetch_nekos_single(session, endpoint)
    if result:
        # Store it for next time
        await cache_db.add_images("nekos", endpoint, [{"url": result["url"]}])
        return result["url"]
    return None


async def _get_nekosia(
    session: aiohttp.ClientSession | None, tag: str
) -> tuple[str | None, str | None]:
    """Get a Nekosia image, cache-first with live fallback."""
    cached = await cache_db.get_random_image("nekosia", tag)
    if cached:
        return cached["url"], cached.get("source_url")

    # Cache miss -- fall back to live API
    if not session:
        return None, None
    img, src = await _fetch_nekosia_single(session, tag)
    if img:
        await cache_db.add_images("nekosia", tag, [{"url": img, "source_url": src}])
    return img, src


async def _get_nekos_image(
    session: aiohttp.ClientSession | None, endpoint: str
) -> dict | None:
    """Get a nekos.best static image (for images cog), cache-first with live fallback.

    Returns dict with url, source_url, artist -- or None.
    """
    cached = await cache_db.get_random_image("nekos", endpoint)
    if cached:
        return cached

    # Cache miss -- fall back to live API
    if not session:
        return None
    result = await _fetch_nekos_single(session, endpoint)
    if result:
        img_data = {
            "url": result["url"],
            "source_url": result.get("source_url"),
            "artist": result.get("artist_name"),
        }
        await cache_db.add_images("nekos", endpoint, [img_data])
        return img_data
    return None
