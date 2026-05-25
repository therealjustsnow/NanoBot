"""
tests/test_docs_freshness.py
Static guards that stop documentation from drifting away from the code.

These run via AST only (no imports, no Discord) so they're fast and safe:
  - Every top-level command name is mentioned in its cog's module docstring.
  - The AutoMod /help entry covers every rule and action the cog defines.

When a guard fails, the fix is to update the docs — not to weaken the guard.
"""

import ast
import os
import re

HERE = os.path.dirname(__file__)
COGS_DIR = os.path.join(HERE, "..", "cogs")

# Cogs whose top-level commands are generated dynamically at runtime, so they
# are intentionally not enumerated by name in the module docstring.
_DOCSTRING_EXEMPT = {"fun.py"}

_CMD_DECORATORS = {"command", "group", "hybrid_command", "hybrid_group"}


def _module_docstring_tokens(tree: ast.Module) -> set[str]:
    doc = ast.get_docstring(tree) or ""
    return set(re.findall(r"[a-z0-9_]+", doc.lower()))


def _top_level_command_names(tree: ast.Module) -> list[str]:
    """Names of commands registered directly on `commands` (not group subcommands)."""
    names = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            func = dec.func
            if not (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "commands"
                and func.attr in _CMD_DECORATORS
            ):
                continue
            name = node.name
            for kw in dec.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    name = kw.value.value
            names.append(name)
    return names


def test_every_command_is_named_in_its_cog_docstring():
    """A new command must be added to the cog's module docstring too."""
    failures = []
    for fname in sorted(os.listdir(COGS_DIR)):
        if not fname.endswith(".py") or fname in {"__init__.py"}:
            continue
        if fname in _DOCSTRING_EXEMPT:
            continue
        tree = ast.parse(open(os.path.join(COGS_DIR, fname)).read(), filename=fname)
        tokens = _module_docstring_tokens(tree)
        for name in _top_level_command_names(tree):
            if name.lower() not in tokens:
                failures.append(f"  {fname}: command {name!r} not in module docstring")
    assert not failures, "Commands missing from their cog docstring:\n" + "\n".join(
        failures
    )


def _dict_string_keys(tree: ast.Module, var_name: str) -> set[str]:
    """Return the string keys of a module-level `var_name = {...}` literal."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if var_name in targets and isinstance(node.value, ast.Dict):
                return {
                    k.value
                    for k in node.value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)
                }
        if isinstance(node, ast.AnnAssign):
            tgt = node.target
            if (
                isinstance(tgt, ast.Name)
                and tgt.id == var_name
                and isinstance(node.value, ast.Dict)
            ):
                return {
                    k.value
                    for k in node.value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)
                }
    raise AssertionError(f"{var_name} dict literal not found")


def test_automod_help_covers_all_rules_and_actions():
    """The /help AutoMod entry must mention every rule and action the cog enforces."""
    automod = ast.parse(open(os.path.join(COGS_DIR, "automod.py")).read())
    rules = _dict_string_keys(automod, "RULE_LABELS")
    actions = _dict_string_keys(automod, "ACTION_LABELS")

    help_text = open(os.path.join(COGS_DIR, "utility.py")).read().lower()
    # The automod entry lives in _SLASH_GROUPS; checking the whole file is enough
    # to catch a rule/action that was added to automod but never surfaced in help.
    missing_rules = [
        r
        for r in rules
        if r.replace("attachment_word", "word+attachment") not in help_text
    ]
    missing_actions = [a for a in actions if a not in help_text]
    assert not missing_rules, f"AutoMod rules absent from /help: {missing_rules}"
    assert not missing_actions, f"AutoMod actions absent from /help: {missing_actions}"
