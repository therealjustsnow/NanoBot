"""
Tests for utils/db.py — all run against an in-memory SQLite database.
"""

import time

import aiosqlite
import pytest

import utils.db as db


@pytest.fixture(autouse=True)
async def database(monkeypatch):
    """Fresh in-memory DB wired into utils.db for every test."""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    monkeypatch.setattr(db, "_db", conn)

    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS tags (
            guild_id  TEXT NOT NULL,
            scope     TEXT NOT NULL,
            name      TEXT NOT NULL,
            content   TEXT,
            image_url TEXT,
            by_id     TEXT,
            by_name   TEXT,
            PRIMARY KEY (guild_id, scope, name)
        );
        CREATE TABLE IF NOT EXISTS notes (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id  TEXT NOT NULL,
            user_id   TEXT NOT NULL,
            content   TEXT NOT NULL,
            by_id     TEXT NOT NULL,
            by_name   TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS notes_guild_user ON notes (guild_id, user_id);
        CREATE TABLE IF NOT EXISTS prefixes (
            guild_id  TEXT PRIMARY KEY,
            prefix    TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS unban_schedules (
            key       TEXT PRIMARY KEY,
            guild_id  TEXT NOT NULL,
            user_id   TEXT NOT NULL,
            until     REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS slow_schedules (
            channel_id  TEXT PRIMARY KEY,
            guild_id    TEXT NOT NULL,
            until       REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reminders (
            id          TEXT PRIMARY KEY,
            target_id   TEXT NOT NULL,
            set_by_id   TEXT NOT NULL,
            guild_id    TEXT NOT NULL,
            channel_id  TEXT NOT NULL,
            message     TEXT NOT NULL,
            due         REAL NOT NULL,
            duration    REAL NOT NULL,
            dm          INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS reminders_target ON reminders (target_id);
        CREATE INDEX IF NOT EXISTS reminders_setter ON reminders (set_by_id);
    """)
    await conn.commit()

    await db._ensure_warnings_tables()
    await db._ensure_welcome_tables()
    await db._ensure_votes_table()
    await db._ensure_recurring_table()
    await db._ensure_role_panels_tables()
    await db._migrate_role_panel_entries()
    await db._ensure_auditlog_tables()
    await db._migrate_auditlog_null_events()
    await db._ensure_automod_tables()

    yield conn

    await conn.close()
    monkeypatch.setattr(db, "_db", None)


# ── Tags ──────────────────────────────────────────────────────────────────────


async def test_get_tag_missing():
    assert await db.get_tag(1, "hello", 99) is None


async def test_set_and_get_personal_tag():
    await db.set_tag(1, "42", "greet", "hello", None)
    tag = await db.get_tag(1, "greet", 42)
    assert tag["content"] == "hello"
    assert tag["image_url"] is None


async def test_set_and_get_global_tag():
    await db.set_tag(1, "global", "rules", "be nice", None, by_id="1", by_name="Admin")
    tag = await db.get_tag(1, "rules", 99)
    assert tag["content"] == "be nice"


async def test_personal_tag_takes_priority_over_global():
    await db.set_tag(1, "global", "tip", "global tip", None)
    await db.set_tag(1, "55", "tip", "personal tip", None)
    tag = await db.get_tag(1, "tip", 55)
    assert tag["content"] == "personal tip"


async def test_tag_exists_true_and_false():
    await db.set_tag(1, "global", "x", "content", None)
    assert await db.tag_exists(1, "global", "x") is True
    assert await db.tag_exists(1, "global", "y") is False


async def test_delete_tag_returns_true_false():
    await db.set_tag(1, "global", "del_me", "bye", None)
    assert await db.delete_tag(1, "global", "del_me") is True
    assert await db.delete_tag(1, "global", "del_me") is False


async def test_update_tag_content():
    await db.set_tag(1, "global", "editable", "old", None)
    await db.update_tag_content(1, "global", "editable", "new")
    tag = await db.get_tag(1, "editable", 0)
    assert tag["content"] == "new"


async def test_update_tag_image():
    await db.set_tag(1, "global", "imgtest", "txt", None)
    await db.update_tag_image(1, "global", "imgtest", "https://example.com/img.png")
    tag = await db.get_tag(1, "imgtest", 0)
    assert tag["image_url"] == "https://example.com/img.png"


async def test_get_personal_tags():
    await db.set_tag(1, "7", "a", "aaa", None)
    await db.set_tag(1, "7", "b", "bbb", None)
    await db.set_tag(1, "global", "c", "ccc", None)
    tags = await db.get_personal_tags(1, 7)
    assert set(tags.keys()) == {"a", "b"}


async def test_get_global_tags():
    await db.set_tag(1, "global", "g1", "v1", None, by_id="1", by_name="Admin")
    await db.set_tag(1, "global", "g2", "v2", None)
    tags = await db.get_global_tags(1)
    assert "g1" in tags and "g2" in tags
    assert tags["g1"]["by_name"] == "Admin"


async def test_find_tag_scope_personal():
    await db.set_tag(1, "10", "mine", "val", None)
    scope = await db.find_tag_scope(1, "mine", 10)
    assert scope == "10"


async def test_find_tag_scope_global():
    await db.set_tag(1, "global", "shared", "val", None)
    scope = await db.find_tag_scope(1, "shared", 99)
    assert scope == "global"


async def test_find_tag_scope_none():
    assert await db.find_tag_scope(1, "nope", 1) is None


# ── Notes ─────────────────────────────────────────────────────────────────────


async def test_add_note_returns_count():
    count = await db.add_note(1, 2, "first note", "3", "Mod", "2024-01-01")
    assert count == 1
    count = await db.add_note(1, 2, "second note", "3", "Mod", "2024-01-02")
    assert count == 2


async def test_get_notes_ordered():
    await db.add_note(1, 2, "note A", "3", "Mod", "2024-01-01")
    await db.add_note(1, 2, "note B", "3", "Mod", "2024-01-02")
    notes = await db.get_notes(1, 2)
    assert [n["note"] for n in notes] == ["note A", "note B"]


async def test_get_note_count():
    await db.add_note(1, 5, "x", "1", "A", "2024-01-01")
    await db.add_note(1, 5, "y", "1", "A", "2024-01-01")
    assert await db.get_note_count(1, 5) == 2


async def test_clear_notes_returns_deleted():
    await db.add_note(1, 9, "n1", "1", "A", "2024-01-01")
    await db.add_note(1, 9, "n2", "1", "A", "2024-01-01")
    deleted = await db.clear_notes(1, 9)
    assert deleted == 2
    assert await db.get_note_count(1, 9) == 0


async def test_notes_isolated_by_guild():
    await db.add_note(1, 2, "guild1 note", "3", "Mod", "2024-01-01")
    assert await db.get_note_count(2, 2) == 0


# ── Prefixes ──────────────────────────────────────────────────────────────────


async def test_get_prefix_missing():
    assert await db.get_prefix(1) is None


async def test_set_and_get_prefix():
    await db.set_prefix(1, "!")
    assert await db.get_prefix(1) == "!"


async def test_set_prefix_upsert():
    await db.set_prefix(1, "!")
    await db.set_prefix(1, "?")
    assert await db.get_prefix(1) == "?"


async def test_get_all_prefixes():
    await db.set_prefix(10, "n!")
    await db.set_prefix(20, ">>")
    prefixes = await db.get_all_prefixes()
    assert prefixes["10"] == "n!"
    assert prefixes["20"] == ">>"


# ── Unban schedules ───────────────────────────────────────────────────────────


async def test_set_and_get_all_unbans():
    await db.set_unban("1:2", 1, 2, 9999.0)
    unbans = await db.get_all_unbans()
    assert "1:2" in unbans
    assert unbans["1:2"]["until"] == 9999.0


async def test_remove_unban():
    await db.set_unban("1:3", 1, 3, 9999.0)
    await db.remove_unban("1:3")
    assert "1:3" not in await db.get_all_unbans()


# ── Slow schedules ────────────────────────────────────────────────────────────


async def test_set_and_get_all_slows():
    await db.set_slow(100, 1, 5000.0)
    slows = await db.get_all_slows()
    assert "100" in slows
    assert slows["100"]["until"] == 5000.0


async def test_remove_slow():
    await db.set_slow(200, 1, 5000.0)
    await db.remove_slow(200)
    assert "200" not in await db.get_all_slows()


# ── Reminders ─────────────────────────────────────────────────────────────────


def _reminder(rid="abc123", target=1, setter=1, guild=1, channel=1, due=9999.0, dm=True):
    return {
        "id": rid,
        "target_id": str(target),
        "set_by_id": str(setter),
        "guild_id": str(guild),
        "channel_id": str(channel),
        "message": "don't forget",
        "due": due,
        "duration": 3600.0,
        "dm": dm,
    }


async def test_reminder_id_exists():
    assert await db.reminder_id_exists("abc123") is False
    await db.set_reminder(_reminder())
    assert await db.reminder_id_exists("abc123") is True


async def test_set_and_get_all_reminders():
    await db.set_reminder(_reminder("r1"))
    reminders = await db.get_all_reminders()
    assert "r1" in reminders
    assert reminders["r1"]["message"] == "don't forget"
    assert reminders["r1"]["dm"] is True


async def test_remove_reminder():
    await db.set_reminder(_reminder("r2"))
    await db.remove_reminder("r2")
    assert "r2" not in await db.get_all_reminders()


async def test_get_user_reminders():
    await db.set_reminder(_reminder("r3", target=5))
    await db.set_reminder(_reminder("r4", target=6))
    user_reminders = await db.get_user_reminders(5)
    assert "r3" in user_reminders
    assert "r4" not in user_reminders


async def test_count_user_reminders():
    await db.set_reminder(_reminder("r5", target=7))
    await db.set_reminder(_reminder("r6", target=7))
    assert await db.count_user_reminders(7) == 2


async def test_get_sent_reminders():
    # setter=10, target=20 → shows up as "sent" for user 10
    await db.set_reminder(_reminder("rs1", target=20, setter=10))
    # setter=10, target=10 → self-reminder, should NOT appear
    await db.set_reminder(_reminder("rs2", target=10, setter=10))
    sent = await db.get_sent_reminders(10)
    assert "rs1" in sent
    assert "rs2" not in sent


async def test_reminder_dm_false():
    await db.set_reminder(_reminder("rdm", dm=False))
    r = (await db.get_all_reminders())["rdm"]
    assert r["dm"] is False


# ── Warnings ──────────────────────────────────────────────────────────────────


async def test_add_warning_returns_count():
    count = await db.add_warning(1, 2, "spam", "3", "Mod", "2024-01-01")
    assert count == 1
    count = await db.add_warning(1, 2, "rude", "3", "Mod", "2024-01-02")
    assert count == 2


async def test_get_warnings():
    await db.add_warning(1, 3, "reason A", "9", "Admin", "2024-01-01")
    warnings = await db.get_warnings(1, 3)
    assert len(warnings) == 1
    assert warnings[0]["reason"] == "reason A"
    assert warnings[0]["by_name"] == "Admin"


async def test_get_warning_count():
    await db.add_warning(1, 4, "x", "1", "A", "2024-01-01")
    assert await db.get_warning_count(1, 4) == 1


async def test_clear_warnings():
    await db.add_warning(1, 5, "x", "1", "A", "2024-01-01")
    await db.add_warning(1, 5, "y", "1", "A", "2024-01-01")
    deleted = await db.clear_warnings(1, 5)
    assert deleted == 2
    assert await db.get_warning_count(1, 5) == 0


async def test_get_warn_config_defaults():
    cfg = await db.get_warn_config(999)
    assert cfg == {"kick_at": 0, "ban_at": 0, "dm_user": True}


async def test_set_and_get_warn_config():
    await db.set_warn_config(1, kick_at=3, ban_at=5, dm_user=False)
    cfg = await db.get_warn_config(1)
    assert cfg["kick_at"] == 3
    assert cfg["ban_at"] == 5
    assert cfg["dm_user"] is False


# ── Welcome / Leave ───────────────────────────────────────────────────────────


async def test_get_welcome_config_missing():
    assert await db.get_welcome_config(1) is None


async def test_set_and_get_welcome_config():
    await db.set_welcome_config(1, enabled=True, channel_id="123", title="Welcome!")
    cfg = await db.get_welcome_config(1)
    assert cfg["enabled"] is True
    assert cfg["channel_id"] == "123"
    assert cfg["title"] == "Welcome!"


async def test_get_leave_config_missing():
    assert await db.get_leave_config(1) is None


async def test_set_and_get_leave_config():
    await db.set_leave_config(1, enabled=True, channel_id="456", content="Goodbye {user}")
    cfg = await db.get_leave_config(1)
    assert cfg["enabled"] is True
    assert cfg["content"] == "Goodbye {user}"


# ── Votes ─────────────────────────────────────────────────────────────────────


async def test_get_vote_missing():
    assert await db.get_vote(1, "topgg") is None


async def test_record_vote_first_time():
    result = await db.record_vote(1, "topgg")
    assert result["streak"] == 1
    assert result["user_id"] == "1"


async def test_record_vote_increments_streak_within_cooldown(monkeypatch):
    # First vote
    await db.record_vote(1, "topgg")
    # Second vote immediately after — within cooldown window
    result = await db.record_vote(1, "topgg")
    assert result["streak"] == 2


async def test_record_vote_resets_streak_after_cooldown(monkeypatch):
    now = time.time()
    # Plant a vote that is older than the 18h cooldown+grace
    old_time = now - (19 * 3600)
    await db._conn().execute(
        "INSERT INTO votes (user_id, site, voted_at, streak, notify) VALUES (?,?,?,?,?)",
        ("77", "topgg", old_time, 5, 1),
    )
    await db._conn().commit()
    result = await db.record_vote(77, "topgg")
    assert result["streak"] == 1


async def test_has_voted_recently_true():
    await db.record_vote(2, "dbl")
    assert await db.has_voted_recently(2, "dbl") is True


async def test_has_voted_recently_false():
    assert await db.has_voted_recently(999, "topgg") is False


async def test_set_vote_notify_and_get_all_for_notify():
    await db.record_vote(3, "topgg")
    await db.set_vote_notify(3, "topgg", False)
    notifies = await db.get_all_votes_for_notify()
    user_ids = [v["user_id"] for v in notifies]
    assert "3" not in user_ids


# ── Recurring reminders ───────────────────────────────────────────────────────


def _recurring(rid="rec1", target=1, setter=1, interval=86400.0, next_due=9999.0):
    return {
        "id": rid,
        "target_id": str(target),
        "set_by_id": str(setter),
        "guild_id": "1",
        "channel_id": "1",
        "message": "daily reminder",
        "interval": interval,
        "next_due": next_due,
        "dm": True,
        "paused": False,
        "fire_count": 0,
        "label": None,
    }


async def test_recurring_id_exists():
    assert await db.recurring_id_exists("rec1") is False
    await db.set_recurring(_recurring())
    assert await db.recurring_id_exists("rec1") is True


async def test_set_and_get_recurring():
    await db.set_recurring(_recurring("rec2"))
    r = await db.get_recurring("rec2")
    assert r["message"] == "daily reminder"
    assert r["dm"] is True
    assert r["paused"] is False


async def test_get_recurring_missing():
    assert await db.get_recurring("nope") is None


async def test_update_recurring():
    await db.set_recurring(_recurring("rec3"))
    await db.update_recurring({"id": "rec3", "next_due": 12345.0, "fire_count": 3, "paused": False})
    r = await db.get_recurring("rec3")
    assert r["next_due"] == 12345.0
    assert r["fire_count"] == 3


async def test_set_recurring_paused():
    await db.set_recurring(_recurring("rec4"))
    await db.set_recurring_paused("rec4", True)
    assert (await db.get_recurring("rec4"))["paused"] is True
    await db.set_recurring_paused("rec4", False)
    assert (await db.get_recurring("rec4"))["paused"] is False


async def test_remove_recurring():
    await db.set_recurring(_recurring("rec5"))
    await db.remove_recurring("rec5")
    assert await db.get_recurring("rec5") is None


async def test_get_user_recurring_ordered():
    await db.set_recurring(_recurring("rec6", target=10, next_due=2000.0))
    await db.set_recurring(_recurring("rec7", target=10, next_due=1000.0))
    results = await db.get_user_recurring(10)
    assert results[0]["id"] == "rec7"
    assert results[1]["id"] == "rec6"


async def test_count_user_recurring():
    await db.set_recurring(_recurring("rec8", target=20))
    await db.set_recurring(_recurring("rec9", target=20))
    assert await db.count_user_recurring(20) == 2


async def test_get_all_recurring():
    await db.set_recurring(_recurring("recA"))
    all_r = await db.get_all_recurring()
    assert "recA" in all_r


# ── Role Panels ───────────────────────────────────────────────────────────────


async def test_create_and_get_role_panel():
    await db.create_role_panel("p1", 1, "Colour Roles", None, "toggle")
    panel = await db.get_role_panel("p1")
    assert panel["title"] == "Colour Roles"
    assert panel["mode"] == "toggle"
    assert panel["entries"] == []


async def test_get_role_panel_missing():
    assert await db.get_role_panel("nope") is None


async def test_add_and_remove_role_from_panel():
    await db.create_role_panel("p2", 1, "Roles", None, "toggle")
    await db.add_role_to_panel("p2", {"role_id": 100, "label": "Red", "emoji": "🔴", "style": "danger"})
    panel = await db.get_role_panel("p2")
    assert len(panel["entries"]) == 1
    assert panel["entries"][0]["label"] == "Red"

    await db.remove_role_from_panel("p2", 100)
    panel = await db.get_role_panel("p2")
    assert panel["entries"] == []


async def test_role_panel_entries_ordered_by_position():
    await db.create_role_panel("p3", 1, "Test", None, "toggle")
    await db.add_role_to_panel("p3", {"role_id": 1, "label": "First"})
    await db.add_role_to_panel("p3", {"role_id": 2, "label": "Second"})
    panel = await db.get_role_panel("p3")
    labels = [e["label"] for e in panel["entries"]]
    assert labels == ["First", "Second"]


async def test_edit_role_panel():
    await db.create_role_panel("p4", 1, "Old Title", None, "toggle")
    await db.edit_role_panel("p4", "New Title", "desc", "single")
    panel = await db.get_role_panel("p4")
    assert panel["title"] == "New Title"
    assert panel["description"] == "desc"
    assert panel["mode"] == "single"


async def test_update_role_panel_message():
    await db.create_role_panel("p5", 1, "T", None, "toggle")
    await db.update_role_panel_message("p5", 999, 888)
    panel = await db.get_role_panel("p5")
    assert panel["channel_id"] == "999"
    assert panel["message_id"] == "888"


async def test_delete_role_panel_cascades_entries():
    await db.create_role_panel("p6", 1, "T", None, "toggle")
    await db.add_role_to_panel("p6", {"role_id": 50, "label": "Gone"})
    await db.delete_role_panel("p6")
    assert await db.get_role_panel("p6") is None


async def test_get_role_panels_for_guild():
    await db.create_role_panel("pg1", 5, "A", None, "toggle")
    await db.create_role_panel("pg2", 5, "B", None, "toggle")
    await db.create_role_panel("pg3", 6, "C", None, "toggle")
    panels = await db.get_role_panels_for_guild(5)
    ids = [p["id"] for p in panels]
    assert "pg1" in ids and "pg2" in ids and "pg3" not in ids


# ── Audit log ─────────────────────────────────────────────────────────────────


async def test_get_auditlog_config_missing():
    assert await db.get_auditlog_config(1) is None


async def test_set_auditlog_channel_creates_row():
    await db.set_auditlog_channel(1, 555)
    cfg = await db.get_auditlog_config(1)
    assert cfg is not None
    assert cfg["channel_id"] == "555"


async def test_set_auditlog_enabled():
    await db.set_auditlog_enabled(1, True)
    cfg = await db.get_auditlog_config(1)
    assert cfg["enabled"] is True
    await db.set_auditlog_enabled(1, False)
    assert (await db.get_auditlog_config(1))["enabled"] is False


async def test_set_auditlog_events():
    await db.set_auditlog_channel(1, 1)
    await db.set_auditlog_events(1, {"msg_delete", "msg_edit"})
    cfg = await db.get_auditlog_config(1)
    assert set(cfg["events"]) == {"msg_delete", "msg_edit"}


# ── AutoMod ───────────────────────────────────────────────────────────────────


async def test_get_automod_config_missing():
    assert await db.get_automod_config(1) is None


async def test_set_automod_enabled_creates_row():
    await db.set_automod_enabled(1, True)
    cfg = await db.get_automod_config(1)
    assert cfg is not None
    assert cfg["enabled"] is True


async def test_set_automod_rule_merges():
    await db.set_automod_rule(1, "spam", enabled=True, count=5, seconds=5)
    cfg = await db.get_automod_config(1)
    rule = cfg["rules"]["spam"]
    assert rule["enabled"] is True
    assert rule["count"] == 5
    # Update only one field — others preserved
    await db.set_automod_rule(1, "spam", count=10)
    cfg = await db.get_automod_config(1)
    rule = cfg["rules"]["spam"]
    assert rule["count"] == 10
    assert rule["enabled"] is True


async def test_add_and_remove_automod_badword():
    assert await db.add_automod_badword(1, "badword") is True
    assert await db.add_automod_badword(1, "badword") is False
    assert "badword" in await db.get_automod_badwords(1)
    assert await db.remove_automod_badword(1, "badword") is True
    assert await db.remove_automod_badword(1, "badword") is False


async def test_get_automod_badwords_sorted():
    await db.add_automod_badword(1, "zebra")
    await db.add_automod_badword(1, "apple")
    words = await db.get_automod_badwords(1)
    assert words == sorted(words)


async def test_toggle_automod_ignore_channel():
    await db.set_automod_enabled(1, False)
    result = await db.toggle_automod_ignore(1, "channel", 300)
    assert result == "added"
    result = await db.toggle_automod_ignore(1, "channel", 300)
    assert result == "removed"


async def test_add_remove_automod_regex():
    assert await db.add_automod_regex(1, r"\bdiscord\.gg\b", "invite links") is True
    assert await db.add_automod_regex(1, r"\bdiscord\.gg\b") is False
    patterns = await db.get_automod_regex_patterns(1)
    assert len(patterns) == 1
    assert patterns[0]["pattern"] == r"\bdiscord\.gg\b"
    assert await db.remove_automod_regex(1, r"\bdiscord\.gg\b") is True
    assert await db.get_automod_regex_patterns(1) == []


async def test_add_remove_automod_attachment_words():
    assert await db.add_automod_attachment_word(1, "invoice") is True
    assert await db.add_automod_attachment_word(1, "invoice") is False
    words = await db.get_automod_attachment_words(1)
    assert "invoice" in words
    assert await db.remove_automod_attachment_word(1, "invoice") is True
    assert await db.get_automod_attachment_words(1) == []
