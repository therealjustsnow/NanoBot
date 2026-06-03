"""Tests for pure helper functions in cogs/music.py.

We extract only the pure functions via ast/textwrap so no Discord,
yt-dlp, or FFmpeg stubs are needed.
"""

import ast
import re
import textwrap
from pathlib import Path
from typing import Optional

import pytest

# ---------------------------------------------------------------------------
# Extract pure free functions from music.py without importing the whole module
# ---------------------------------------------------------------------------

_MUSIC_SOURCE = (Path(__file__).parent.parent / "cogs" / "music.py").read_text()
_TREE = ast.parse(_MUSIC_SOURCE)
_LINES = _MUSIC_SOURCE.splitlines(keepends=True)

_PURE_FUNCTIONS = {
    "_apply_delta",
    "_extract_ytid",
    "_fmt_time",
    "_progress_bar",
    "_parse_timestamp",
    "_spotify_cover",
    "_split_artist_title",
    "_clean_artist",
}

# Grab module-level assignments that pure functions depend on
_CONST_NAMES = {"_LYRICS_NOISE", "_YTID_RE"}
_const_assigns: dict[str, ast.Assign] = {}
for node in ast.walk(_TREE):
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id in _CONST_NAMES:
                _const_assigns[t.id] = node

_ns: dict = {"re": re, "Optional": Optional}

# Exec constants first (order matters: _YTID_RE before _extract_ytid)
for _cname in ("_YTID_RE", "_LYRICS_NOISE"):
    node = _const_assigns.get(_cname)
    if node:
        start = node.lineno - 1
        end = node.end_lineno
        src = textwrap.dedent("".join(_LINES[start:end]))
        exec(compile(src, f"<{_cname}>", "exec"), _ns)  # noqa: S102

# Exec each pure function
for node in ast.walk(_TREE):
    if isinstance(node, ast.FunctionDef) and node.name in _PURE_FUNCTIONS:
        start = node.lineno - 1
        end = node.end_lineno
        src = textwrap.dedent("".join(_LINES[start:end]))
        exec(compile(src, f"<{node.name}>", "exec"), _ns)  # noqa: S102

_apply_delta = _ns["_apply_delta"]
_extract_ytid = _ns["_extract_ytid"]
_fmt_time = _ns["_fmt_time"]
_progress_bar = _ns["_progress_bar"]
_parse_timestamp = _ns["_parse_timestamp"]
_spotify_cover = _ns["_spotify_cover"]
_split_artist_title = _ns["_split_artist_title"]
_clean_artist = _ns["_clean_artist"]


# ---------------------------------------------------------------------------
# _apply_delta
# ---------------------------------------------------------------------------


class TestApplyDelta:
    def test_absolute_int(self):
        assert _apply_delta("80", 50.0) == 80.0

    def test_absolute_float(self):
        assert _apply_delta("1.5", 0.0) == 1.5

    def test_relative_plus(self):
        assert _apply_delta("+10", 50.0) == 60.0

    def test_relative_minus(self):
        assert _apply_delta("-5", 50.0) == 45.0

    def test_leading_equals_stripped(self):
        assert _apply_delta("=100", 50.0) == 100.0

    def test_whitespace_stripped(self):
        assert _apply_delta("  75  ", 0.0) == 75.0

    def test_empty_string(self):
        assert _apply_delta("", 50.0) is None

    def test_non_numeric(self):
        assert _apply_delta("abc", 50.0) is None

    def test_relative_minus_goes_negative(self):
        assert _apply_delta("-100", 50.0) == -50.0

    def test_zero_absolute(self):
        assert _apply_delta("0", 999.0) == 0.0


# ---------------------------------------------------------------------------
# _fmt_time
# ---------------------------------------------------------------------------


