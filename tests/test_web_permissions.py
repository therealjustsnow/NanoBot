"""
Tests for web/permissions.py — who may configure what, and which permissions
each feature needs.

The gate has to be the same one every `/…` admin command uses, so the tests here
are mostly about *not* being clever: Administrator implies everything, Manage
Server is the bar, and nothing about being a dashboard user grants anything.
"""

import pytest

from web import permissions as perms

ADMIN = perms.ADMINISTRATOR
MANAGE = perms.MANAGE_GUILD
BAN = perms.BAN_MEMBERS


# ── has_perm ──────────────────────────────────────────────────────────────────
def test_administrator_implies_everything():
    """Discord's own rule. A dashboard that ignored it would refuse the one
    person who can never actually be refused."""
    assert perms.has_perm(ADMIN, BAN)
    assert perms.has_perm(ADMIN, MANAGE)
    assert perms.has_perm(ADMIN, perms.MODERATE_MEMBERS)


def test_specific_bit_does_not_imply_others():
    assert perms.has_perm(BAN, BAN)
    assert not perms.has_perm(BAN, MANAGE)


@pytest.mark.parametrize("value", [None, "", "abc", [], {}])
def test_garbage_permissions_grant_nothing(value):
    assert not perms.has_perm(value, MANAGE)


def test_permissions_as_a_string_of_digits_still_work():
    """Discord's OAuth guild list sends `permissions` as a string."""
    assert perms.has_perm(str(MANAGE), MANAGE)


# ── can_manage ────────────────────────────────────────────────────────────────
def test_can_manage_needs_manage_guild():
    assert perms.can_manage(MANAGE)
    assert perms.can_manage(ADMIN)
    assert not perms.can_manage(BAN)
    assert not perms.can_manage(0)


def test_guild_owner_can_always_manage():
    assert perms.can_manage(0, is_owner=True)


# ── guild_summary ─────────────────────────────────────────────────────────────
def _guild(gid="1", **kwargs):
    return {"id": gid, "name": "Test", "icon": None, "permissions": 0, **kwargs}


def test_summary_separates_managed_from_present():
    """Two different questions, two different answers.

    Collapsing them into one boolean is what makes a dashboard look like it has
    lost your server: one you manage but the bot isn't in should offer an
    invite, not disappear.
    """
    row = perms.guild_summary(_guild(permissions=MANAGE), bot_guild_ids=[])
    assert row["managed"] is True
    assert row["present"] is False

    row = perms.guild_summary(_guild(permissions=0), bot_guild_ids=[1])
    assert row["managed"] is False
    assert row["present"] is True


def test_summary_survives_a_bad_id():
    row = perms.guild_summary(_guild(gid="not-a-number"), bot_guild_ids=[1])
    assert row["present"] is False


def test_bot_owner_manages_everything():
    row = perms.guild_summary(_guild(permissions=0), bot_guild_ids=[1], bot_owner=True)
    assert row["managed"] is True


# ── manageable_guilds ─────────────────────────────────────────────────────────
def test_sort_order_puts_actionable_servers_first():
    """Manageable+present, then manageable, then the rest — each alphabetical.

    This ordering is the picker's whole ergonomics on a phone.
    """
    raw = [
        {"id": "1", "name": "Zeta", "permissions": 0},  # neither
        {"id": "2", "name": "Alpha", "permissions": MANAGE},  # manage, not present
        {"id": "3", "name": "Beta", "permissions": MANAGE},  # manage + present
        {"id": "4", "name": "Aardvark", "permissions": 0},  # member only
    ]
    rows = perms.manageable_guilds(raw, bot_guild_ids=[3, 4, 1])
    assert [row["name"] for row in rows] == ["Beta", "Alpha", "Aardvark", "Zeta"]


def test_empty_list_is_empty_not_an_error():
    assert perms.manageable_guilds([], []) == []


# ── the permission map ────────────────────────────────────────────────────────
def test_every_feature_declares_real_permission_bits():
    """A typo'd bit would render as "Permission 8796093022208" in the checklist."""
    for key, spec in perms.FEATURE_PERMISSIONS.items():
        assert spec["required"], f"{key} requires nothing — is that right?"
        assert spec["label"] and spec["why"]
        for bit in spec["required"] + spec["optional"]:
            assert bit in perms.PERMISSION_LABELS, f"{key} names an unlabelled bit"


def test_required_and_optional_never_overlap():
    for key, spec in perms.FEATURE_PERMISSIONS.items():
        assert not (set(spec["required"]) & set(spec["optional"])), key


def test_report_flags_what_is_missing():
    report = perms.permission_report("automod", 0)
    assert report["ok"] is False
    assert "Manage Messages" in report["missing"]

    report = perms.permission_report("automod", perms.MANAGE_MESSAGES)
    assert report["ok"] is True
    assert report["missing"] == []


def test_report_separates_degraded_from_broken():
    """An optional permission missing means one capability is off, not that the
    feature is broken — the checklist has to say which."""
    report = perms.permission_report(
        "auditlog", perms.SEND_MESSAGES | perms.EMBED_LINKS
    )
    assert report["ok"] is True
    assert report["degraded"] == ["View Audit Log"]


def test_administrator_satisfies_every_feature():
    for report in perms.permission_reports(perms.ADMINISTRATOR):
        assert report["ok"], report["feature"]
        assert not report["degraded"], report["feature"]


def test_unknown_feature_is_none_not_a_pass():
    """A typo'd feature key must not read as "all good"."""
    assert perms.permission_report("nope", perms.ADMINISTRATOR) is None


def test_reports_put_problems_first():
    reports = perms.permission_reports(0)
    assert reports[0]["ok"] is False
    # Everything is broken at zero permissions, which is itself worth asserting.
    assert all(not report["ok"] for report in reports)
