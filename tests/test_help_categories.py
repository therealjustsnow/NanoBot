"""
tests/test_help_categories.py
Static guards that keep the /help category index reachable.

Every command declares its help category as a plain string in its decorator's
`extras={...}`, but the help engine keeps two *separate* registries keyed by
that string:

  * `_CATEGORY_ORDER`   — where the category sorts in the paginated view
  * `_CATEGORY_ALIASES` — the keywords `!help <category>` accepts

Nothing links a cog's category string to either one, so adding a cog with a
new category silently produces a page that sorts last and that
`!help <category>` answers with "No command or category named ...". That is
exactly how the whole 🪙 Economy family (8 cogs) became unreachable.

These guards run via AST only (no imports, no Discord), so they're fast and
safe. When one fails, the fix is to register the category — not to weaken the
guard.
"""

import ast
import os

from cogs.utility.help_engine import (
    _CATEGORY_ALIASES,
    _CATEGORY_ORDER,
    _OWNER_CATEGORIES,
    _SLASH_GROUPS,
    _SUBCATEGORY_ORDER,
    _UNGROUPED,
    _build_category_embed,
    _build_help_pages,
    _collect_categories,
    _fit_fields,
    _group_by_sub,
)

HERE = os.path.dirname(__file__)
COGS_DIR = os.path.join(HERE, "..", "cogs")

# Categories the engine assigns itself rather than reading from extras.
_ENGINE_ASSIGNED = {"📦 Uncategorized"}


def _declared_entries() -> list[tuple[str, str, str, str]]:
    """
    Every command's declared help metadata, as (category, sub, name, file).

    Read straight off the `extras={...}` dict in each command decorator, so
    this sees exactly what the help engine will see at runtime.
    """
    entries: list[tuple[str, str, str, str]] = []

    for root, _dirs, files in os.walk(COGS_DIR):
        for fname in files:
            if not fname.endswith(".py"):
                continue
            path = os.path.join(root, fname)
            rel = os.path.relpath(path, COGS_DIR).replace(os.sep, "/")
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)

            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for dec in node.decorator_list:
                    if not isinstance(dec, ast.Call):
                        continue
                    kwargs = {k.arg: k.value for k in dec.keywords if k.arg}
                    extras = kwargs.get("extras")
                    if not isinstance(extras, ast.Dict):
                        continue

                    flat = {
                        key.value: value.value
                        for key, value in zip(extras.keys, extras.values)
                        if isinstance(key, ast.Constant)
                        and isinstance(value, ast.Constant)
                    }
                    cat = flat.get("category")
                    if not isinstance(cat, str):
                        continue

                    name_node = kwargs.get("name")
                    cmd_name = (
                        name_node.value
                        if isinstance(name_node, ast.Constant)
                        else node.name
                    )
                    entries.append((cat, flat.get("sub") or "", cmd_name, rel))

    return entries


def _declared_categories() -> dict[str, set[str]]:
    """Map every category string found in a cog's extras to the files using it."""
    found: dict[str, set[str]] = {}
    for cat, _sub, _name, rel in _declared_entries():
        found.setdefault(cat, set()).add(rel)
    return found


def test_every_declared_category_is_ordered():
    """A category used by a cog must have a slot in _CATEGORY_ORDER."""
    missing = {
        cat: sorted(files)
        for cat, files in _declared_categories().items()
        if cat not in _ENGINE_ASSIGNED and cat not in _CATEGORY_ORDER
    }
    assert not missing, (
        "categories used by cogs but absent from _CATEGORY_ORDER "
        f"(they sort last in /help): {missing}"
    )


def test_every_declared_category_has_an_alias():
    """`!help <keyword>` must reach every category a cog declares."""
    aliased = set(_CATEGORY_ALIASES.values())
    missing = {
        cat: sorted(files)
        for cat, files in _declared_categories().items()
        if cat not in aliased
    }
    assert not missing, (
        "categories with no _CATEGORY_ALIASES keyword "
        f"(!help <category> can't find them): {missing}"
    )


def test_slash_group_categories_are_registered():
    """The static _SLASH_GROUPS entries need the same two registrations."""
    aliased = set(_CATEGORY_ALIASES.values())
    for sg in _SLASH_GROUPS:
        cat = sg["category"]
        assert cat in _CATEGORY_ORDER, f"{sg['name']}: {cat} missing from order"
        assert cat in aliased, f"{sg['name']}: {cat} has no alias keyword"


def test_alias_targets_are_real_categories():
    """No alias may point at a category name that doesn't exist."""
    real = set(_CATEGORY_ORDER) | _ENGINE_ASSIGNED
    bad = {kw: cat for kw, cat in _CATEGORY_ALIASES.items() if cat not in real}
    assert not bad, f"aliases pointing at unknown categories: {bad}"


