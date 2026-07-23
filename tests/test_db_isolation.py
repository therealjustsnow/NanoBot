"""
Regression tests for test-database isolation.

The db package routes ``db._db`` and ``db._DB_PATH`` to utils/db/_core, so a
``monkeypatch.setattr(db, "_DB_PATH", ...)`` in a fixture genuinely redirects
``db.init()`` instead of silently opening the real data/nanobot.db (the bug:
the package used to keep a stale copy of ``_DB_PATH`` that init() never read).
"""

import os

import utils.db as db
from utils.db import _core


def test_db_path_attribute_routes_to_core(monkeypatch):
    monkeypatch.setattr(db, "_DB_PATH", "/nonexistent/route-check.db")
    assert _core._DB_PATH == "/nonexistent/route-check.db"
    assert db._DB_PATH == "/nonexistent/route-check.db"


async def test_init_opens_the_monkeypatched_path(tmp_path, monkeypatch):
    target = str(tmp_path / "isolated.db")
    monkeypatch.setattr(db, "_DB_PATH", target)
    await db.init()
    try:
        assert os.path.exists(target)
        # And the connection slot routes too: closing via the package clears
        # the same slot init() populated.
        assert db._db is not None
    finally:
        await db.close()
    assert db._db is None
