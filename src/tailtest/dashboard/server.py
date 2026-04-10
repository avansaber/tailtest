"""Dashboard HTTP + WebSocket server (Phase 4 Task 4.2).

Serves the live tailtest dashboard on ``127.0.0.1`` (localhost only).
Streams events from ``.tailtest/events.jsonl`` to connected WebSocket
clients via a background file-watcher.

Usage::

    from tailtest.dashboard.server import run_server
    run_server(tailtest_dir=Path(".tailtest"), port=7777)

The module can also be used programmatically::

    server = DashboardServer(tailtest_dir=Path(".tailtest"))
    await server.start(host="127.0.0.1", port=7777)
    # ... later ...
    await server.stop()
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import signal
import socket
from pathlib import Path
from typing import Any

import aiohttp.web
import watchfiles

logger = logging.getLogger("tailtest.dashboard")

_PING_INTERVAL = 30  # seconds
_PORT_RETRY_LIMIT = 10
_PLACEHOLDER_HTML = "<html><body><h1>tailtest dashboard</h1><p>Starting up...</p></body></html>"

# Hosts accepted by the origin-check middleware.
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}


def _host_is_allowed(host_header: str) -> bool:
    """Return True if *host_header* is a localhost host (with optional port)."""
    # Strip port suffix, e.g. "localhost:7777" -> "localhost"
    bare = host_header.rsplit(":", 1)[0] if ":" in host_header else host_header
    # IPv6 addresses arrive as "[::1]" or "[::1]:7777"; after rsplit the bare
    # value is "[::1]". Accept both bracketed and unbracketed forms.
    return bare in _ALLOWED_HOSTS


def find_free_port(start: int, attempts: int = _PORT_RETRY_LIMIT) -> int:
    """Return the first free TCP port starting at *start*.

    Raises ``OSError`` if no free port is found within *attempts* tries.
    """
    for offset in range(attempts):
        port = start + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise OSError(f"No free port found in range {start}–{start + attempts - 1}")


@aiohttp.web.middleware
async def _localhost_only_middleware(
    request: aiohttp.web.Request,
    handler: Any,
) -> aiohttp.web.StreamResponse:
    """Reject requests whose Host header points outside localhost."""
    host = request.headers.get("Host", "")
    if not _host_is_allowed(host):
        logger.warning("Rejected request with Host: %r", host)
        raise aiohttp.web.HTTPForbidden(reason="Dashboard is localhost-only")
    return await handler(request)


class DashboardServer:
    """HTTP + WebSocket server for the live tailtest dashboard.

    Parameters
    ----------
    tailtest_dir:
        Path to the project's ``.tailtest/`` directory. The event stream is
        read from ``<tailtest_dir>/events.jsonl``.
    """

    def __init__(self, tailtest_dir: Path) -> None:
        self._tailtest_dir = tailtest_dir
        self._events_path = tailtest_dir / "events.jsonl"

        # Registry of live WebSocket connections.
        self._ws_clients: set[aiohttp.web.WebSocketResponse] = set()

        # Byte offset into events.jsonl — tracks how much we've already read.
        self._known_size: int = 0

        # Background tasks — set in start().
        self._watcher_task: asyncio.Task[None] | None = None
        self._ping_task: asyncio.Task[None] | None = None

        # aiohttp runner — set in start().
        self._runner: aiohttp.web.AppRunner | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self, host: str = "127.0.0.1", port: int = 7777) -> int:
        """Start the HTTP server and background tasks.

        Returns the actual port the server is listening on (may differ from
        *port* if that port was busy).
        """
        actual_port = find_free_port(port)

        app = aiohttp.web.Application(middlewares=[_localhost_only_middleware])
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/live", self._handle_ws)

        app.on_startup.append(self._on_startup)
        app.on_shutdown.append(self._on_shutdown)

        self._runner = aiohttp.web.AppRunner(app)
        await self._runner.setup()

        site = aiohttp.web.TCPSite(self._runner, host, actual_port)
        await site.start()

        logger.info("tailtest dashboard listening on http://%s:%d", host, actual_port)
        return actual_port

    async def stop(self) -> None:
        """Shut down background tasks and the HTTP server cleanly."""
        # Cancel background tasks.
        for task in (self._watcher_task, self._ping_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

        # Close all open WebSocket connections.
        if self._ws_clients:
            await asyncio.gather(
                *(ws.close() for ws in list(self._ws_clients)),
                return_exceptions=True,
            )
        self._ws_clients.clear()

        # Shut down the aiohttp runner (closes the HTTP server).
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    # ------------------------------------------------------------------
    # aiohttp lifecycle hooks
    # ------------------------------------------------------------------

    async def _on_startup(self, _app: aiohttp.web.Application) -> None:
        """Start background tasks when the server is ready."""
        self._watcher_task = asyncio.create_task(self._watch_events(), name="dashboard-watcher")
        self._ping_task = asyncio.create_task(self._ping_loop(), name="dashboard-ping")

    async def _on_shutdown(self, _app: aiohttp.web.Application) -> None:
        """Cancel background tasks on server shutdown."""
        for task in (self._watcher_task, self._ping_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    # ------------------------------------------------------------------
    # Route handlers
    # ------------------------------------------------------------------

    async def _handle_index(self, _request: aiohttp.web.Request) -> aiohttp.web.Response:
        """GET / -- placeholder HTML page."""
        return aiohttp.web.Response(
            text=_PLACEHOLDER_HTML,
            content_type="text/html",
        )

    async def _handle_ws(self, request: aiohttp.web.Request) -> aiohttp.web.WebSocketResponse:
        """WS /live -- upgrade and register the client."""
        ws = aiohttp.web.WebSocketResponse()
        await ws.prepare(request)

        self._ws_clients.add(ws)
        logger.debug("WebSocket client connected (total: %d)", len(self._ws_clients))

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.ERROR:
                    logger.debug("WebSocket error: %s", ws.exception())
                    break
                # Ignore incoming messages for now — clients are receive-only.
        finally:
            self._ws_clients.discard(ws)
            logger.debug("WebSocket client disconnected (remaining: %d)", len(self._ws_clients))

        return ws

    # ------------------------------------------------------------------
    # Background tasks
    # ------------------------------------------------------------------

    async def _watch_events(self) -> None:
        """Watch events.jsonl for new lines and broadcast them to clients.

        Handles the case where the file does not yet exist by watching the
        parent directory (``watchfiles`` emits a change when the file is
        created).
        """
        # Seed the known size if the file already exists.
        if self._events_path.exists():
            self._known_size = self._events_path.stat().st_size

        # Watch the parent directory so we notice when the file is created.
        watch_target = self._events_path.parent
        watch_target.mkdir(parents=True, exist_ok=True)

        try:
            async for _changes in watchfiles.awatch(watch_target):
                if not self._events_path.exists():
                    continue
                await self._flush_new_lines()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Unexpected error in event watcher")

    async def _flush_new_lines(self) -> None:
        """Read any new lines appended to events.jsonl and push to clients."""
        try:
            current_size = self._events_path.stat().st_size
        except OSError:
            return

        if current_size <= self._known_size:
            # File was truncated or unchanged; reset cursor.
            self._known_size = current_size
            return

        try:
            with self._events_path.open("rb") as fp:
                fp.seek(self._known_size)
                new_bytes = fp.read(current_size - self._known_size)
        except OSError:
            return

        self._known_size = current_size

        for raw_line in new_bytes.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed event line: %r", line[:120])
                continue

            message = json.dumps({"kind": "event", "payload": payload})
            await self._broadcast(message)

    async def _ping_loop(self) -> None:
        """Send a ping message to all connected clients every 30 seconds."""
        try:
            while True:
                await asyncio.sleep(_PING_INTERVAL)
                await self._broadcast(json.dumps({"kind": "ping"}))
        except asyncio.CancelledError:
            pass

    async def _broadcast(self, message: str) -> None:
        """Send *message* to every connected WebSocket client.

        Stale connections are removed from the registry on send failure.
        """
        if not self._ws_clients:
            return

        dead: set[aiohttp.web.WebSocketResponse] = set()
        for ws in list(self._ws_clients):
            if ws.closed:
                dead.add(ws)
                continue
            try:
                await ws.send_str(message)
            except Exception:
                logger.debug("Failed to send to WebSocket client; removing")
                dead.add(ws)

        self._ws_clients -= dead


# ------------------------------------------------------------------
# Module-level entry point
# ------------------------------------------------------------------


def run_server(
    tailtest_dir: Path,
    host: str = "127.0.0.1",
    port: int = 7777,
) -> None:
    """Start the dashboard server and block until interrupted.

    Handles SIGINT / SIGTERM for graceful shutdown. Exits with code 0.

    Parameters
    ----------
    tailtest_dir:
        Path to the project's ``.tailtest/`` directory.
    host:
        Interface to bind. Defaults to ``127.0.0.1``.
    port:
        Preferred port. If taken, the next free port within
        :data:`_PORT_RETRY_LIMIT` attempts is used.
    """

    async def _run() -> None:
        server = DashboardServer(tailtest_dir)
        loop = asyncio.get_running_loop()

        stop_event = asyncio.Event()

        def _signal_handler() -> None:
            logger.info("Shutdown signal received")
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

        actual_port = await server.start(host=host, port=port)
        logger.info("Dashboard ready at http://%s:%d", host, actual_port)

        try:
            await stop_event.wait()
        finally:
            logger.info("Shutting down dashboard server…")
            await server.stop()
            logger.info("Dashboard server stopped")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    asyncio.run(_run())
