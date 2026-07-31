"""
Tests for web/security.py — sessions, CSRF tokens, and OAuth state.

Pure stdlib crypto over plain dicts, so none of this needs a socket, a database
or a gateway. What is actually being asserted is that every failure mode
collapses to None rather than to a partially-trusted payload: a caller that
branches on "is there a session?" must not be able to be handed a forged one.
"""

import time

import pytest

from web import security as sec

SECRET = "a" * 48
OTHER = "b" * 48


# ── encoding ──────────────────────────────────────────────────────────────────
def test_b64_roundtrip_survives_padding_strip():
    for raw in (b"", b"a", b"ab", b"abc", b"\x00\xff" * 17):
        assert sec.b64d(sec.b64e(raw)) == raw


def test_b64d_rejects_garbage():
    with pytest.raises(ValueError):
        sec.b64d("!!!not base64!!!")


def test_constant_time_still_compares_correctly():
    assert sec.constant_time("abc", "abc")
    assert not sec.constant_time("abc", "abd")
    assert not sec.constant_time("abc", "abcd")


# ── sessions ──────────────────────────────────────────────────────────────────
def test_session_roundtrip():
    token = sec.sign_session({"uid": "42", "name": "Ada"}, SECRET, 3600)
    payload = sec.verify_session(token, SECRET)
    assert payload["uid"] == "42"
    assert payload["name"] == "Ada"
    assert payload["v"] == sec.SESSION_VERSION
    assert payload["sid"]  # minted for us, and it is what CSRF binds to


def test_session_rejects_a_different_secret():
    token = sec.sign_session({"uid": "42"}, SECRET, 3600)
    assert sec.verify_session(token, OTHER) is None


def test_session_rejects_a_tampered_payload():
    """The whole point: editing the body has to invalidate the signature.

    A session carries the user id, so a payload that could be edited without
    detection would be an "act as anyone" button.
    """
    token = sec.sign_session({"uid": "42"}, SECRET, 3600)
    body, sig = token.split(".")
    forged = sec.b64e(b'{"uid":"1","v":1,"exp":99999999999,"sid":"x"}')
    assert sec.verify_session(f"{forged}.{sig}", SECRET) is None


def test_session_expires():
    token = sec.sign_session({"uid": "42"}, SECRET, 10)
    assert sec.verify_session(token, SECRET, now=time.time() + 5) is not None
    assert sec.verify_session(token, SECRET, now=time.time() + 11) is None


def test_session_rejects_an_old_version():
    """A payload-shape change signs everyone out instead of crashing on a cookie."""
    token = sec.sign_session({"uid": "42"}, SECRET, 3600)
    body, _sig = token.split(".")
    import json

    payload = json.loads(sec.b64d(body))
    payload["v"] = sec.SESSION_VERSION + 1
    new_body = sec.b64e(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    )
    resigned = f"{new_body}.{sec._mac(SECRET, sec._SESSION_LABEL, new_body.encode())}"
    assert sec.verify_session(resigned, SECRET) is None


@pytest.mark.parametrize(
    "token",
    ["", "nodot", "a.b.c", ".", "x.", ".y", "!!!.???"],
)
def test_session_rejects_malformed_tokens(token):
    assert sec.verify_session(token, SECRET) is None


def test_session_rejects_a_non_object_payload():
    body = sec.b64e(b'"just a string"')
    token = f"{body}.{sec._mac(SECRET, sec._SESSION_LABEL, body.encode())}"
    assert sec.verify_session(token, SECRET) is None


def test_session_without_a_secret_is_never_valid():
    token = sec.sign_session({"uid": "42"}, SECRET, 3600)
    assert sec.verify_session(token, "") is None


# ── CSRF ──────────────────────────────────────────────────────────────────────
def test_csrf_is_bound_to_the_session():
    a = sec.verify_session(sec.sign_session({"uid": "1"}, SECRET, 60), SECRET)
    b = sec.verify_session(sec.sign_session({"uid": "1"}, SECRET, 60), SECRET)
    assert sec.csrf_for(a, SECRET) != sec.csrf_for(b, SECRET)
    assert sec.csrf_ok(a, SECRET, sec.csrf_for(a, SECRET))
    assert not sec.csrf_ok(a, SECRET, sec.csrf_for(b, SECRET))


def test_csrf_is_stable_for_one_session():
    session = sec.verify_session(sec.sign_session({"uid": "1"}, SECRET, 60), SECRET)
    assert sec.csrf_for(session, SECRET) == sec.csrf_for(session, SECRET)


def test_csrf_needs_both_halves():
    session = sec.verify_session(sec.sign_session({"uid": "1"}, SECRET, 60), SECRET)
    assert not sec.csrf_ok(None, SECRET, sec.csrf_for(session, SECRET))
    assert not sec.csrf_ok(session, SECRET, None)
    assert not sec.csrf_ok(session, SECRET, "")


def test_csrf_token_is_not_the_session_signature():
    """Domain separation: the two uses of the secret must not collide."""
    token = sec.sign_session({"uid": "1"}, SECRET, 60)
    session = sec.verify_session(token, SECRET)
    assert sec.csrf_for(session, SECRET) != token.split(".")[1]


# ── OAuth state ───────────────────────────────────────────────────────────────
def test_state_roundtrip():
    raw = sec.new_state()
    signed = sec.sign_state(raw, SECRET)
    assert sec.verify_state(signed, SECRET) == raw


def test_state_expires_and_cannot_be_forged():
    signed = sec.sign_state("hello", SECRET, ttl_seconds=5)
    assert sec.verify_state(signed, SECRET, now=time.time() + 6) is None
    assert sec.verify_state(signed, OTHER) is None
    assert sec.verify_state("garbage", SECRET) is None


def test_new_state_values_differ():
    assert len({sec.new_state() for _ in range(20)}) == 20


def test_new_secret_is_long_enough_to_matter():
    secret = sec.new_secret()
    assert len(secret) >= 32
    assert sec.new_secret() != secret