def test_economy_category_is_reachable():
    """Regression: the reported bug — !help economy found nothing."""
    assert _CATEGORY_ALIASES.get("economy") == "🪙 Economy"
    assert "🪙 Economy" in _CATEGORY_ORDER
    # The family's other entry points resolve to the same page.
    for kw in ("fishing", "casino", "inventory", "crafting", "shop", "work"):
        assert _CATEGORY_ALIASES.get(kw) == "🪙 Economy", kw


class _FakeBot:
    """Minimal stand-in: _collect_categories only reads bot.commands."""

    def __init__(self, commands):
        self.commands = commands


class _FakeCommand:
    def __init__(self, name, category):
        self.name = name
        self.aliases = []
        self.description = "desc"
        self.extras = {"category": category}
        self.cog = None


def test_unknown_categories_sort_before_owner_admin():
    """A brand-new category lands before the Owner/Admin tail, not after it."""
    bot = _FakeBot(
        [
            _FakeCommand("newthing", "🆕 Brand New"),
            _FakeCommand("balance", "🪙 Economy"),
            _FakeCommand("reload", "🔧 Owner / Admin"),
        ]
    )
    order = list(_collect_categories(bot, is_owner=True))

    assert order[-1] in _OWNER_CATEGORIES
    assert order.index("🆕 Brand New") < order.index("🔧 Owner / Admin")
    assert order.index("🪙 Economy") < order.index("🆕 Brand New")


def test_owner_categories_hidden_from_non_owners():
    bot = _FakeBot(
        [
            _FakeCommand("balance", "🪙 Economy"),
            _FakeCommand("reload", "🔧 Owner / Admin"),
        ]
    )
    assert "🔧 Owner / Admin" not in _collect_categories(bot, is_owner=False)
    assert "🔧 Owner / Admin" in _collect_categories(bot, is_owner=True)


# ── Subcategories ─────────────────────────────────────────────────────────────
# A category listed in _SUBCATEGORY_ORDER is opted in to grouped rendering.
# These guards keep the opt-in complete in both directions: no command falls
# out of a group, and no group is declared that nothing uses.


def test_subcategorized_commands_all_declare_a_sub():
    """Every command in an opted-in category names one of its groups."""
    missing = [
        f"{rel}::{name} ({cat})"
        for cat, sub, name, rel in _declared_entries()
        if cat in _SUBCATEGORY_ORDER and not sub
    ]
    assert not missing, (
        "commands in a subcategorized category with no extras['sub'] "
        f"(they'd fall into '{_UNGROUPED}'): {missing}"
    )


def test_declared_subs_are_registered_groups():
    """A sub must match a group name in its category's _SUBCATEGORY_ORDER."""
    bad = [
        f"{rel}::{name} declares {sub!r} (not a group of {cat})"
        for cat, sub, name, rel in _declared_entries()
        if sub and sub not in _SUBCATEGORY_ORDER.get(cat, [])
    ]
    assert not bad, bad


def test_no_orphan_subs_outside_subcategorized_categories():
    """A sub on a category that isn't opted in would render nowhere."""
    orphans = [
        f"{rel}::{name} ({cat} is not in _SUBCATEGORY_ORDER)"
        for cat, sub, name, rel in _declared_entries()
        if sub and cat not in _SUBCATEGORY_ORDER
    ]
    assert not orphans, orphans


def test_every_declared_group_is_used():
    """A group nobody uses is dead config — and renders as nothing."""
    used = {(cat, sub) for cat, sub, _n, _r in _declared_entries() if sub}
    empty = [
        f"{cat} → {group}"
        for cat, groups in _SUBCATEGORY_ORDER.items()
        for group in groups
        if (cat, group) not in used
    ]
    assert not empty, f"groups in _SUBCATEGORY_ORDER with no commands: {empty}"


def test_subcategorized_categories_are_ordered_and_aliased():
    """Opting a category in doesn't exempt it from the category registries."""
    aliased = set(_CATEGORY_ALIASES.values())
    for cat in _SUBCATEGORY_ORDER:
        assert cat in _CATEGORY_ORDER, f"{cat} missing from _CATEGORY_ORDER"
        assert cat in aliased, f"{cat} has no alias keyword"


def _grouped_bot():
    """A bot whose Music/Economy commands carry real group names."""
    cmds = []
    for cat, groups in _SUBCATEGORY_ORDER.items():
        for i, group in enumerate(groups):
            for j in range(3):
                c = _FakeCommand(f"{cat[0]}cmd{i}{j}", cat)
                c.extras["sub"] = group
                cmds.append(c)
    return _FakeBot(cmds)


