"""
utils/health.py
Optional plain-HTTP health-check endpoint for container/orchestrator probes.

Off by default. Enable by setting [bot] health_check_port to a non-zero port in
config.ini. When on, the bot serves:

    GET /health   → 200 {"status": "ok", ...} once the gateway is ready,
                    503 {"status": "starting"} before on_ready fires.

The route is mounted on the shared HttpServer (utils/webserver.py), so it can
share a port with the vote webhook when only one inbound port is available.
Uses the aiohttp already pulled in by discord.py, so it adds no new dependency.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from main import NanoBot

log = logging.getLogger("NanoBot.health")

HEALTH_OWNER = "health"


async def _handle(bot: "NanoBot", request: web.Request) -> web.Response:
    ready = bot.is_ready()
    latency = bot.latency  # gateway heartbeat RTT in seconds (nan early on)
    payload = {
        "status": "ok" if ready else "starting",
        "ready": ready,
        "guilds": len(bot.guilds),
        "latency_ms": (
            round(latency * 1000) if latency == latency else None  # nan check
        ),
        "uptime_s": round(time.time() - bot.start_time.timestamp()),
    }
    return web.json_response(payload, status=200 if ready else 503)


def health_routes(bot: "NanoBot") -> list[web.RouteDef]:
    """Route defs for the health endpoint, bound to this bot instance."""

    async def handler(request: web.Request) -> web.Response:
        return await _handle(bot, request)

    return [web.get("/health", handler)]
