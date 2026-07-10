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
    "gatekeeper",
    "birthday",
    "liverole",
    "tickets",
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

# The connection lives in a single place — _core._db. Pre-split, callers and
# tests treated ``db._db`` as that slot (e.g. monkeypatch.setattr(db, "_db",
# conn) to inject an in-memory DB). Route the package's ``_db`` attribute to
# _core so that contract still holds: reads and writes both hit _core._db, which
# is what _conn() consults. Drop the stale copy the loop above made first.
globals().pop("_db", None)


class _DbPackage(_types.ModuleType):
    def __getattr__(self, name):
        if name == "_db":
            return _core._db
        raise AttributeError(f"module {self.__name__!r} has no attribute {name!r}")

    def __setattr__(self, name, value):
        if name == "_db":
            _core._db = value
        else:
            super().__setattr__(name, value)


_sys.modules[__name__].__class__ = _DbPackage
