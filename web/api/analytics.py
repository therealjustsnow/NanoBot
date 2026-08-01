"""
web/api/analytics.py
One endpoint, because one page.

Everything the analytics screen draws arrives together: splitting it would make
the slowest page in the dashboard slower for no benefit, and the charts are read
as a set rather than one at a time.

Manager-gated. Not because the numbers are secret — most of them are visible on
a leaderboard — but because the page is an *administrative* read: it is about
whether the server's economy and moderation load are healthy, which is not a
question a member is being asked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiohttp import web

from .. import http as H
from ..engine import analytics as A

if TYPE_CHECKING:
    from ..app import Dashboard


def routes(dash: "Dashboard") -> list:
    @H.require_manager
    async def overview(request: web.Request) -> web.Response:
        guild = H.guild_of(request)
        member_ids = [m.id for m in guild.members if not m.bot]
        range_key = request.query.get("range", A.DEFAULT_RANGE)
        return H.ok(await A.overview(guild.id, member_ids, range_key))

    return [web.get("/api/guilds/{guild_id}/analytics", overview)]
