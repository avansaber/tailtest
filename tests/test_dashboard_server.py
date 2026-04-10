"""Tests for the dashboard HTTP + WebSocket server (Phase 4 Task 4.2)."""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from tailtest.dashboard.server import (
    DashboardServer,
    _host_is_allowed,
    find_free_port,
)

# ---------------------------------------------------------------------------
# Origin / Host header checks
# ---------------------------------------------------------------------------


def test_origin_check_allows_localhost() -> None:
    assert _host_is_allowed("localhost:7777") is True


def test_origin_check_allows_127_0_0_1() -> None:
    assert _host_is_allowed("127.0.0.1:7777") is True


def test_origin_check_rejects_external_host() -> None:
    assert _host_is_allowed("evil.com") is False


def test_origin_check_allows_ipv6_localhost() -> None:
    assert _host_is_allowed("[::1]:7777") is True


# ---------------------------------------------------------------------------
# Port finding
# ---------------------------------------------------------------------------


def test_find_free_port_returns_a_port() -> None:
    port = find_free_port(7700)
    assert 1024 <= port <= 65535


# ---------------------------------------------------------------------------
# Full server lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_server_starts_and_stops(tmp_path: Path) -> None:
    """Start on a free port, GET /, verify 200, shut down cleanly."""
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()

    server = DashboardServer(tailtest_dir)
    port = find_free_port(17777)
    await server.start(host="127.0.0.1", port=port)

    try:
        import aiohttp

        async with (
            aiohttp.ClientSession() as session,
            session.get(
                f"http://127.0.0.1:{port}/",
                headers={"Host": f"127.0.0.1:{port}"},
            ) as resp,
        ):
            assert resp.status == 200
            text = await resp.text()
            assert "tailtest dashboard" in text
    finally:
        await server.stop()


# ---------------------------------------------------------------------------
# Middleware via aiohttp TestServer / TestClient
# ---------------------------------------------------------------------------


def _make_test_app() -> web.Application:
    """Build a minimal app with the same middleware as DashboardServer."""
    from tailtest.dashboard.server import _localhost_only_middleware

    app = web.Application(middlewares=[_localhost_only_middleware])

    async def _index(_request: web.Request) -> web.Response:
        return web.Response(text="ok")

    app.router.add_get("/", _index)
    return app


@pytest.mark.asyncio
async def test_middleware_allows_localhost_host() -> None:
    """TestClient connects to 127.0.0.1; Host header passes the check."""
    app = _make_test_app()
    async with TestClient(TestServer(app)) as client:
        # TestServer binds to 127.0.0.1; aiohttp sets Host automatically.
        resp = await client.get("/")
        assert resp.status == 200


@pytest.mark.asyncio
async def test_middleware_rejects_external_host() -> None:
    """Spoofing a non-localhost Host header must yield 403."""
    app = _make_test_app()
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/", headers={"Host": "evil.com"})
        assert resp.status == 403


# ---------------------------------------------------------------------------
# File-watcher broadcast
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_event_lines_broadcast_to_ws_clients(tmp_path: Path) -> None:
    """Appending a line to events.jsonl must push a JSON message to WS clients."""
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()
    events_path = tailtest_dir / "events.jsonl"

    server = DashboardServer(tailtest_dir)
    port = find_free_port(18777)
    await server.start(host="127.0.0.1", port=port)

    received: list[str] = []

    async def _ws_listener() -> None:
        import aiohttp

        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(
                f"ws://127.0.0.1:{port}/live",
                headers={"Host": f"127.0.0.1:{port}"},
            ) as ws,
        ):
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    received.append(msg.data)
                    # Got one message -- stop listening.
                    break

    listener_task = asyncio.create_task(_ws_listener())

    # Give the listener time to connect.
    await asyncio.sleep(0.2)

    # Append a line to the events file.
    events_path.write_text('{"kind": "run", "session_id": "s1"}\n')

    # Allow the watcher and broadcast to run.
    await asyncio.sleep(1.0)

    listener_task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await listener_task

    await server.stop()

    assert len(received) >= 1
    msg = json.loads(received[0])
    assert msg["kind"] == "event"
    assert msg["payload"]["kind"] == "run"


# ---------------------------------------------------------------------------
# Static file serving (Phase 4 Tasks 4.4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_receives_ping(tmp_path: Path) -> None:
    """WS client receives at least one ping message when ping interval is short."""
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()

    import tailtest.dashboard.server as _server_mod

    original_interval = _server_mod._PING_INTERVAL
    _server_mod._PING_INTERVAL = 0.05  # 50 ms

    server = DashboardServer(tailtest_dir)
    port = find_free_port(20777)
    await server.start(host="127.0.0.1", port=port)

    received_ping = False
    try:
        import aiohttp

        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(
                f"ws://127.0.0.1:{port}/live",
                headers={"Host": f"127.0.0.1:{port}"},
            ) as ws,
        ):
            # Wait up to 0.5 s for a ping.
            deadline = asyncio.get_event_loop().time() + 0.5
            while asyncio.get_event_loop().time() < deadline:
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=0.15)
                except TimeoutError:
                    continue
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data.get("kind") == "ping":
                        received_ping = True
                        break
    finally:
        _server_mod._PING_INTERVAL = original_interval
        await server.stop()

    assert received_ping, "Expected at least one ping message within 0.5 s"


@pytest.mark.asyncio
async def test_ws_connect_and_disconnect(tmp_path: Path) -> None:
    """WS client connects cleanly and server removes it from _ws_clients on disconnect."""
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()

    server = DashboardServer(tailtest_dir)
    port = find_free_port(21777)
    await server.start(host="127.0.0.1", port=port)

    try:
        import aiohttp

        async with (
            aiohttp.ClientSession() as session,
            session.ws_connect(
                f"ws://127.0.0.1:{port}/live",
                headers={"Host": f"127.0.0.1:{port}"},
            ) as ws,
        ):
            # Give the server a moment to register the client.
            await asyncio.sleep(0.05)
            assert len(server._ws_clients) == 1
            # Close the connection from the client side.
            await ws.close()

        # Give the server a moment to process the disconnect.
        await asyncio.sleep(0.1)
        assert len(server._ws_clients) == 0
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_index_serves_html(tmp_path: Path) -> None:
    """GET / returns 200 with Content-Type text/html and an <html element."""
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()

    server = DashboardServer(tailtest_dir)
    port = find_free_port(19777)
    await server.start(host="127.0.0.1", port=port)

    try:
        import aiohttp

        async with (
            aiohttp.ClientSession() as session,
            session.get(
                f"http://127.0.0.1:{port}/",
                headers={"Host": f"127.0.0.1:{port}"},
            ) as resp,
        ):
            assert resp.status == 200
            assert "text/html" in resp.content_type
            body = await resp.text()
            assert "<html" in body
    finally:
        await server.stop()
