"""Pure helper functions for the music player (no Discord/yt-dlp deps)."""

import re
from typing import Optional

from .constants import _YTID_RE, _LYRICS_NOISE


def _extract_ytid(url: str) -> Optional[str]:
    """Return the 11-char YouTube video ID from a URL, or None."""
    m = _YTID_RE.search(url)
    return m.group(1) if m else None


def _apply_delta(arg: str, current: float) -> Optional[float]:
    """Parse a numeric command arg as absolute ("80") or relative ("+10"/"-10").

    A leading +/- adds to the current value; anything else is absolute.
    Returns None when the arg isn't a number.
    """
    arg = arg.strip().lstrip("=")
    if not arg:
        return None
    try:
        value = float(arg)
    except ValueError:
        return None
    return current + value if arg[0] in "+-" else value


def _fmt_time(seconds: float | int | None) -> str:
    """Format seconds as M:SS or H:MM:SS. Returns '🔴 LIVE' for None/0."""
    if not seconds:
        return "🔴 LIVE"
    seconds = int(seconds)
    hh, rem = divmod(seconds, 3600)
    mm, ss = divmod(rem, 60)
    if hh:
        return f"{hh}:{mm:02d}:{ss:02d}"
    return f"{mm}:{ss:02d}"


def _ellipsize(text: str, limit: int) -> str:
    """Trim `text` to `limit` chars, appending an ellipsis when shortened."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _progress_bar(elapsed: float, total: float | None, length: int = 18) -> str:
    """Render a slider-style progress bar."""
    if not total:
        return "🔘" + "▬" * (length - 1)
    frac = max(0.0, min(1.0, elapsed / total))
    filled = int(frac * (length - 1))
    return "▬" * filled + "🔘" + "▬" * (length - 1 - filled)


def _parse_timestamp(text: str) -> Optional[int]:
    """Parse 'H:MM:SS', 'M:SS', or plain seconds into an int. None if invalid."""
    text = text.strip()
    try:
        if ":" in text:
            parts = [int(p) for p in text.split(":")]
            if len(parts) == 2:
                return parts[0] * 60 + parts[1]
            if len(parts) == 3:
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
            return None
        return int(text)
    except ValueError:
        return None


def _spotify_cover(obj: dict) -> Optional[str]:
    """Pull the highest-resolution cover-art URL from a Spotify embed object."""
    if not isinstance(obj, dict):
        return None
    art = obj.get("coverArt") or obj.get("visualIdentity") or {}
    sources = art.get("sources") if isinstance(art, dict) else None
    if sources:
        return sources[-1].get("url")
    return None


def _clean_artist(
    title: str, uploader: Optional[str], artist: Optional[str] = None
) -> Optional[str]:
    """Best-effort artist label, de-duplicated against the title.

    Prefers an explicit artist tag (YouTube Music supplies ``artist``/``creator``),
    else falls back to the channel/uploader with common noise ("- Topic", "VEVO",
    "Official …") stripped. Returns ``None`` when the result is empty or every
    meaningful word already appears in the title — so the Now Playing card never
    shows the same words twice (e.g. uploader "Eminem" alongside title
    "Eminem - Godzilla").
    """
    cand = (artist or uploader or "").strip()
    if not cand:
        return None
    # Strip channel-name noise that isn't really an artist.
    cand = re.sub(r"\s*-\s*topic\s*$", "", cand, flags=re.IGNORECASE)
    cand = re.sub(r"\s*vevo\s*$", "", cand, flags=re.IGNORECASE)
    cand = re.sub(r"\s*-\s*official.*$", "", cand, flags=re.IGNORECASE)
    cand = re.sub(r"\bofficial\b", "", cand, flags=re.IGNORECASE)
    cand = re.sub(r"\s+", " ", cand).strip(" -·|")
    if not cand:
        return None
    title_words = {w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) > 1}
    cand_words = {w for w in re.findall(r"[a-z0-9]+", cand.lower()) if len(w) > 1}
    if cand_words and cand_words <= title_words:
        return None
    return cand


def _metadata_query(title: str) -> str:
    """Strip YouTube-title noise (brackets, 'feat.', 'Official Video', …) to a
    clean search term for the music-metadata API."""
    q = _LYRICS_NOISE.sub("", title)
    return re.sub(r"\s+", " ", q).strip(" -·|")


def _pick_itunes_match(title: str, results: list) -> Optional[tuple[str, str]]:
    """Pick a confident (artist, track) from iTunes Search results for `title`.

    Only accepts a result whose track name substantially overlaps the original
    title (≥60% of its words appear in it) so an unrelated "closest" song is
    never mislabelled onto the playing track. Returns None when nothing matches.
    """
    want = {w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) > 1}
    if not want:
        return None
    for item in results:
        if not isinstance(item, dict):
            continue
        artist = (item.get("artistName") or "").strip()
        track = (item.get("trackName") or "").strip()
        if not artist or not track:
            continue
        track_words = {w for w in re.findall(r"[a-z0-9]+", track.lower()) if len(w) > 1}
        if not track_words:
            continue
        if len(track_words & want) / len(track_words) >= 0.6:
            return artist, track
    return None


def _split_artist_title(query: str) -> tuple[str, str]:
    """Best-effort split of a query/title into (artist, title) for lyrics lookup."""
    cleaned = _LYRICS_NOISE.sub("", query)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -·|")
    if " - " in cleaned:
        artist, title = cleaned.split(" - ", 1)
        return artist.strip(), title.strip()
    return "", cleaned.strip()
