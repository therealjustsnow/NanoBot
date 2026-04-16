"""
tests/test_helpers.py
Unit tests for pure utility functions in utils/helpers.py.

These functions have no Discord dependency at runtime, so no mocking is needed.
Note: helpers.py imports discord at module level, so discord.py must be installed
(via requirements.txt) before running these tests.
"""

import pytest

from utils.helpers import (
    fmt_duration,
    fmt_interval,
    parse_duration,
    parse_duration_from_end,
    parse_interval,
)


# ══════════════════════════════════════════════════════════════════════════════
#  parse_duration
# ══════════════════════════════════════════════════════════════════════════════


class TestParseDuration:
    def test_seconds_short(self):
        assert parse_duration("30s") == 30

    def test_minutes_short(self):
        assert parse_duration("5m") == 300

    def test_hours_short(self):
        assert parse_duration("2h") == 7200

    def test_days_short(self):
        assert parse_duration("1d") == 86400

    def test_weeks_short(self):
        assert parse_duration("1w") == 604800

    def test_bare_number_is_seconds(self):
        assert parse_duration("60") == 60

    def test_long_form_hours(self):
        assert parse_duration("2 hours") == 7200

    def test_long_form_minutes(self):
        assert parse_duration("30 minutes") == 1800

    def test_long_form_days(self):
        assert parse_duration("3 days") == 259200

    def test_long_form_singular(self):
        assert parse_duration("1 hour") == 3600

    def test_zero_seconds(self):
        assert parse_duration("0s") == 0

    def test_invalid_returns_none(self):
        assert parse_duration("invalid") is None

    def test_none_input_returns_none(self):
        assert parse_duration(None) is None

    def test_empty_string_returns_none(self):
        assert parse_duration("") is None

    def test_case_insensitive_unit(self):
        assert parse_duration("5M") == 300


# ══════════════════════════════════════════════════════════════════════════════
#  parse_duration_from_end
# ══════════════════════════════════════════════════════════════════════════════


class TestParseDurationFromEnd:
    def test_in_hours(self):
        text, secs = parse_duration_from_end("go for a run in 2 hours")
        assert text == "go for a run"
        assert secs == 7200

    def test_short_unit_appended(self):
        text, secs = parse_duration_from_end("call mum 30m")
        assert text == "call mum"
        assert secs == 1800

    def test_in_minutes(self):
        text, secs = parse_duration_from_end("stand up in 45 minutes")
        assert text == "stand up"
        assert secs == 2700

    def test_no_duration_unchanged(self):
        text, secs = parse_duration_from_end("no duration here")
        assert text == "no duration here"
        assert secs is None

    def test_in_days(self):
        text, secs = parse_duration_from_end("renew subscription in 7 days")
        assert text == "renew subscription"
        assert secs == 604800

    def test_empty_string(self):
        text, secs = parse_duration_from_end("")
        assert text == ""
        assert secs is None


# ══════════════════════════════════════════════════════════════════════════════
#  fmt_duration
# ══════════════════════════════════════════════════════════════════════════════


class TestFmtDuration:
    def test_seconds_only(self):
        assert fmt_duration(45) == "45s"

    def test_minutes_and_seconds(self):
        assert fmt_duration(90) == "1m 30s"

    def test_exact_hour(self):
        assert fmt_duration(3600) == "1h"

    def test_days_and_hours(self):
        # 90061 = 1d + 1h + 1m + 1s; capped at 2 units → "1d 1h"
        assert fmt_duration(90061) == "1d 1h"

    def test_zero(self):
        assert fmt_duration(0) == "0s"

    def test_negative(self):
        assert fmt_duration(-5) == "0s"

    def test_exact_minute(self):
        assert fmt_duration(60) == "1m"

    def test_exact_day(self):
        assert fmt_duration(86400) == "1d"


# ══════════════════════════════════════════════════════════════════════════════
#  parse_interval
# ══════════════════════════════════════════════════════════════════════════════


class TestParseInterval:
    def test_daily_keyword(self):
        assert parse_interval("daily") == 86400

    def test_weekly_keyword(self):
        assert parse_interval("weekly") == 604800

    def test_every_two_weeks(self):
        assert parse_interval("every 2 weeks") == 1209600

    def test_short_duration_fallthrough(self):
        assert parse_interval("1h") == 3600

    def test_hourly_keyword(self):
        assert parse_interval("hourly") == 3600

    def test_monthly_keyword(self):
        assert parse_interval("monthly") == 30 * 86400

    def test_every_day(self):
        assert parse_interval("every day") == 86400

    def test_invalid_returns_none(self):
        assert parse_interval("invalid") is None

    def test_none_returns_none(self):
        assert parse_interval(None) is None

    def test_empty_returns_none(self):
        assert parse_interval("") is None


# ══════════════════════════════════════════════════════════════════════════════
#  fmt_interval
# ══════════════════════════════════════════════════════════════════════════════


class TestFmtInterval:
    def test_one_hour(self):
        assert fmt_interval(3600) == "1 hour"

    def test_two_hours(self):
        assert fmt_interval(7200) == "2 hours"

    def test_one_day(self):
        assert fmt_interval(86400) == "1 day"

    def test_two_weeks(self):
        assert fmt_interval(1209600) == "2 weeks"

    def test_one_week(self):
        assert fmt_interval(604800) == "1 week"

    def test_one_minute(self):
        assert fmt_interval(60) == "1 minute"

    def test_irregular_falls_back_to_fmt_duration(self):
        # 3661 = 1h 1m 1s — doesn't divide evenly into any whole unit, falls back
        assert fmt_interval(3661) == "1h 1m"

    def test_zero(self):
        assert fmt_interval(0) == "0s"
