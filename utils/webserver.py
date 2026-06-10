"""
utils/webserver.py
A small shared HTTP server so several features can ride one TCP port.

NanoBot may expose more than one plain-HTTP surface — the health probe and the
vote webhook — but a host (e.g. a Pterodactyl allocation) often grants only a
single inbound port. This manager lets each feature *register* its routes
against a (host, port); registrations that share a (host, port) are merged into
one aiohttp application bound once, so `/health` and `/webhook/*` can coexist on
the same port. Features on different ports get their own bound server.

Binding retries briefly: during `!restart`/`!upgrade` the replacement process
starts while the old one is still releasing the port, so a few retries avoid a
spurious "address already in use".

Owners register by a string key and can unregister (e.g. on cog unload). After
any change, call `restart()` to rebuild and rebind from the current set.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Iterable

from aiohttp import web

log = logging.getLogger("NanoBot.web")


class HttpServer:
    def __init__(self, bind_attempts: int = 5, bind_delay: float = 1.0) -> None:
        # owner -> {host, port, routes, middlewares}
        self._regs: dict[str, dict] = {}
        # (host, port) -> running AppRunner
        self._runners: dict[tuple[str, int], web.AppRunner] = {}
        self._bind_attempts = bind_attempts
        self._bind_delay = bind_delay

    @property
    def is_running(self) -> bool:
        return bool(self._runners)

    def register(
        self,
        owner: str,
        host: str,
        port: int,
        routes: Iterable[web.RouteDef],
        middlewares: Iterable = (),
    ) -> None:
        """Add/replace an owner's routes. Call restart() to (re)bind."""
        self._regs[owner] = {
            "host": host,
            "port": int(port),
            "routes": list(routes),
            "middlewares": list(middlewares),
        }

    def unregister(self, owner: str) -> None:
        self._regs.pop(owner, None)

    async def restart(self) -> None:
        """Tear down all bound servers and rebuild from the current registrations."""
        await self.stop()
        groups: dict[tuple[str, int], dict] = {}
        for reg in self._regs.values():
            key = (reg["host"], reg["port"])
            slot = groups.setdefault(key, {"routes": [], "middlewares": []})
            slot["routes"].extend(reg["routes"])
            for mw in reg["middlewares"]:
                if mw not in slot["middlewares"]:
                    slot["middlewares"].append(mw)

        for (host, port), slot in groups.items():
            app = web.Application(middlewares=slot["middlewares"])
            app.add_routes(slot["routes"])
            runner = web.AppRunner(app)
            await runner.setup()
            try:
                await self._bind_with_retry(runner, host, port)
            except OSError as exc:
                log.warning(
                    "HTTP server could not bind %s:%d after retries: %s",
                    host,
                    port,
                    exc,
                )
                await runner.cleanup()
                continue
            self._runners[(host, port)] = runner

    async def _bind_with_retry(
        self,
        runner: web.AppRunner,
        host: str,
        port: int,
    ) -> None:
        attempts = self._bind_attempts
        delay = self._bind_delay
        last: OSError | None = None
        for attempt in range(attempts):
            try:
                site = web.TCPSite(runner, host=host, port=port)
                await site.start()
                log.info("🌐 HTTP server listening on %s:%d", host, port)
                return
            except OSError as exc:
                last = exc
                if attempt < attempts - 1:
                    log.debug(
                        "Bind %s:%d failed (attempt %d/%d): %s — retrying",
                        host,
                        port,
                        attempt + 1,
                        attempts,
                        exc,
                    )
                    await asyncio.sleep(delay)
        assert last is not None
        raise last

    async def stop(self) -> None:
        for runner in list(self._runners.values()):
            try:
                await asyncio.wait_for(runner.cleanup(), timeout=5.0)
            except (asyncio.TimeoutError, Exception):  # noqa: B014 - best effort
                pass
        self._runners.clear()
