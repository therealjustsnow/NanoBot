"""Reading WYR questions out of an LLM reply (`cogs/fun/helpers.parse_wyr_json`).

The failure this guards is the one that showed up in production every day:
gpt-oss is a reasoning model, its thinking is billed against the same
max_tokens budget as the answer, and the array came back cut off mid-string
("Unterminated string starting at: line 1 column 1019"). A strict json.loads
threw away all twenty questions over one incomplete one.
"""

import json

from cogs.fun.helpers import parse_wyr_json

Q1 = "Would you rather fight one horse-sized duck or a hundred duck-sized horses?"
Q2 = "Would you rather always be ten minutes late or always be twenty minutes early?"
Q3 = "Would you rather taste colours or hear smells?"


def test_plain_array():
    assert parse_wyr_json(json.dumps([Q1, Q2])) == [Q1, Q2]


def test_code_fence_is_stripped():
    assert parse_wyr_json(f"```json\n{json.dumps([Q1])}\n```") == [Q1]
    assert parse_wyr_json(f"```\n{json.dumps([Q1])}\n```") == [Q1]


def test_object_wrapper_is_walked():
    body = json.dumps({"questions": [Q1, Q2]})
    assert parse_wyr_json(body) == [Q1, Q2]


def test_missing_question_mark_is_added_and_doubles_are_not():
    assert parse_wyr_json(json.dumps([Q1.rstrip("?")])) == [Q1]
    assert parse_wyr_json(json.dumps([Q1 + "?"])) == [Q1]


def test_non_questions_are_dropped():
    body = json.dumps(["Here are your questions:", Q1, 7, None, "Would you rather?"])
    assert parse_wyr_json(body) == [Q1]


def test_duplicates_collapse_case_insensitively():
    assert parse_wyr_json(json.dumps([Q1, Q1.upper(), Q2])) == [Q1, Q2]


# ── the actual bug: a reply that ran out of tokens ───────────────────────────
def test_truncated_array_keeps_the_questions_that_landed():
    whole = json.dumps([Q1, Q2, Q3])
    # Cut mid-way through the third string, exactly as a token ceiling does.
    truncated = whole[: whole.index(Q3) + 12]
    assert not truncated.endswith("]")

    got = parse_wyr_json(truncated)
    assert got == [Q1, Q2], "complete questions before the cut must survive"


def test_truncated_first_question_yields_nothing_rather_than_a_fragment():
    whole = json.dumps([Q1, Q2])
    assert parse_wyr_json(whole[:30]) == []


def test_salvage_handles_escapes_inside_a_string():
    q = 'Would you rather say "hello" or "goodbye"?'
    whole = json.dumps([q, Q2])
    truncated = whole[: whole.index(Q2) + 10]
    assert parse_wyr_json(truncated) == [q]


def test_salvage_does_not_invent_questions_from_json_keys():
    # A truncated object-of-objects: keys are complete string literals too, and
    # none of them may be mistaken for a question.
    body = '{"questions": [{"text": "' + Q1 + '"}, {"text": "Would you rather ea'
    assert parse_wyr_json(body) == [Q1]


def test_empty_and_junk_replies_are_empty_lists():
    for body in ("", "   ", "```json\n```", "sorry, I can't do that", "[", "null"):
        assert parse_wyr_json(body) == []


def test_whitespace_is_normalised():
    assert parse_wyr_json(json.dumps(["  Would you rather\n  run  or   walk?  "])) == [
        "Would you rather run or walk?"
    ]
