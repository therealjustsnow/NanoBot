"""
tests/test_slash_surface.py
Guards on the *shape* of the slash command tree, not on any one command.

Discord caps an application at 100 top-level slash commands and rejects the
whole sync if you go over. NanoBot reached 99. That is not just a headroom
problem: a picker with 99 entries is a picker nobody reads, and most of what
had a top-level slot was there by default rather than by decision — owner
tools, once-a-server setup, and six single-purpose lookups.

Two rules came out of that audit, and this file is where they are enforced:

  1. **Owner-only commands are not slash commands.** A command gated on
     `is_owner` shows up in the picker of every member of every server the bot
     is in, and none of them can run it. Owner tooling lives on the prefix
     (`n!econ`, `n!cooldown`, `n!reload`), so `is_owner` + an app command is a
     contradiction. Fix it with `with_app_command=False`, don't add an
     exemption here.

  2. **The top-level budget keeps real headroom.** Not a style rule — going
     over is a hard API failure that takes the *entire* command tree down with
     it, including the commands that were fine.
"""

import pytest_asyncio
from discord import app_commands
from discord.ext import commands

import main
import utils.db as db
from cogs.admin.constants import _ALL_COGS

# Discord's hard cap. Crossing it fails the sync for every command at once.
DISCORD_TOP_LEVEL_CAP = 100

# What we hold ourselves to. The gap is deliberate room for the next feature to
# land without triggering an emergency reshuffle — if a change needs this
# raised, that is the moment to group something instead.
TOP_LEVEL_BUDGET = 92


@pytest_asyncio.fixture
async def tree_bot(tmp_path, monkeypatch):
    """Every cog loaded, no dpytest backend — we only read the static tree.

    Mirrors tests/test_command_limits.py, including the unload-everything
    teardown: a Cog shares class-level command objects, so loading it into a
    second bot in-process mutates them.
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


def _walk_app_commands(cmds):
    for cmd in cmds:
        yield cmd
        if isinstance(cmd, app_commands.Group):
            yield from _walk_app_commands(cmd.commands)


def _is_owner_gated(cmd) -> bool:
    """True if any of the command's checks is discord.py's is_owner check."""
    for check in getattr(cmd, "checks", []) or []:
        if getattr(check, "__qualname__", "").startswith("is_owner"):
            return True
    return False


async def test_top_level_slash_commands_stay_within_budget(tree_bot):
    top = tree_bot.tree.get_commands()
    assert len(top) <= TOP_LEVEL_BUDGET, (
        f"{len(top)} top-level slash commands — over the {TOP_LEVEL_BUDGET} "
        f"budget (Discord's hard cap is {DISCORD_TOP_LEVEL_CAP}, and going over "
        "it fails the sync for the whole tree). Group the new command under an "
        "existing one rather than raising this number."
    )


async def test_no_owner_only_command_is_exposed_over_slash(tree_bot):
    """`is_owner` and an app command together is always a mistake.

    Prefix commands are checked here too — a hybrid command's app_command is
    what ends up in the picker, so the hybrid is where this goes wrong.
    """
    offenders = []
    for cmd in tree_bot.walk_commands():
        if not _is_owner_gated(cmd):
            continue
        if isinstance(cmd, (commands.HybridCommand, commands.HybridGroup)):
            if cmd.app_command is not None:
                offenders.append(cmd.qualified_name)

    assert not offenders, (
        "Owner-only commands exposed over slash: "
        + ", ".join(sorted(offenders))
        + " — pass with_app_command=False so they stay prefix-only."
    )


async def test_owner_commands_that_moved_off_slash_still_work_on_prefix(tree_bot):
    """The audit moved these seven off the slash tree. They must still exist as
    prefix commands — moving a command off slash must never delete it."""
    expected_prefix_only = [
        "profile grant",
        "profile grantall",
        "profile revoke",
        "coin grant",
        "coin take",
        "coin reset",
        "fish event",
    ]
    for qualified_name in expected_prefix_only:
        cmd = tree_bot.get_command(qualified_name)
        assert cmd is not None, f"n!{qualified_name} disappeared entirely"
        assert _is_owner_gated(cmd), f"n!{qualified_name} lost its is_owner check"
        assert (
            getattr(cmd, "app_command", None) is None
        ), f"/{qualified_name} is back on the slash tree"


async def test_regrouped_commands_kept_their_prefix_names(tree_bot):
    """Grouping a command under a slash parent must not cost prefix users the
    flat name they already type. Every one of these moved on the slash side
    only."""
    for name in (
        "remind",  # slash: /reminders user
        "every",  # slash: /recurring every
        "id",  # slash: /info id
        "mc",  # slash: /info members
        "roleinfo",  # slash: /info role
        "banner",  # slash: /info banner
        "channelinfo",  # slash: /info channel
        "firstmsg",  # slash: /info firstmsg
        "blocksong",  # slash: /music blocksong add
        "blockuser",  # slash: /music blockuser add
        # NB: guild-playlist *curation* has no prefix twin and never had one —
        # it went straight from /guildplaylist to /music guildplaylist.
    ):
        assert tree_bot.get_command(name) is not None, f"n!{name} disappeared"


async def test_regrouped_commands_are_reachable_over_slash(tree_bot):
    """...and the slash side must actually have somewhere to land. A command
    that left the top level without arriving under a parent is just deleted."""
    tree = {c.qualified_name for c in _walk_app_commands(tree_bot.tree.get_commands())}
    for qualified_name in (
        "reminders user",
        "recurring every",
        "info id",
        "info members",
        "info role",
        "info banner",
        "info channel",
        "info firstmsg",
        "music blocksong add",
        "music blockuser add",
        "music guildplaylist add",
    ):
        assert qualified_name in tree, f"/{qualified_name} is not in the slash tree"
