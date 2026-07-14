"""
Tests for the pure helpers in cogs/tickets/ (no live Discord dependency).
"""

from cogs.tickets import thread_name, transcript_line

# ── thread_name ────────────────────────────────────────────────────────────────


def test_thread_name_basic():
    assert thread_name(42, "snow") == "ticket-0042-snow"


def test_thread_name_sanitizes_and_lowercases():
    assert thread_name(1, "Cool User!!") == "ticket-0001-cool-user"


def test_thread_name_unsafe_only_falls_back_to_number():
    assert thread_name(7, "🎫🎫🎫") == "ticket-0007"


def test_thread_name_fits_discord_100_char_cap():
    name = thread_name(9999, "x" * 300)
    assert len(name) <= 100
    assert name.startswith("ticket-9999-")


def test_thread_name_five_digit_numbers_still_work():
    assert thread_name(12345, "a") == "ticket-12345-a"


# ── transcript_line ────────────────────────────────────────────────────────────


def test_transcript_line_plain():
    line = transcript_line("2026-07-10 12:00", "snow (1)", "hello", [])
    assert line == "[2026-07-10 12:00] snow (1): hello"


def test_transcript_line_attachments_appended():
    line = transcript_line("t", "a", "look", ["http://x/1.png"])
    assert line == "[t] a: look [attachments: http://x/1.png]"


def test_transcript_line_attachments_only():
    line = transcript_line("t", "a", "", ["http://x/1.png", "http://x/2.png"])
    assert line == "[t] a: [attachments: http://x/1.png http://x/2.png]"


def test_transcript_line_empty_message():
    assert transcript_line("t", "a", "", []) == "[t] a: [no text content]"
