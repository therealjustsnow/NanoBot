"""
tests/test_slash_entrypoints.py
A hybrid group's *own* callback is not invocable over slash. Discord has no
"run the group itself" gesture, so `/adventure` only ever offers its
subcommands — the landing card the group callback renders is prefix-only
unless the group declares `fallback="…"`, which registers that callback as a
real subcommand.

That is how the economy shipped four dashboards that phone users could not
reach: `/adventure`, `/inventory`, `/casino`, and `/craft` all rendered a rich
overview for `n!…` and nothing for `/…`.

This guard pins the rule for every economy group. A group is fine when either:
  * it declares a `fallback`, or
  * its bare form just runs an action that already has its own subcommand
    (`/mine` digs and `/mine dig` exists; `/fish` casts, `/coin` shows the
    leaderboard `/coin top` shows).
Anything else is a landing view with no slash entry point.
"""

import pytest
from discord.ext import commands

# group name → the subcommand its bare form is reachable through.
# `fallback` means the group declares one; anything else names the pre-existing
# subcommand that already does what the bare form does.
_EXPECTED_ENTRYPOINTS = {
    "adventure": "fallback",  # the dashboard — was unreachable
    "inventory": "fallback",  # the item view — was unreachable
    "casino": "fallback",  # the games/limits/jackpot overview
    "craft": "fallback",  # the recipe list
    "progress": "fallback",
    "prestige": "fallback",  # nested under /progress, already correct
    "profile": "fallback",
    "mine": "dig",
    "fish": "cast",
    "coin": "top",
    # /shop's bare form used to be the guild item list; it is now the aisle hub
    # (profile / wallet / server cosmetics), which is a landing view and so
    # needs its own slash entry point.
    "shop": "fallback",
}

_ECONOMY_COGS = (
    "cogs.economy",
    "cogs.fishing",
    "cogs.inventory",
    "cogs.crafting",
    "cogs.casino",
    "cogs.progression",
    "cogs.activities",
    "cogs.identity",
)


def _hybrid_groups(bot):
    return {
        c.name: c for c in bot.walk_commands() if isinstance(c, commands.HybridGroup)
    }


@pytest.mark.cogs(*_ECONOMY_COGS)
async def test_every_economy_group_has_a_slash_entry_point(bot):
    """No economy group may present a landing view that slash can't open."""
    unreachable = []
    for name, group in _hybrid_groups(bot).items():
        expected = _EXPECTED_ENTRYPOINTS.get(name)
        assert expected, (
            f"/{name} is a new economy hybrid group — add it to "
            "_EXPECTED_ENTRYPOINTS, declaring either 'fallback' (its bare form "
            "renders something) or the subcommand that already covers it."
        )
        if expected == "fallback":
            if not group.fallback:
                unreachable.append(f"/{name} (bare view has no slash entry point)")
        else:
            children = {c.name for c in group.app_command.commands}
            if expected not in children:
                unreachable.append(f"/{name} — expected /{name} {expected} to exist")
    assert not unreachable, "Prefix-only landing views: " + ", ".join(unreachable)


@pytest.mark.cogs(*_ECONOMY_COGS)
async def test_fallbacks_register_a_real_slash_subcommand(bot):
    """A declared fallback must actually show up in the app-command tree —
    that child is the thing a phone user taps."""
    groups = _hybrid_groups(bot)
    for name, expected in _EXPECTED_ENTRYPOINTS.items():
        if expected != "fallback":
            continue
        group = groups[name]
        children = {c.name for c in group.app_command.commands}
        assert group.fallback in children, (
            f"/{name} declares fallback={group.fallback!r} but no such "
            f"app command child exists (has: {sorted(children)})"
        )


@pytest.mark.cogs(*_ECONOMY_COGS)
async def test_fallback_names_do_not_shadow_a_real_subcommand(bot):
    """A fallback name that collides with an existing subcommand would take
    that subcommand's slot — /mine's bare form is a dig, so it must never
    declare fallback='dig'."""
    for name, group in _hybrid_groups(bot).items():
        if not group.fallback:
            continue
        siblings = [c.name for c in group.commands]
        assert siblings.count(group.fallback) == 0, (
            f"/{name} fallback={group.fallback!r} collides with its own "
            "subcommand of the same name"
        )
