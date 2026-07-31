"""
The guard that the website can't quietly become a different game.

`web/engine/` re-orchestrates the economy without Discord, which is a real risk:
the tempting bug is not a wrong number, it is a *copy* — someone inlines a drop
table or a cooldown constant "just for the web", the two drift, and nobody
notices until a member points out that fishing pays better on the site.

So this file asserts the shape of the dependency rather than the behaviour:
every roll, table, ladder and cooldown the engine uses is imported from the cog
that owns it, and every claim is the same `utils.db` statement the cog calls. If
a future edit defines a local `RARITY_ODDS` or a `CAST_COOLDOWN = 20`, this
fails — which is the point at which someone has to think about it.

It also pins the couple of places the web is deliberately *allowed* to differ,
so those stay decisions rather than accidents.
"""

import ast
import inspect
import pathlib

import pytest

from web.engine import activities as A
from web.engine import economy as E
from web.engine import fishing as F

ENGINE_DIR = pathlib.Path(__file__).resolve().parent.parent / "web" / "engine"


def _module_source(module) -> str:
    return pathlib.Path(inspect.getfile(module)).read_text(encoding="utf-8")


def _assignments(module) -> set[str]:
    """Every module-level name the engine assigns itself."""
    tree = ast.parse(_module_source(module))
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


# ══════════════════════════════════════════════════════════════════════════════
#  The rules and tables come from the cogs
# ══════════════════════════════════════════════════════════════════════════════
def test_fishing_engine_uses_the_cogs_own_helpers():
    """Every roll goes through the function `/fish` rolls with."""
    for name in (
        "rarity_odds",
        "roll_weight",
        "catch_value",
        "treasure_coins",
        "rod_info",
        "next_rod",
        "fish_level",
        "level_luck_bonus",
        "next_streak",
        "streak_bonus_coins",
        "generate_quest",
        "quest_reward",
    ):
        fn = getattr(F, name)
        assert (
            fn.__module__ == "cogs.fishing.helpers"
        ), f"{name} is not the cog's — the web has grown its own copy"

    for name in ("pick_spot_rarity", "pick_spot_fish", "roll_hazard", "unlock_state"):
        assert getattr(F, name).__module__ == "cogs.fishing.spots", name


def test_activities_engine_uses_the_cogs_own_helpers():
    for name in (
        "roll_work_pay",
        "roll_vein",
        "pick_ore",
        "roll_cave_in",
        "roll_hunt_bag",
        "pick_hunt_catch",
        "pick_explore_outcome",
        "apply_streak",
        "streak_multiplier",
        "charge_state",
        "max_charges",
        "effective_cooldown",
        "roll_encounter",
        "resolve_encounter",
        "outcome_coins",
        "rob_success",
        "rob_steal_amount",
    ):
        assert getattr(A, name).__module__ == "cogs.activities.helpers", name


def test_economy_engine_uses_the_cogs_own_daily_maths():
    assert E.compute_daily.__module__ == "cogs.economy.helpers"
    assert E.seconds_to_afford.__module__ == "cogs.economy.helpers"
    assert E._rank_title.__module__ == "cogs.economy.helpers"


def test_constants_are_imported_not_restated():
    """A local `CAST_COOLDOWN = 20` would be the drift bug in its purest form."""
    forbidden = {
        F: {
            "CAST_COOLDOWN",
            "RARITY_ODDS",
            "RODS",
            "FISH",
            "XP_PER_RARITY",
            "MAX_LUCK",
            "TRAP_SOAK",
        },
        A: {
            "ACTIVITY_INFO",
            "ORES",
            "PICKAXES",
            "EXPLORE_FLAVOR",
            "ROB_FINE",
            "ENCOUNTERS",
        },
        E: {"DAILY_STREAK_CAP_DAYS", "REWARD_DEFAULTS"},
    }
    for module, names in forbidden.items():
        assigned = _assignments(module)
        clash = assigned & names
        assert not clash, (
            f"{module.__name__} defines {sorted(clash)} itself instead of importing "
            "it from the cog that owns it"
        )


def test_reward_key_map_matches_the_cogs():
    """The engine restates `_REWARD_KEYS`; if the cog's map changes, so must it.

    Restated rather than imported because reaching into the cog *class* to read
    it would drag Discord into the engine — so this is the guard instead.
    """
    from cogs.economy.cog import Economy

    assert E._REWARD_KEYS == Economy._REWARD_KEYS


# ══════════════════════════════════════════════════════════════════════════════
#  The claims are the same rows
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "module, claim",
    [
        ("fishing", "try_claim_cast"),
        ("fishing", "try_claim_daily_streak"),
        ("fishing", "try_claim_quest_reward"),
        ("fishing", "claim_trap"),
        ("fishing", "try_debit_coins"),
        ("activities", "try_claim_activity"),
        ("activities", "try_claim_adventure_streak"),
        ("activities", "set_pickaxe_level"),
        ("economy", "compute_daily"),
        ("economy", "transfer_coins"),
        ("economy", "try_consume_item"),
    ],
)
def test_engine_goes_through_the_atomic_claim(module, claim):
    """Each engine module must actually call the shared claim, by name.

    A source-level check on purpose: this is about the call existing at all, and
    a behavioural test can only prove the claim happened for the paths it
    exercises.
    """
    source = (ENGINE_DIR / f"{module}.py").read_text(encoding="utf-8")
    assert claim in source, f"web/engine/{module}.py no longer calls {claim}"


def test_no_engine_module_imports_discord():
    """The engine is the Discord-free half. Importing discord here would mean a
    web request could end up needing a gateway object."""
    for path in ENGINE_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("discord"), path.name
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("discord"), path.name


def test_engine_never_builds_an_embed():
    """The whole reason this layer exists is that the cogs' resolvers end in an
    embed. If one shows up here, the split has failed.

    Checked over the parsed tree rather than the raw text, so the docstrings that
    *explain* the split — which necessarily name `discord.Embed` and the embed
    factories — don't trip it.
    """
    # h.fmt_duration / h.coin_boost_multiplier are fine; these four are the
    # embed factories in utils/helpers.py.
    embed_factories = {"ok", "err", "warn", "info", "embed"}
    for path in ENGINE_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "Embed":
                pytest.fail(f"{path.name} builds a Discord embed")
            if isinstance(node, ast.Name) and node.id == "Embed":
                pytest.fail(f"{path.name} builds a Discord embed")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "h"
                and node.func.attr in embed_factories
            ):
                pytest.fail(f"{path.name} calls h.{node.func.attr}() — that's a reply")


# ══════════════════════════════════════════════════════════════════════════════
#  Where the web is deliberately different
# ══════════════════════════════════════════════════════════════════════════════
def test_web_casts_never_start_a_random_event():
    """Events are guild-wide and get announced in a channel; a browser cast has
    no channel. Honouring a running event is fine — starting one is not."""
    source = (ENGINE_DIR / "fishing.py").read_text(encoding="utf-8")
    assert "start_event" not in source
    assert "EVENT_CHANCE" not in source
    # But it must still read them.
    assert "get_active_events" in source


def test_rob_has_no_one_tap_runner():
    """`/rob` takes a target, so it is excluded from the runnable list for the
    same reason it has no Discord button."""
    assert "rob" not in A.RUNNABLE
    assert set(A.RUNNABLE) == {"work", "mine", "hunt", "explore"}
