"""utils/db — async SQLite storage, split into per-domain modules.

The public surface is unchanged: ``from utils import db`` then ``db.get_tag(...)``,
``db.init()``, ``db._conn()``, etc. all still work. Connection state and schema
machinery live in :mod:`utils.db._core`; each domain module (tags, economy,
music, …) holds its own accessors and registers its table setup with _core.

Domains are imported in the exact order the old monolithic init() created their
tables (so creation/migration order is identical). Every name each module
defines — public and private — is then lifted into this package's namespace so
existing ``db._ensure_*`` / ``db._conn`` callers keep working against the flat
``db.<name>`` API.
"""

import importlib
import sys as _sys
import types as _types

from . import _core

# Order matches the original init() table-creation sequence. Importing a domain
# module runs its register_init(...) side effect, so this order also fixes the
# order init() builds tables and applies the interleaved one-time migrations.
_DOMAIN_ORDER = (
    "tags",
    "notes",
    "prefixes",
    "schedules",
    "reminders",
    "warnings",
    "welcome",
    "votes",
    "recurring",
    "roles",
    "auditlog",
    "automod",
    "music",
    "leveling",
    "economy",
    "items",
    "casino",
    "activities",
    "progression",
    "fishing",
    "gatekeeper",
    "birthday",
    "liverole",
    "tickets",
    "identity",
    # Account-level social counters (rep, cookies) — user-keyed like identity,
    # but worth no coins, so they sit outside the economy entirely.
    "social",
    # Live /fun wyr poll boards. A board, not a balance: guild-scoped, deleted
    # the moment it announces, and tied to no other domain's tables.
    "polls",
    # Bot-wide, owner-owned knobs (the activity cooldowns, the coin faucet
    # amounts) — no guild id, no user id, so it stands apart from every domain
    # above.
    "settings",
    # The price-refund ledger. Reads the tiers other domains own (fishing rods
    # and charters, activities' pickaxes) but owns no ladder itself, so it is
    # imported after all of them.
    "refunds",
    # No tables of its own — the retention/WAL/VACUUM janitor for every other
    # domain's tables, driven by main.py's daily maintenance loop.
    "maintenance",
    # No tables of its own — registers migration 1 (per-guild economy → global),
    # so it must be imported after every domain whose tables it rebuilds.
    "globalize",
)

# _core first so its connection/schema names (init, close, _conn, migration, …)
# win over the same-named imports the domain modules pull in from it.
_modules = [_core] + [
    importlib.import_module(f".{_name}", __name__) for _name in _DOMAIN_ORDER
]

for _mod in _modules:
    for _attr in dir(_mod):
        if not _attr.startswith("__"):
            globals().setdefault(_attr, getattr(_mod, _attr))

# Connection state lives in a single place — _core. Pre-split, callers and
# tests treated ``db._db`` (the connection slot) and ``db._DB_PATH`` (the file
# path init() opens) as that state (e.g. monkeypatch.setattr(db, "_db", conn)
# to inject an in-memory DB, or monkeypatch.setattr(db, "_DB_PATH", tmp) to
# isolate an on-disk one). Route both package attributes to _core so that
# contract still holds: reads and writes hit _core's module globals, which are
# what _conn()/init() consult. Drop the stale copies the loop above made first.
_CORE_ROUTED = ("_db", "_DB_PATH", "_reader")
for _name in _CORE_ROUTED:
    globals().pop(_name, None)


class _DbPackage(_types.ModuleType):
    def __getattr__(self, name):
        if name in _CORE_ROUTED:
            return getattr(_core, name)
        raise AttributeError(f"module {self.__name__!r} has no attribute {name!r}")

    def __setattr__(self, name, value):
        if name in _CORE_ROUTED:
            setattr(_core, name, value)
        else:
            super().__setattr__(name, value)


_sys.modules[__name__].__class__ = _DbPackage
