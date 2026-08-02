"""
tests/test_command_limits.py
Live-tree check that every registered slash command fits Discord's limits.

Discord rejects an *entire* command sync if any command violates its caps, so a
single over-long description can silently break slash registration for the whole
bot. discord.py validates some of this at decoration time but NOT description
length — that only surfaces at sync against the API. This loads every cog into
the dpytest bot and walks the real app-command tree to catch it in CI instead.

The `tree_bot` fixture (every cog loaded, no dpytest backend) lives in
conftest.py — tests/test_command_copy.py walks the same tree.

Limits enforced (per Discord's application-command schema):
  • command / group / subcommand name  ≤ 32 chars (and non-empty)
  • command / group description         1–100 chars
  • options (parameters) per command    ≤ 25
  • option name ≤ 32, option description 1–100
"""

from discord import app_commands

NAME_MAX = 32
DESC_MAX = 100
OPTIONS_MAX = 25


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
