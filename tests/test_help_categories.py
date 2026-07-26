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
    _collect_categories,
)

HERE = os.path.dirname(__file__)
COGS_DIR = os.path.join(HERE, "..", "cogs")

# Categories the engine assigns itself rather than reading from extras.
_ENGINE_ASSIGNED = {"📦 Uncategorized"}


def _declared_categories() -> dict[str, set[str]]:
    """Map every category string found in a cog's extras to the files using it."""
    found: dict[str, set[str]] = {}

    for root, _dirs, files in os.walk(COGS_DIR):
        for fname in files:
            if not fname.endswith(".py"):
                continue
            path = os.path.join(root, fname)
            rel = os.path.relpath(path, COGS_DIR).replace(os.sep, "/")
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)

            for node in ast.walk(tree):
                if not isinstance(node, ast.Dict):
                    continue
                for key, value in zip(node.keys, node.values):
                    if not isinstance(key, ast.Constant) or key.value != "category":
                        continue
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        found.setdefault(value.value, set()).add(rel)

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