class TestFmtTime:
    def test_none_returns_live(self):
        assert _fmt_time(None) == "🔴 LIVE"

    def test_zero_returns_live(self):
        assert _fmt_time(0) == "🔴 LIVE"

    def test_seconds_only(self):
        assert _fmt_time(65) == "1:05"

    def test_exactly_one_minute(self):
        assert _fmt_time(60) == "1:00"

    def test_under_one_minute(self):
        assert _fmt_time(9) == "0:09"

    def test_hours(self):
        assert _fmt_time(3661) == "1:01:01"

    def test_truncates_float(self):
        assert _fmt_time(90.9) == "1:30"

    def test_large_duration(self):
        assert _fmt_time(7384) == "2:03:04"


# ---------------------------------------------------------------------------
# _progress_bar
# ---------------------------------------------------------------------------


class TestProgressBar:
    def test_no_total_live_bar_starts_with_slider(self):
        bar = _progress_bar(30, None, length=5)
        assert bar.startswith("🔘")

    def test_no_total_correct_length(self):
        bar = _progress_bar(30, None, length=5)
        assert bar.count("▬") + bar.count("🔘") == 5

    def test_start_of_track_slider_first(self):
        bar = _progress_bar(0, 100, length=5)
        assert bar.startswith("🔘")

    def test_end_of_track_slider_last(self):
        bar = _progress_bar(100, 100, length=5)
        assert bar.endswith("🔘")

    def test_midpoint(self):
        # 11-char bar, slider at position 5 (0-indexed) for 50%
        bar = _progress_bar(50, 100, length=11)
        assert bar[5] == "🔘"

    def test_correct_total_length(self):
        bar = _progress_bar(0, 100, length=6)
        assert bar.count("▬") + bar.count("🔘") == 6

    def test_elapsed_beyond_total_clamped(self):
        bar = _progress_bar(200, 100, length=5)
        assert bar.endswith("🔘")

    def test_negative_elapsed_clamped(self):
        bar = _progress_bar(-10, 100, length=5)
        assert bar.startswith("🔘")


# ---------------------------------------------------------------------------
# _parse_timestamp
# ---------------------------------------------------------------------------


class TestParseTimestamp:
    def test_plain_seconds(self):
        assert _parse_timestamp("90") == 90

    def test_mm_ss(self):
        assert _parse_timestamp("1:30") == 90

    def test_hh_mm_ss(self):
        assert _parse_timestamp("1:01:01") == 3661

    def test_zero_zero(self):
        assert _parse_timestamp("0:00") == 0

    def test_whitespace(self):
        assert _parse_timestamp("  2:00  ") == 120

    def test_invalid_string(self):
        assert _parse_timestamp("abc") is None

    def test_too_many_colons(self):
        assert _parse_timestamp("1:2:3:4") is None

    def test_float_string_rejected(self):
        assert _parse_timestamp("1.5") is None

    def test_negative_accepted(self):
        # function does not reject negatives; callers are responsible for clamping
        assert _parse_timestamp("-5") == -5


# ---------------------------------------------------------------------------
# _spotify_cover
# ---------------------------------------------------------------------------


class TestSpotifyCover:
    def test_non_dict_string_returns_none(self):
        assert _spotify_cover("string") is None

    def test_none_returns_none(self):
        assert _spotify_cover(None) is None

    def test_list_returns_none(self):
        assert _spotify_cover([]) is None

    def test_cover_art_sources_last_wins(self):
        obj = {"coverArt": {"sources": [{"url": "low"}, {"url": "high"}]}}
        assert _spotify_cover(obj) == "high"

    def test_visual_identity_fallback(self):
        obj = {"visualIdentity": {"sources": [{"url": "vi_url"}]}}
        assert _spotify_cover(obj) == "vi_url"

    def test_cover_art_takes_priority_over_visual_identity(self):
        obj = {
            "coverArt": {"sources": [{"url": "cover"}]},
            "visualIdentity": {"sources": [{"url": "vi"}]},
        }
        assert _spotify_cover(obj) == "cover"

    def test_missing_sources_returns_none(self):
        assert _spotify_cover({"coverArt": {}}) is None

    def test_empty_dict_returns_none(self):
        assert _spotify_cover({}) is None

    def test_single_source(self):
        obj = {"coverArt": {"sources": [{"url": "only"}]}}
        assert _spotify_cover(obj) == "only"


