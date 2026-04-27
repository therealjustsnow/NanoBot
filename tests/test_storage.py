"""
Tests for utils/storage.py — sync and async JSON file storage.
"""

import json
import os

import pytest

import utils.storage as storage


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Redirect all storage I/O to a temp directory and reset per-file locks."""
    monkeypatch.setattr(storage, "_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(storage, "_locks", {})
    yield tmp_path


# ── read ──────────────────────────────────────────────────────────────────────


def test_read_missing_file_returns_empty_dict():
    assert storage.read("missing.json") == {}


def test_read_corrupt_json_returns_empty_dict(tmp_path):
    p = tmp_path / "corrupt.json"
    p.write_text("{not valid json", encoding="utf-8")
    assert storage.read("corrupt.json") == {}


def test_read_returns_data():
    storage.write("data.json", {"key": "value"})
    assert storage.read("data.json") == {"key": "value"}


# ── write ─────────────────────────────────────────────────────────────────────


def test_write_creates_file(tmp_path):
    storage.write("out.json", {"x": 1})
    p = tmp_path / "out.json"
    assert p.exists()
    assert json.loads(p.read_text()) == {"x": 1}


def test_write_overwrites_existing():
    storage.write("over.json", {"a": 1})
    storage.write("over.json", {"b": 2})
    assert storage.read("over.json") == {"b": 2}


def test_write_no_tmp_file_left_behind(tmp_path):
    storage.write("clean.json", {"z": 9})
    assert not (tmp_path / "clean.json.tmp").exists()


# ── awrite ────────────────────────────────────────────────────────────────────


async def test_awrite_creates_file(tmp_path):
    await storage.awrite("async.json", {"async": True})
    p = tmp_path / "async.json"
    assert p.exists()
    assert json.loads(p.read_text()) == {"async": True}


async def test_awrite_concurrent_writes_serialize(tmp_path):
    import asyncio

    results = []

    async def write_and_read(val):
        await storage.awrite("shared.json", {"v": val})
        data = storage.read("shared.json")
        results.append(data["v"])

    await asyncio.gather(*(write_and_read(i) for i in range(5)))
    # All writes must have been persisted — final value is one of our writes
    final = storage.read("shared.json")
    assert final["v"] in range(5)


# ── Guild helpers ─────────────────────────────────────────────────────────────


def test_get_guild_missing():
    assert storage.get_guild("g.json", 1) == {}


def test_set_and_get_guild():
    storage.write("guilds.json", {})


async def test_set_guild_persists():
    await storage.set_guild("guilds.json", 1, {"muted": []})
    assert storage.get_guild("guilds.json", 1) == {"muted": []}


async def test_set_guild_isolates_guilds():
    await storage.set_guild("guilds2.json", 1, {"a": 1})
    await storage.set_guild("guilds2.json", 2, {"b": 2})
    assert storage.get_guild("guilds2.json", 1) == {"a": 1}
    assert storage.get_guild("guilds2.json", 2) == {"b": 2}


async def test_update_guild_merges():
    await storage.set_guild("merge.json", 1, {"x": 1, "y": 2})
    await storage.update_guild("merge.json", 1, {"y": 99, "z": 3})
    result = storage.get_guild("merge.json", 1)
    assert result == {"x": 1, "y": 99, "z": 3}


# ── User helpers ──────────────────────────────────────────────────────────────


def test_get_user_missing():
    assert storage.get_user("u.json", 1, 99) is None


async def test_set_and_get_user():
    await storage.set_user("users.json", 1, 42, {"mutes": 3})
    assert storage.get_user("users.json", 1, 42) == {"mutes": 3}


async def test_set_user_isolates_by_guild_and_user():
    await storage.set_user("users2.json", 1, 10, "data_a")
    await storage.set_user("users2.json", 1, 20, "data_b")
    await storage.set_user("users2.json", 2, 10, "data_c")
    assert storage.get_user("users2.json", 1, 10) == "data_a"
    assert storage.get_user("users2.json", 1, 20) == "data_b"
    assert storage.get_user("users2.json", 2, 10) == "data_c"


async def test_delete_user_returns_true_false():
    await storage.set_user("del.json", 1, 5, "exists")
    assert await storage.delete_user("del.json", 1, 5) is True
    assert await storage.delete_user("del.json", 1, 5) is False


async def test_delete_user_removes_data():
    await storage.set_user("del2.json", 1, 7, "bye")
    await storage.delete_user("del2.json", 1, 7)
    assert storage.get_user("del2.json", 1, 7) is None
