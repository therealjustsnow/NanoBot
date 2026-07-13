"""
tests/test_invite_permissions.py
Static analysis: every Discord permission the bot checks on *itself*
(`guild.me` / a local `me` bound to it) at runtime must also be requested by
the `/invite` command's `discord.Permissions(...)` — otherwise a fresh invite
under-provisions the bot and a feature silently breaks in a brand-new server.

This does not catch every possible pattern (only the `X = Y.me` /
`Z.permissions_for(<bot-member>)` / `<bot-member>.guild_permissions.attr`
shapes actually used in this codebase), but it pins the invariant so a new
bot-permission check added without updating `/invite` fails CI instead of
shipping silently.
"""

import ast
import os

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
SCAN_DIRS = ["cogs", "utils"]
SCAN_FILES = ["main.py"]

# discord.py permission flags that alias the same bit under a different name.
# Map every alias to its canonical form so comparisons don't false-positive.
ALIASES = {
    "read_messages": "view_channel",
}


def _canon(name: str) -> str:
    return ALIASES.get(name, name)


def _is_bot_member_expr(node: ast.AST, bot_names: set[str]) -> bool:
    """True for `<anything>.me` or a Name already known to hold the bot's member."""
    if isinstance(node, ast.Attribute) and node.attr == "me":
        return True
    if isinstance(node, ast.Name) and node.id in bot_names:
        return True
    return False


def _is_permissions_for_call(node: ast.AST, bot_names: set[str]) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "permissions_for"
        and len(node.args) >= 1
        and _is_bot_member_expr(node.args[0], bot_names)
    )


def _scan_function(func: ast.AST) -> set[tuple[str, int]]:
    """Return {(perm_name, lineno), ...} the bot checks on itself in one function."""
    bot_names: set[str] = set()
    perms_names: set[str] = set()

    for node in ast.walk(func):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            if isinstance(node.value, ast.Attribute) and node.value.attr == "me":
                bot_names.add(target.id)

    for node in ast.walk(func):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            if _is_permissions_for_call(node.value, bot_names):
                perms_names.add(target.id)

    found: set[tuple[str, int]] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Attribute):
            continue
        base = node.value
        # perms.<attr>  (perms bound from X.permissions_for(<bot-member>))
        if isinstance(base, ast.Name) and base.id in perms_names:
            found.add((_canon(node.attr), node.lineno))
            continue
        # <expr>.permissions_for(<bot-member>).<attr>  (no intermediate variable)
        if _is_permissions_for_call(base, bot_names):
            found.add((_canon(node.attr), node.lineno))
            continue
        # <bot-member>.guild_permissions.<attr>
        if (
            isinstance(base, ast.Attribute)
            and base.attr == "guild_permissions"
            and _is_bot_member_expr(base.value, bot_names)
        ):
            found.add((_canon(node.attr), node.lineno))

    return found


def _scan_file(path: str) -> dict[str, list[str]]:
    """Map required permission name -> ["path:lineno", ...] for one file."""
    src = open(path).read()
    tree = ast.parse(src, filename=path)
    rel = os.path.relpath(path, REPO_ROOT)
    out: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for perm, lineno in _scan_function(node):
                out.setdefault(perm, []).append(f"{rel}:{lineno}")
    return out


def _iter_py_files():
    for f in SCAN_FILES:
        yield os.path.join(REPO_ROOT, f)
    for d in SCAN_DIRS:
        base = os.path.join(REPO_ROOT, d)
        for root, _dirs, files in os.walk(base):
            for fname in sorted(files):
                if fname.endswith(".py"):
                    yield os.path.join(root, fname)


def _required_bot_permissions() -> dict[str, list[str]]:
    """Map required permission name -> [locations] across the whole codebase."""
    required: dict[str, list[str]] = {}
    for path in _iter_py_files():
        for perm, locs in _scan_file(path).items():
            required.setdefault(perm, []).extend(locs)
    return required


def _invite_permissions() -> set[str]:
    """Parse the True-valued kwargs passed to discord.Permissions(...) in pfx_invite."""
    path = os.path.join(REPO_ROOT, "cogs", "utility", "cog.py")
    src = open(path).read()
    tree = ast.parse(src, filename=path)

    invite_func = None
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "pfx_invite"
        ):
            invite_func = node
            break
    assert invite_func is not None, "cogs/utility/cog.py: pfx_invite() not found"

    perms: set[str] = set()
    for node in ast.walk(invite_func):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "Permissions"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "discord"
        ):
            for kw in node.keywords:
                if kw.arg is not None and isinstance(kw.value, ast.Constant):
                    if kw.value.value is True:
                        perms.add(_canon(kw.arg))
            break
    return perms


def test_invite_requests_every_permission_the_bot_checks_on_itself():
    required = _required_bot_permissions()
    invited = _invite_permissions()

    missing = {perm: locs for perm, locs in required.items() if perm not in invited}
    assert not missing, (
        "The /invite command (cogs/utility/cog.py: pfx_invite) is missing "
        "permission(s) the bot checks on itself elsewhere — a fresh invite "
        "would under-provision the bot:\n"
        + "\n".join(
            f"  {perm!r} checked at: {', '.join(locs)}"
            for perm, locs in sorted(missing.items())
        )
    )
