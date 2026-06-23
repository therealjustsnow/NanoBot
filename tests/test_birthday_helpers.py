"""
Tests for the pure date helpers in cogs/birthday.py (no Discord deps).
"""

from datetime import date

from cogs.birthday import (
    _ffmpeg_song_cmd,
    _HB_NOTES,
    _TZ_CHOICES,
    age_on,
    days_until_birthday,
    fmt_birthday,
    guess_timezone_from_regions,
    is_birthday_today,
    next_birthday_date,
    parse_birthday,
)

# ── parse_birthday ─────────────────────────────────────────────────────────────


def test_parse_month_name_forms():
    assert parse_birthday("March 5") == (3, 5, None)
    assert parse_birthday("march 5, 1998") == (3, 5, 1998)
    assert parse_birthday("5 Mar 1998") == (3, 5, 1998)
    assert parse_birthday("Dec 25th") == (12, 25, None)
    assert parse_birthday("  sept 1  ") == (9, 1, None)


def test_parse_numeric_forms():
    assert parse_birthday("03/05") == (3, 5, None)
    assert parse_birthday("3-5-1998") == (3, 5, 1998)
    assert parse_birthday("1998-03-05") == (3, 5, 1998)  # ISO is year-first


def test_parse_rejects_garbage_and_out_of_range():
    assert parse_birthday("") is None
    assert parse_birthday("not a date") is None
    assert parse_birthday("13/40") is None  # bad month + day
    assert parse_birthday("February 30") is None
    assert parse_birthday("March 5, 1700") is None  # year out of range


def test_parse_allows_leap_day():
    assert parse_birthday("Feb 29") == (2, 29, None)


# ── formatting ─────────────────────────────────────────────────────────────────


def test_fmt_birthday():
    assert fmt_birthday(3, 5) == "March 5"
    assert fmt_birthday(3, 5, 1998) == "March 5, 1998"


# ── countdown + today ──────────────────────────────────────────────────────────


def test_days_until_counts_today_as_zero():
    today = date(2026, 3, 5)
    assert days_until_birthday(3, 5, today) == 0


def test_days_until_rolls_to_next_year():
    today = date(2026, 3, 6)  # one day after
    assert days_until_birthday(3, 5, today) == 364
    assert next_birthday_date(3, 5, today) == date(2027, 3, 5)


def test_is_birthday_today_exact():
    assert is_birthday_today(3, 5, date(2026, 3, 5))
    assert not is_birthday_today(3, 6, date(2026, 3, 5))


def test_leap_day_birthday_celebrated_feb28_in_non_leap_year():
    # 2026 is not a leap year — Feb 29 birthday lands on Feb 28.
    assert is_birthday_today(2, 29, date(2026, 2, 28))
    # 2028 IS a leap year — Feb 28 is not the birthday, Feb 29 is.
    assert not is_birthday_today(2, 29, date(2028, 2, 28))
    assert is_birthday_today(2, 29, date(2028, 2, 29))


def test_next_birthday_leap_day_falls_back():
    # From Jan 2027 (non-leap), next Feb 29 birthday → Feb 28 2027.
    assert next_birthday_date(2, 29, date(2027, 1, 1)) == date(2027, 2, 28)


# ── age ────────────────────────────────────────────────────────────────────────


def test_age_on_birthday_is_year_diff():
    # Born 2000-03-05; on the 2026 birthday they turn 26.
    assert age_on(3, 5, 2000, date(2026, 3, 5)) == 26


def test_age_before_birthday_this_year_subtracts_one():
    assert age_on(3, 5, 2000, date(2026, 3, 4)) == 25


def test_age_unknown_when_no_year():
    assert age_on(3, 5, None, date(2026, 3, 5)) is None


# ── song command builder ───────────────────────────────────────────────────────


# ── timezone guessing + choices ────────────────────────────────────────────────


def test_guess_timezone_picks_most_common_region():
    assert (
        guess_timezone_from_regions(["us-central", "us-central", "us-east"])
        == "America/Chicago"
    )


def test_guess_timezone_ignores_unknown_and_none():
    assert guess_timezone_from_regions(["mars", None, "europe"]) == "Europe/Paris"
    assert guess_timezone_from_regions([]) is None
    assert guess_timezone_from_regions(["nope", None]) is None


def test_tz_choices_are_valid_iana_and_within_select_limit():
    from zoneinfo import available_timezones

    valid = available_timezones()
    assert 0 < len(_TZ_CHOICES) <= 25  # Discord select-menu cap
    for label, iana, _emoji in _TZ_CHOICES:
        assert iana in valid, f"{iana} is not a valid IANA timezone"
        assert label and len(label) <= 100


# ── song command builder ───────────────────────────────────────────────────────


def test_song_cmd_has_one_input_per_note_and_concat():
    cmd = _ffmpeg_song_cmd("/tmp/x.wav")
    assert cmd[0] == "ffmpeg"
    assert cmd[-1] == "/tmp/x.wav"
    # One "-i sine=..." per note.
    sine_inputs = [a for a in cmd if a.startswith("sine=frequency=")]
    assert len(sine_inputs) == len(_HB_NOTES)
    filtergraph = cmd[cmd.index("-filter_complex") + 1]
    assert f"concat=n={len(_HB_NOTES)}:v=0:a=1[out]" in filtergraph
