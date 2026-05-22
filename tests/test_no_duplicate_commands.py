"""
tests/test_no_duplicate_commands.py
Static analysis: no two cogs may register the same top-level command name or alias.

Only @commands.command() and @commands.group() decorators are checked — subcommands
registered via @somegroup.command() live inside a group namespace and cannot conflict.
"""

import ast
import os
from collections import defaultdict

COGS_DIR = os.path.join(os.path.dirname(__file__), "..", "cogs")


def _top_level_commands(filepath: str) -> list[tuple[str, list[str]]]:
    """Return [(name, [aliases, ...]), ...] for every top-level command in a cog file."""
    src = open(filepath).read()
    tree = ast.parse(src, filename=filepath)
    results = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            func = dec.func
            # Must be `commands.command(...)` or `commands.group(...)`
            if not (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "commands"
                and func.attr in ("command", "group")
            ):
                continue
            name = node.name  # default to function name
            aliases: list[str] = []
            for kw in dec.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    name = kw.value.value
                if kw.arg == "aliases" and isinstance(kw.value, ast.List):
                    aliases = [
                        e.value for e in kw.value.elts if isinstance(e, ast.Constant)
                    ]
            results.append((name, aliases))

    return results


def _collect_all() -> dict[str, list[str]]:
    """Map every command token (name or alias) → [cog filenames that define it]."""
    token_to_cogs: dict[str, list[str]] = defaultdict(list)
    for fname in sorted(os.listdir(COGS_DIR)):
        if not fname.endswith(".py"):
            continue
        path = os.path.join(COGS_DIR, fname)
        for name, aliases in _top_level_commands(path):
            token_to_cogs[name].append(fname)
            for alias in aliases:
                token_to_cogs[alias].append(fname)
    return token_to_cogs


def test_no_duplicate_top_level_command_names():
    """Every top-level command name is unique across all cogs."""
    duplicates = {
        token: cogs for token, cogs in _collect_all().items() if len(cogs) > 1
    }
    assert not duplicates, (
        "Duplicate top-level command names / aliases found — discord.py will raise "
        "CommandRegistrationError at startup:\n"
        + "\n".join(
            f"  {token!r}: {cogs}" for token, cogs in sorted(duplicates.items())
        )
    )
