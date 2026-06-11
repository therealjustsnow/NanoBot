"""Encryption-at-rest for the SQLite databases via SQLCipher.

When an encryption key is configured (``db_encryption_key`` in config.ini's
``[bot]`` section, or the ``NANOBOT_DB_KEY`` environment variable, which takes
precedence), ``connect()`` opens databases through the ``sqlcipher3`` driver
instead of the stdlib ``sqlite3``, so ``data/nanobot.db`` and ``data/cache.db``
are AES-256 encrypted on disk. The first start with a key transparently
migrates an existing plaintext database in place (the plaintext original is
kept next to it as ``<name>.plain.bak`` — delete it once you've verified the
bot runs).

Without a key, everything routes through plain aiosqlite: zero behaviour
change, and ``sqlcipher3`` does not need to be installed. The driver is the
``sqlcipher3-binary`` PyPI package (prebuilt wheels, no system SQLCipher
needed).

Things to know about the encrypted mode:

* The key is a passphrase (SQLCipher derives the actual cipher key from it
  with PBKDF2). Losing it means losing the database — there is no recovery.
* Changing the key does NOT re-encrypt an existing database; the old key is
  still required. Re-key manually with sqlcipher's ``PRAGMA rekey`` if needed.
* sqlcipher3 raises its own exception classes (not ``sqlite3.*``), so code
  catching constraint violations must use :data:`INTEGRITY_ERRORS` instead of
  ``aiosqlite.IntegrityError``.
"""

import asyncio
import logging
import os

import aiosqlite

try:
    from sqlcipher3 import dbapi2 as _sqlcipher
except ImportError:  # encryption disabled or driver not installed
    _sqlcipher = None

log = logging.getLogger("NanoBot.db_crypto")

ENV_VAR = "NANOBOT_DB_KEY"

# First 16 bytes of every plaintext SQLite file; an encrypted database has a
# random-looking header instead.
_SQLITE_MAGIC = b"SQLite format 3\x00"

# Catch-tuple for UNIQUE/PK violations that works for both drivers (the
# sqlcipher3 exception classes do not inherit from sqlite3's).
INTEGRITY_ERRORS: tuple = (
    (aiosqlite.IntegrityError, _sqlcipher.IntegrityError)
    if _sqlcipher
    else (aiosqlite.IntegrityError,)
)


def available() -> bool:
    """True when the sqlcipher3 driver is importable."""
    return _sqlcipher is not None


def resolve_key(cfg: dict) -> str | None:
    """Return the active encryption key, or None when encryption is off."""
    key = os.getenv(ENV_VAR) or cfg.get("db_encryption_key")
    return str(key) if key else None


def _key_pragma(key: str) -> str:
    # PRAGMA values can't be bound as parameters; escape by doubling quotes.
    return "PRAGMA key = '%s'" % key.replace("'", "''")


def _is_plaintext(path: str) -> bool:
    """True when `path` exists and is an unencrypted SQLite database."""
    try:
        with open(path, "rb") as fh:
            return fh.read(16) == _SQLITE_MAGIC
    except (FileNotFoundError, OSError):
        return False


def _migrate_plaintext(path: str, key: str) -> None:
    """Encrypt an existing plaintext database in place (plaintext → SQLCipher).

    Copies every table into a freshly keyed database via sqlcipher_export(),
    carries PRAGMA user_version across (it lives in the file header, which the
    export does not copy — the schema-migration runner in utils/db.py depends
    on it), then swaps the files. The plaintext original is kept as
    ``<path>.plain.bak``.
    """
    tmp = path + ".enc.tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    src = _sqlcipher.connect(path)
    try:
        # Fold any leftover WAL into the main file so the export sees all data.
        src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        src.execute("ATTACH DATABASE ? AS encrypted KEY ?", (tmp, key))
        src.execute("SELECT sqlcipher_export('encrypted')")
        version = src.execute("PRAGMA user_version").fetchone()[0]
        src.execute(f"PRAGMA encrypted.user_version = {int(version)}")
        src.execute("DETACH DATABASE encrypted")
        src.commit()
    except Exception:
        src.close()
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    src.close()
    backup = path + ".plain.bak"
    os.replace(path, backup)
    os.replace(tmp, path)
    log.warning(
        f"Encrypted {path} (plaintext copy kept as {backup} — "
        "delete it once you've verified the bot runs)"
    )


def _open_encrypted(path: str, key: str):
    """Synchronous connector: open `path` with sqlcipher3 and apply the key."""
    conn = _sqlcipher.connect(path)
    conn.execute(_key_pragma(key))
    try:
        # First real read; fails with "file is not a database" on a wrong key.
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except _sqlcipher.DatabaseError as e:
        conn.close()
        raise RuntimeError(
            f"Cannot decrypt {path} — wrong db_encryption_key / "
            f"{ENV_VAR}? (key changes don't re-encrypt an existing database)"
        ) from e
    conn.row_factory = _sqlcipher.Row
    return conn


async def connect(path: str, key: str | None) -> aiosqlite.Connection:
    """Open `path`, encrypted when `key` is set, plain aiosqlite otherwise.

    The returned connection already has its row factory set (sqlite3.Row or
    the sqlcipher equivalent). Raises RuntimeError on a wrong key, a missing
    driver, or an encrypted file opened without a key.
    """
    if not key:
        if _is_encrypted_file(path):
            raise RuntimeError(
                f"{path} is encrypted but no key is configured — set "
                f"db_encryption_key in config.ini or the {ENV_VAR} env var"
            )
        conn = await aiosqlite.connect(path)
        conn.row_factory = aiosqlite.Row
        return conn

    if _sqlcipher is None:
        raise RuntimeError(
            "db_encryption_key is set but the sqlcipher3 driver is missing — "
            "install it with: pip install sqlcipher3-binary"
        )
    if _is_plaintext(path):
        # Blocking file rewrite; keep the event loop responsive at startup.
        await asyncio.to_thread(_migrate_plaintext, path, key)
    return await aiosqlite.Connection(
        lambda: _open_encrypted(path, key), iter_chunk_size=64
    )


def _is_encrypted_file(path: str) -> bool:
    """True when `path` exists, is non-empty, and lacks the SQLite header."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
        return bool(head) and head != _SQLITE_MAGIC
    except (FileNotFoundError, OSError):
        return False