def test_group_by_sub_is_flat_for_unregistered_categories():
    """A category without groups returns [] — the caller's flat-list signal."""
    assert _group_by_sub("🏷️ Tags", [{"name": "tag", "sub": ""}]) == []


def test_group_by_sub_loses_no_commands():
    cat = "🎵 Music"
    cmds = _collect_categories(_grouped_bot(), is_owner=True)[cat]
    grouped = _group_by_sub(cat, cmds)

    assert [g for g, _ in grouped] == _SUBCATEGORY_ORDER[cat], "group order"
    regrouped = [c["name"] for _g, entries in grouped for c in entries]
    assert sorted(regrouped) == sorted(c["name"] for c in cmds)


def test_group_by_sub_buckets_unknown_subs_last():
    """An unrecognised sub still renders, in a trailing catch-all group."""
    cmds = [
        {"name": "b", "sub": "🔊 Sound"},
        {"name": "a", "sub": "🌈 Not A Real Group"},
    ]
    grouped = _group_by_sub("🎵 Music", cmds)
    assert grouped[-1][0] == _UNGROUPED
    assert [c["name"] for c in grouped[-1][1]] == ["a"]


def test_group_by_sub_drops_empty_groups():
    """A group with nothing visible leaves no bare heading."""
    grouped = _group_by_sub("🎵 Music", [{"name": "b", "sub": "🔊 Sound"}])
    assert [g for g, _ in grouped] == ["🔊 Sound"]


def test_fit_fields_splits_at_the_field_cap():
    """A group too big for one field continues into another, losing nothing."""
    import discord

    e = discord.Embed()
    lines = [f"`line {i:03d}` — " + "x" * 60 for i in range(40)]
    _fit_fields(e, "🔊 Sound", lines)

    assert len(e.fields) > 1, "expected a continuation field"
    assert all(len(f.value) <= 1024 for f in e.fields)
    assert e.fields[0].name == "🔊 Sound"
    assert all("cont." in f.name for f in e.fields[1:])
    rejoined = "\n".join(f.value for f in e.fields).splitlines()
    assert rejoined == lines


def test_fit_fields_keeps_one_field_when_it_fits():
    import discord

    e = discord.Embed()
    _fit_fields(e, "🔊 Sound", ["`n!volume` — Show or set playback volume"])
    assert len(e.fields) == 1


# ── Rendered embeds stay inside Discord's limits ──────────────────────────────


def _assert_embed_within_limits(e, label):
    assert len(e) <= 6000, f"{label}: embed is {len(e)} chars (cap 6000)"
    assert len(e.fields) <= 25, f"{label}: {len(e.fields)} fields (cap 25)"
    assert len(e.description or "") <= 4096, f"{label}: description too long"
    for f in e.fields:
        assert len(f.value) <= 1024, f"{label}: field {f.name!r} over 1024"
        assert len(f.name) <= 256, f"{label}: field name {f.name!r} over 256"


def test_category_embeds_stay_within_discord_limits():
    """Music is the stress case — 40 commands across 7 groups on one embed."""
    bot = _grouped_bot()
    cats = _collect_categories(bot, is_owner=True)
    for cat, cmds in cats.items():
        _assert_embed_within_limits(
            _build_category_embed(cat, cmds, "n!"), f"category {cat}"
        )


def test_help_pages_stay_within_discord_limits():
    pages = _build_help_pages(_grouped_bot(), "n!", "NanoBot", is_owner=True)
    for i, page in enumerate(pages):
        _assert_embed_within_limits(page, f"page {i + 1}")


def test_grouped_category_embed_renders_groups_as_fields():
    bot = _grouped_bot()
    cmds = _collect_categories(bot, is_owner=True)["🪙 Economy"]
    e = _build_category_embed("🪙 Economy", cmds, "n!")

    names = [f.name for f in e.fields]
    assert names == _SUBCATEGORY_ORDER["🪙 Economy"]
    # every command still appears exactly once
    body = "\n".join(f.value for f in e.fields)
    for c in cmds:
        assert body.count(f"`n!{c['name']}`") == 1, c["name"]


def test_flat_category_embed_still_uses_the_description():
    cmds = _collect_categories(
        _FakeBot([_FakeCommand("tag", "🏷️ Tags")]), is_owner=True
    )["🏷️ Tags"]
    e = _build_category_embed("🏷️ Tags", cmds, "n!")
    assert not e.fields
    assert "`n!tag`" in e.description
