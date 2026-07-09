"""Pure birthday helpers (imported directly by tests): date parsing/formatting,
countdown math, the voice-region→timezone guesser, and the FFmpeg song builder.
"""

import re
from datetime import date

from .constants import (
    _HB_NOTES,
    _MAX_DAY,
    _MONTH_NAMES,
    _MONTHS,
    _REGION_TZ,
)


def guess_timezone_from_regions(regions: list[str]) -> str | None:
    """Pick the most common mappable IANA tz from a list of voice-region codes."""
    tallies: dict[str, int] = {}
    for code in regions:
        tz = _REGION_TZ.get((code or "").lower())
        if tz:
            tallies[tz] = tallies.get(tz, 0) + 1
    if not tallies:
        return None
    return max(tallies, key=tallies.get)


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def parse_birthday(s: str) -> tuple[int, int, int | None] | None:
    """Parse a birthday string into ``(month, day, year_or_None)`` or ``None``.

    Accepts month-name forms ("March 5", "5 Mar 1998", "March 5, 1998"),
    numeric month-first forms ("03/05", "3-5-1998") and ISO ("1998-03-05").
    Ordinal suffixes (5th) are tolerated. Years must be four digits.
    """
    if not s:
        return None
    s = s.strip()
    month = day = year = None

    low = s.lower().replace(",", " ")
    # Strip ordinal suffixes: "5th" → "5".
    tokens = [re.sub(r"^(\d+)(st|nd|rd|th)$", r"\1", t) for t in low.split()]

    name_idx = next((i for i, t in enumerate(tokens) if t in _MONTHS), None)
    if name_idx is not None:
        month = _MONTHS[tokens[name_idx]]
        nums = [int(t) for t in tokens if t.isdigit()]
        days = [n for n in nums if 1 <= n <= 31]
        years = [n for n in nums if 1000 <= n <= 9999]
        if not days:
            return None
        day = days[0]
        year = years[0] if years else None
    else:
        iso = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
        if iso:
            year, month, day = int(iso[1]), int(iso[2]), int(iso[3])
        else:
            m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})(?:[-/.](\d{4}))?", s)
            if not m:
                return None
            month, day = int(m[1]), int(m[2])
            year = int(m[3]) if m[3] else None

    if not (1 <= month <= 12):
        return None
    if not (1 <= day <= _MAX_DAY[month - 1]):
        return None
    if year is not None and not (1900 <= year <= 2100):
        return None
    return month, day, year


def fmt_birthday(month: int, day: int, year: int | None = None) -> str:
    """'March 5' or 'March 5, 1998'."""
    out = f"{_MONTH_NAMES[month - 1]} {day}"
    if year:
        out += f", {year}"
    return out


def next_birthday_date(month: int, day: int, today: date) -> date:
    """The next calendar date this birthday lands on (today counts as itself).

    A Feb 29 birthday falls back to Feb 28 in non-leap years.
    """

    def _make(year: int) -> date:
        try:
            return date(year, month, day)
        except ValueError:
            return date(year, month, 28)  # Feb 29 → Feb 28

    d = _make(today.year)
    if d < today:
        d = _make(today.year + 1)
    return d


def days_until_birthday(month: int, day: int, today: date) -> int:
    return (next_birthday_date(month, day, today) - today).days


def is_birthday_today(month: int, day: int, today: date) -> bool:
    if month == today.month and day == today.day:
        return True
    # Feb 29 birthday celebrated on Feb 28 when the year isn't a leap year.
    if month == 2 and day == 29 and today.month == 2 and today.day == 28:
        return not _is_leap(today.year)
    return False


def age_on(month: int, day: int, year: int | None, on_date: date) -> int | None:
    """Age the person reaches on/by ``on_date`` (None when birth year unknown).

    A Feb 29 birthday counts as reached on Feb 28 in non-leap years, matching
    when it's celebrated (next_birthday_date/is_birthday_today) — otherwise the
    announcement would report the age one year low.
    """
    if year is None:
        return None
    if (month, day) == (2, 29) and not _is_leap(on_date.year):
        day = 28
    age = on_date.year - year
    if (on_date.month, on_date.day) < (month, day):
        age -= 1
    return age


# ── "Happy Birthday" melody (synthesized, no asset/network needed) ─────────────
# C-major, public-domain melody rendered as plain sine tones by FFmpeg the first
# time it's needed, then cached on disk. (freq_hz, duration_seconds).


def _ffmpeg_song_cmd(path: str) -> list[str]:
    """Build the FFmpeg command that renders _HB_NOTES to a stereo WAV at *path*."""
    inputs: list[str] = []
    chain: list[str] = []
    labels: list[str] = []
    for i, (freq, dur) in enumerate(_HB_NOTES):
        inputs += [
            "-f", "lavfi", "-t", f"{dur:.3f}",
            "-i", f"sine=frequency={freq}:sample_rate=48000",
        ]  # fmt: skip
        fade_out = max(dur - 0.07, 0.0)
        chain.append(
            f"[{i}]afade=t=in:st=0:d=0.02,"
            f"afade=t=out:st={fade_out:.3f}:d=0.07,volume=0.25[a{i}]"
        )
        labels.append(f"[a{i}]")
    filtergraph = (
        ";".join(chain)
        + ";"
        + "".join(labels)
        + f"concat=n={len(_HB_NOTES)}:v=0:a=1[out]"
    )
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        *inputs,
        "-filter_complex", filtergraph,
        "-map", "[out]", "-ac", "2", "-ar", "48000", "-y", path,
    ]  # fmt: skip
