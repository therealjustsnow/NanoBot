"""
tests/test_command_limits.py
Live-tree check that every registered slash command fits Discord's limits.

Discord rejects an *entire* command sync if any command violates its caps, so a
single over-long description can silently break slash registration for the whole
bot. discord.py validates some of this at decoration time but NOT description
length — that only surfaces at sync against the API. This loads every cog into
the dpytest bot and walks the real app-command tree to catch it in CI instead.

Limits enforced (per Discord's application-command schema):
  • command / group / subcommand name  ≤ 32 chars (and non-empty)
  • command / group description         1–100 chars
  • options (parameters) per command    ≤ 25
  • option name ≤ 32, option description 1–100
"""

import pytest_asyncio
from discord import app_commands

import main
import utils.db as db
from cogs.admin.constants import _ALL_COGS

NAME_MAX = 32
DESC_MAX = 100
OPTIONS_MAX = 25


@pytest_asyncio.fixture
async def tree_bot(tmp_path, monkeypatch):
    """A NanoBot with every cog loaded, but NO dpytest backend.

    We only inspect the static app-command tree, so we deliberately skip
    dpytest.configure() — it mutates a module-global fake guild/backend that
    would bleed permission state into other tests.
    """
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "nanobot.db"))
    await db.init()
    bot = main.NanoBot({})
    await bot._async_setup_hook()
    for ext in _ALL_COGS:
        await bot.load_extension(ext)
    try:
        yield bot
    finally:
        # Unload every extension: a Cog shares class-level command objects, and
        # loading it into a second bot in-process mutates them. Unloading
        # restores that state so this test can't bleed into the dpytest-backed
        # command tests.
        for ext in _ALL_COGS:
            try:
                await bot.unload_extension(ext)
            except Exception:
                pass
        for task in list(bot._bg_tasks):
            task.cancel()
        try:
            await bot.http.close()
        except Exception:
            pass
        await db.close()


def _walk(commands):
    for cmd in commands:
        yield cmd
        if isinstance(cmd, app_commands.Group):
            yield from _walk(cmd.commands)


async def test_slash_command_metadata_within_discord_limits(tree_bot):
    bot = tree_bot
    problems: list[str] = []

    for cmd in _walk(bot.tree.get_commands()):
        qn = cmd.qualified_name

        if not cmd.name:
            problems.append(f"{qn}: empty name")
        elif len(cmd.name) > NAME_MAX:
            problems.append(f"{qn}: name {len(cmd.name)} > {NAME_MAX} chars")

        desc = getattr(cmd, "description", "") or ""
        # Groups and commands both carry a description sent to Discord.
        if isinstance(cmd, (app_commands.Command, app_commands.Group)):
            if not desc:
                problems.append(f"{qn}: empty description")
            elif len(desc) > DESC_MAX:
                problems.append(f"{qn}: description {len(desc)} > {DESC_MAX} chars")

        if isinstance(cmd, app_commands.Command):
            params = cmd.parameters
            if len(params) > OPTIONS_MAX:
                problems.append(f"{qn}: {len(params)} options > {OPTIONS_MAX}")
            for p in params:
                if len(p.name) > NAME_MAX:
                    problems.append(
                        f"{qn}: option {p.name!r} name {len(p.name)} > {NAME_MAX}"
                    )
                pdesc = p.description or ""
                if not pdesc:
                    problems.append(f"{qn}: option {p.name!r} has empty description")
                elif len(pdesc) > DESC_MAX:
                    problems.append(
                        f"{qn}: option {p.name!r} description {len(pdesc)} > {DESC_MAX}"
                    )

    assert not problems, "Slash commands violating Discord limits:\n" + "\n".join(
        f"  {p}" for p in problems
    )
