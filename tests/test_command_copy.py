"""
tests/test_command_copy.py
Every command name the bot *prints* has to be one a member can actually run.

The failure this exists for is quiet and specific. Discord has no gesture for
invoking a command group — `/adventure` is a folder in the picker, not a
command — so a group's bare form is prefix-only unless it declares a
`fallback`, and the fallback registers under the subcommand's name. Meanwhile
`n!adventure` works fine locally, so copy that says "run `/adventure`" reads as
correct to whoever wrote it and is a dead end for every slash user who follows
it. The same trap catches `/mine` (dig lives at `/mine dig`), `/inventory`,
`/craft`, `/shop`, `/fish` and `/profile`, and it caught all of them at once:
the /adventure dashboard's own headline was offering `/hunt` and `/explore`,
neither of which has ever been a command.

So this walks the real app-command tree and checks every ``/name`` in backticks
that the cogs put in a *string* — embeds, error copy, footers, button replies.
Docstrings are skipped on purpose: they are where the design is explained, and
explaining that "the bare `/fish` casts" is exactly the sentence this test
would otherwise ban.

Two ways to pass, matching the two ways a member can type a command:

  * some prefix of the words names a runnable slash command — a prefix rather
    than the whole string because copy quotes arguments too (`/inventory sell
    all` is `/inventory sell` plus a target);
  * or it names a prefix-only command (owner tools, and the flat names kept
    when a command was grouped for the slash budget).

A name that resolves only to a *group* is the bug, and gets the fix in its
failure message.
"""

import ast
import pathlib
import re

from discord import app_commands
from discord.ext import commands

# `/name`, `/name sub`, `/name sub arg` — lowercase words in backticks. The
# backticks are what make it a quoted command rather than a path or a URL.
_REFERENCE = re.compile(r"`/([a-z][a-z0-9]*(?: [a-z][a-z0-9]*)*)`")

_ROOTS = ("cogs",)


def _docstring_ids(tree: ast.AST) -> set[int]:
    """Every string node that is a docstring, by identity."""
    out = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            out.add(id(body[0].value))
    return out


def _printed_references() -> dict[str, set[str]]:
    """Command names quoted in non-docstring strings → where they were found."""
    found: dict[str, set[str]] = {}
    for root in _ROOTS:
        for path in sorted(pathlib.Path(root).rglob("*.py")):
            tree = ast.parse(path.read_text())
            docs = _docstring_ids(tree)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and id(node) not in docs
                ):
                    for name in _REFERENCE.findall(node.value):
                        found.setdefault(name, set()).add(f"{path}:{node.lineno}")
    return found


def _slash_surface(bot) -> tuple[set[str], set[str]]:
    """(runnable slash names, group names) from the live app-command tree."""
    runnable, groups = set(), set()
    for cmd in bot.tree.walk_commands():
        if isinstance(cmd, app_commands.Group):
            groups.add(cmd.qualified_name)
        else:
            runnable.add(cmd.qualified_name)
    return runnable, groups


def _prefix_runnable(bot, name: str) -> bool:
    """Whether the prefix side can run this name (a group can, bare)."""
    return bot.get_command(name) is not None


async def test_every_printed_command_can_actually_be_run(tree_bot):
    bot = tree_bot
    runnable, groups = _slash_surface(bot)
    problems: list[str] = []

    for name, where in sorted(_printed_references().items()):
        words = name.split()
        prefixes = [" ".join(words[:i]) for i in range(len(words), 0, -1)]
        if any(p in runnable for p in prefixes):
            continue
        if any(_prefix_runnable(bot, p) for p in prefixes):
            # A prefix-only command (owner tools, flat names kept when a
            # command was grouped) — real, just not on slash. The one thing
            # that must not pass here is a bare *group*, checked below.
            command = next(
                bot.get_command(p) for p in prefixes if _prefix_runnable(bot, p)
            )
            if not isinstance(command, commands.Group) or name not in groups:
                continue
        site = sorted(where)[0]
        if name in groups:
            subs = sorted(c for c in runnable if c.startswith(f"{name} "))
            problems.append(
                f"`/{name}` is a group, not a command — Discord can't run it. "
                f"Name a subcommand instead ({', '.join(f'/{s}' for s in subs[:4])}"
                f"{', …' if len(subs) > 4 else ''}). First seen at {site}"
            )
        else:
            problems.append(f"`/{name}` is not a command at all. First seen at {site}")

    assert not problems, "\n".join(problems)
