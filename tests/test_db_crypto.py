"""Tests for utils/db_crypto.py — key resolution, plaintext detection, and the
encrypted-connection / plaintext-migration round-trip.

The round-trip tests need the sqlcipher3 driver (requirements.dev.txt installs
it); they are skipped when it is missing so the rest of the suite still runs.
"""

import os
import sqlite3

import pytest

from utils import db_crypto

# ── resolve_key ────────────────────────────────────────────────────────────────


def test_resolve_key_prefers_env_var(monkeypatch):
    monkeypatch.setenv(db_crypto.ENV_VAR, "env-secret")
    assert db_crypto.resolve_key({"db_encryption_key": "cfg-secret"}) == "env-secret"


def test_resolve_key_falls_back_to_config(monkeypatch):
    monkeypatch.delenv(db_crypto.ENV_VAR, raising=False)
    assert db_crypto.resolve_key({"db_encryption_key": "cfg-secret"}) == "cfg-secret"


def test_resolve_key_none_when_unset(monkeypatch):
    monkeypatch.delenv(db_crypto.ENV_VAR, raising=False)
    assert db_crypto.resolve_key({}) is None
    assert db_crypto.resolve_key({"db_encryption_key": ""}) is None


# ── header sniffing ────────────────────────────────────────────────────────────


def _make_plain_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (a TEXT)")
    conn.execute("INSERT INTO t VALUES ('hello')")
    conn.execute("PRAGMA user_version = 7")
    conn.commit()
    conn.close()


def test_is_plaintext_detects_sqlite_file(tmp_path):
    path = str(tmp_path / "plain.db")
    _make_plain_db(path)
    assert db_crypto._is_plaintext(path)
    assert not db_crypto._is_encrypted_file(path)


def test_is_plaintext_false_for_missing_file(tmp_path):
    path = str(tmp_path / "nope.db")
    assert not db_crypto._is_plaintext(path)
    assert not db_crypto._is_encrypted_file(path)


def test_key_pragma_escapes_quotes():
    assert db_crypto._key_pragma("it's") == "PRAGMA key = 'it''s'"


# ── encrypted round-trip (requires sqlcipher3) ────────────────────────────────

sqlcipher_required = pytest.mark.skipif(
    not db_crypto.available(), reason="sqlcipher3 not installed"
)


@sqlcipher_required
@pytest.mark.asyncio
async def test_connect_encrypted_round_trip(tmp_path):
    path = str(tmp_path / "enc.db")
    conn = await db_crypto.connect(path, "correct horse battery staple")
    await conn.execute("CREATE TABLE t (a TEXT)")
    await conn.execute("INSERT INTO t VALUES ('secret')")
    await conn.commit()
    async with conn.execute("SELECT a FROM t") as cur:
        row = await cur.fetchone()
    assert row["a"] == "secret"  # row factory must be set on the connection
    await conn.close()

    # On-disk file must not be readable as plain SQLite.
    assert db_crypto._is_encrypted_file(path)
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(path).execute("SELECT * FROM t")


@sqlcipher_required
@pytest.mark.asyncio
async def test_connect_wrong_key_raises(tmp_path):
    path = str(tmp_path / "enc.db")
    conn = await db_crypto.connect(path, "right-key-123")
    await conn.execute("CREATE TABLE t (a TEXT)")
    await conn.commit()
    await conn.close()

    with pytest.raises(RuntimeError, match="wrong db_encryption_key"):
        await db_crypto.connect(path, "wrong-key-456")


@sqlcipher_required
@pytest.mark.asyncio
async def test_plaintext_database_is_migrated(tmp_path):
    path = str(tmp_path / "data.db")
    _make_plain_db(path)

    conn = await db_crypto.connect(path, "migrate-me-789")
    async with conn.execute("SELECT a FROM t") as cur:
        row = await cur.fetchone()
    assert row["a"] == "hello"
    # PRAGMA user_version drives db.py's schema migrations — must survive.
    async with conn.execute("PRAGMA user_version") as cur:
        version = (await cur.fetchone())[0]
    assert version == 7
    await conn.close()

    assert db_crypto._is_encrypted_file(path)
    assert os.path.exists(path + ".plain.bak")  # plaintext backup kept


@sqlcipher_required
@pytest.mark.asyncio
async def test_encrypted_file_without_key_refused(tmp_path):
    path = str(tmp_path / "enc.db")
    conn = await db_crypto.connect(path, "some-key-000")
    await conn.execute("CREATE TABLE t (a TEXT)")
    await conn.commit()
    await conn.close()

    with pytest.raises(RuntimeError, match="no key is configured"):
        await db_crypto.connect(path, None)


@pytest.mark.asyncio
async def test_connect_plain_when_no_key(tmp_path):
    path = str(tmp_path / "plain.db")
    conn = await db_crypto.connect(path, None)
    await conn.execute("CREATE TABLE t (a TEXT)")
    await conn.commit()
    await conn.close()
    assert db_crypto._is_plaintext(path)