# ---------------------------------------------------------------------------
# _split_artist_title
# ---------------------------------------------------------------------------


class TestSplitArtistTitle:
    def test_simple_split(self):
        assert _split_artist_title("Artist - Title") == ("Artist", "Title")

    def test_no_delimiter_artist_empty(self):
        artist, title = _split_artist_title("Just A Song")
        assert artist == ""
        assert title == "Just A Song"

    def test_strips_parens(self):
        _, title = _split_artist_title("Song (Official Music Video)")
        assert "Official" not in title

    def test_strips_brackets(self):
        _, title = _split_artist_title("Song [Lyrics]")
        assert "Lyrics" not in title

    def test_strips_feat(self):
        artist, title = _split_artist_title("Artist - Title ft. Someone")
        assert "Someone" not in title

    def test_strips_lyrics_keyword(self):
        _, title = _split_artist_title("Some Song lyrics")
        assert "lyrics" not in title.lower()

    def test_strips_official_video(self):
        _, title = _split_artist_title("Song official video")
        assert "official" not in title.lower()

    def test_multi_dash_splits_on_first(self):
        artist, title = _split_artist_title("A - B - C")
        assert artist == "A"
        assert title == "B - C"

    def test_whitespace_normalised(self):
        artist, title = _split_artist_title("  Artist  -  Title  ")
        assert artist == "Artist"
        assert title == "Title"


# ---------------------------------------------------------------------------
# _extract_ytid
# ---------------------------------------------------------------------------


class TestExtractYtid:
    def test_watch_url(self):
        assert (
            _extract_ytid("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
            == "dQw4w9WgXcQ"
        )

    def test_watch_url_with_extra_params(self):
        assert (
            _extract_ytid(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ"
            )
            == "dQw4w9WgXcQ"
        )

    def test_youtu_be_short(self):
        assert _extract_ytid("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_shorts_url(self):
        assert (
            _extract_ytid("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        )

    def test_embed_url(self):
        assert (
            _extract_ytid("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        )

    def test_non_youtube_url_returns_none(self):
        assert _extract_ytid("https://soundcloud.com/artist/track") is None

    def test_empty_string_returns_none(self):
        assert _extract_ytid("") is None

    def test_plain_text_returns_none(self):
        assert _extract_ytid("just some text") is None

    def test_youtube_mix_url(self):
        assert (
            _extract_ytid(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ"
            )
            == "dQw4w9WgXcQ"
        )


# ---------------------------------------------------------------------------
# _clean_artist
# ---------------------------------------------------------------------------


class TestCleanArtist:
    def test_explicit_artist_preferred(self):
        # YouTube Music supplies a clean artist tag — use it over the channel.
        assert _clean_artist("Godzilla", "EminemVEVO", "Eminem") == "Eminem"

    def test_uploader_fallback(self):
        assert _clean_artist("Some Song", "Lyrical Lemonade") == "Lyrical Lemonade"

    def test_redundant_uploader_dropped(self):
        # Uploader words already in the title → no duplicate Artist field.
        assert _clean_artist("Eminem - Godzilla", "Eminem") is None

    def test_topic_suffix_stripped(self):
        assert _clean_artist("Godzilla", "Eminem - Topic") == "Eminem"

    def test_vevo_suffix_stripped(self):
        assert _clean_artist("Godzilla", "EminemVEVO") == "Eminem"

    def test_official_marker_stripped(self):
        assert _clean_artist("Blinding Lights", "The Weeknd - Official") == "The Weeknd"

    def test_distinct_uploader_kept(self):
        # MV uploader differs from the song's artist — keep it (best we can do).
        assert (
            _clean_artist("Eminem - Godzilla ft. Juice WRLD", "Lyrical Lemonade")
            == "Lyrical Lemonade"
        )

    def test_empty_inputs(self):
        assert _clean_artist("Some Song", None) is None
        assert _clean_artist("Some Song", "", None) is None

    def test_only_noise_becomes_none(self):
        assert _clean_artist("Some Song", "Official") is None
