"""
tests/test_webserver.py
Functional tests for the shared HttpServer (utils/webserver.py).

Binds real sockets on 127.0.0.1 (ephemeral ports), so it exercises the actual
aiohttp path: port sharing across owners, route teardown, and bind-retry.
"""

import socket

from aiohttp import ClientSession, web

from utils.webserver import HttpServer


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def test_two_owners_share_one_port():
    srv = HttpServer()
    port = _free_port()

    async def health(_req):
        return web.json_response({"ok": True})

    async def webhook(_req):
        return web.Response(text="vote")

    srv.register("health", "127.0.0.1", port, [web.get("/health", health)])
    srv.register("votes", "127.0.0.1", port, [web.post("/webhook/topgg", webhook)])
    await srv.restart()
    try:
        # Exactly one listener bound, serving both routes.
        assert len(srv._runners) == 1
        async with ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{port}/health") as r:
                assert r.status == 200
                assert (await r.json()) == {"ok": True}
            async with s.post(f"http://127.0.0.1:{port}/webhook/topgg") as r:
                assert r.status == 200
                assert (await r.text()) == "vote"
    finally:
        await srv.stop()
    assert not srv.is_running


async def test_different_ports_get_separate_listeners():
    srv = HttpServer()
    p1, p2 = _free_port(), _free_port()

    async def a(_req):
        return web.Response(text="a")

    async def b(_req):
        return web.Response(text="b")

    srv.register("a", "127.0.0.1", p1, [web.get("/a", a)])
    srv.register("b", "127.0.0.1", p2, [web.get("/b", b)])
    await srv.restart()
    try:
        assert len(srv._runners) == 2
        async with ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{p1}/a") as r:
                assert (await r.text()) == "a"
            async with s.get(f"http://127.0.0.1:{p2}/b") as r:
                assert (await r.text()) == "b"
    finally:
        await srv.stop()


async def test_unregister_drops_route_on_restart():
    srv = HttpServer()
    port = _free_port()

    async def a(_req):
        return web.Response(text="a")

    srv.register("a", "127.0.0.1", port, [web.get("/a", a)])
    await srv.restart()
    try:
        srv.unregister("a")
        await srv.restart()
        # No registrations left → nothing bound.
        assert not srv.is_running
    finally:
        await srv.stop()


async def test_bind_retry_gives_up_when_port_held():
    # Occupy the port so the server can never bind; with tiny retry settings it
    # should exhaust attempts and leave nothing bound (logged, not raised).
    port = _free_port()
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", port))
    blocker.listen(1)
    try:
        srv = HttpServer(bind_attempts=2, bind_delay=0.01)

        async def a(_req):
            return web.Response(text="a")

        srv.register("a", "127.0.0.1", port, [web.get("/a", a)])
        await srv.restart()
        assert not srv.is_running
        await srv.stop()
    finally:
        blocker.close()
