"""
utils/health.py
Optional plain-HTTP health-check endpoint for container/orchestrator probes.

Off by default. Enable by setting [bot] health_check_port to a non-zero port in
config.ini. When on, the bot serves:

    GET /health   → 200 {"status": "ok", ...} once the gateway is ready,
                    503 {"status": "starting"} before on_ready fires.

Uses the aiohttp already pulled in by discord.py, so it adds no new dependency.
The server is started in NanoBot.setup_hook() and stopped in close().
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from main import NanoBot

log = logging.getLogger("NanoBot.health")


class HealthServer:
    """A tiny aiohttp server exposing GET /health for liveness/readiness probes."""

    def __init__(self, bot: "NanoBot", port: int, host: str = "0.0.0.0"):
        self.bot = bot
        self.port = port
        self.host = host
        self._runner: web.AppRunner | None = None

    async def _handle(self, request: web.Request) -> web.Response:
        ready = self.bot.is_ready()
        latency = self.bot.latency  # gateway heartbeat RTT in seconds (nan early on)
        payload = {
            "status": "ok" if ready else "starting",
            "ready": ready,
            "guilds": len(self.bot.guilds),
            "latency_ms": (
                round(latency * 1000) if latency == latency else None  # nan check
            ),
            "uptime_s": round(time.time() - self.bot.start_time.timestamp()),
        }
        return web.json_response(payload, status=200 if ready else 503)

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/health", self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        # Defaults to all interfaces so a container orchestrator can reach the
        # probe; set health_check_host=127.0.0.1 to keep it host-local (the
        # payload exposes guild count + latency without auth).
        site = web.TCPSite(self._runner, host=self.host, port=self.port)
        await site.start()
        log.info(
            "🩺 Health-check endpoint listening on %s:%d/health", self.host, self.port
        )

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
