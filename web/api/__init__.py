"""
web/api/
The REST surface, grouped by what it is *for* rather than by HTTP verb.

    auth      sign in, sign out, who am I
    guilds    a server's overview, channels, roles, permission health
    settings  one endpoint family per configurable feature
    roles     self-role panels, including the drag-to-reorder save
    play      the economy, played from the browser
    me        the account: profile, wallet, inventory, boards

Every module exports `routes(dash)` returning aiohttp route defs bound to the
one `Dashboard`. Handlers stay thin — they parse, they call an engine or a `db.`
accessor, they shape a reply. Anything that decides something belongs in
`web/engine/` or `utils/db/`, where it can be tested without a socket.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import auth, guilds, me, play, roles, settings

if TYPE_CHECKING:
    from ..app import Dashboard

_MODULES = (auth, guilds, settings, roles, play, me)


def routes(dash: "Dashboard") -> list:
    """Every API route, in module order."""
    out: list = []
    for module in _MODULES:
        out.extend(module.routes(dash))
    return out


__all__ = ["routes"]
