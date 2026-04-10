"""Dashboard HTTP + WebSocket server (Phase 4 Tasks 4.2 and 4.3).

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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp.web
import watchfiles

from tailtest.core.baseline.manager import BaselineManager
from tailtest.core.config.loader import ConfigLoader
from tailtest.core.events.schema import EventKind
from tailtest.core.events.writer import EventWriter
from tailtest.core.findings.schema import FindingBatch
from tailtest.core.recommendations.store import DismissalStore
from tailtest.core.recommender.engine import RecommendationEngine
from tailtest.core.scan.scanner import ProjectScanner

logger = logging.getLogger("tailtest.dashboard")

_PING_INTERVAL = 30  # seconds
_PORT_RETRY_LIMIT = 10
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
        app.router.add_get("/static/dashboard.js", self._handle_static_js)
        app.router.add_get("/live", self._handle_ws)

        # REST API routes (Phase 4 Task 4.3)
        app.router.add_get("/api/status", self._handle_api_status)
        app.router.add_get("/api/findings", self._handle_api_findings)
        app.router.add_get("/api/events", self._handle_api_events)
        app.router.add_get("/api/coverage", self._handle_api_coverage)
        app.router.add_get("/api/recommendations", self._handle_api_recommendations)
        app.router.add_get("/api/timeline", self._handle_api_timeline)
        app.router.add_post("/api/accept/{recommendation_id}", self._handle_api_accept)
        app.router.add_post("/api/dismiss/{finding_id}", self._handle_api_dismiss)

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
        """GET / -- serve the dashboard SPA."""
        static_dir = Path(__file__).parent / "static"
        content = (static_dir / "index.html").read_text(encoding="utf-8")
        return aiohttp.web.Response(text=content, content_type="text/html")

    async def _handle_static_js(self, _request: aiohttp.web.Request) -> aiohttp.web.Response:
        """GET /static/dashboard.js -- serve the dashboard JavaScript."""
        static_dir = Path(__file__).parent / "static"
        content = (static_dir / "dashboard.js").read_text(encoding="utf-8")
        return aiohttp.web.Response(text=content, content_type="application/javascript")

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
    # REST API route handlers (Phase 4 Task 4.3)
    # ------------------------------------------------------------------

    def _problem(
        self,
        status: int,
        title: str,
        detail: str,
    ) -> aiohttp.web.Response:
        """Return an RFC 7807 Problem Details response."""
        body = {
            "type": "about:blank",
            "title": title,
            "status": status,
            "detail": detail,
        }
        return aiohttp.web.Response(
            text=json.dumps(body),
            status=status,
            content_type="application/problem+json",
        )

    async def _handle_api_status(self, _request: aiohttp.web.Request) -> aiohttp.web.Response:
        """GET /api/status -- project profile, config, and baseline count."""
        try:
            project_root = self._tailtest_dir.parent
            scanner = ProjectScanner(project_root)
            profile = scanner.load_profile(tailtest_dir=self._tailtest_dir)
            config = ConfigLoader(self._tailtest_dir).load()
            baseline_file = BaselineManager(self._tailtest_dir).load()
            baseline_count = len(baseline_file.entries)

            profile_dict: dict[str, Any] | None = None
            if profile is not None:
                profile_dict = json.loads(profile.to_json())

            data: dict[str, Any] = {
                "profile": profile_dict,
                "config": {
                    "depth": config.depth.value,
                    "ai_checks_enabled": config.ai_checks_enabled,
                },
                "baseline_count": baseline_count,
            }
            return aiohttp.web.json_response(data)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error in /api/status")
            return self._problem(500, "Internal Server Error", str(exc))

    async def _handle_api_findings(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        """GET /api/findings -- list findings from latest report.

        Query params:
          kind   -- filter by finding kind string (optional)
          since  -- ISO timestamp; only return findings on or after this time (optional)
          limit  -- max results (default 100)
        """
        try:
            report_path = self._tailtest_dir / "reports" / "latest.json"
            if not report_path.exists():
                return aiohttp.web.json_response({"findings": [], "total": 0})

            batch = FindingBatch.model_validate_json(report_path.read_text(encoding="utf-8"))
            findings = batch.findings

            kind_filter = request.rel_url.query.get("kind")
            if kind_filter:
                findings = [f for f in findings if f.kind.value == kind_filter]

            since_str = request.rel_url.query.get("since")
            if since_str:
                try:
                    since_dt = datetime.fromisoformat(since_str)
                    if since_dt.tzinfo is None:
                        since_dt = since_dt.replace(tzinfo=UTC)
                    findings = [f for f in findings if f.timestamp >= since_dt]
                except ValueError:
                    return self._problem(
                        400, "Bad Request", f"Invalid 'since' timestamp: {since_str!r}"
                    )

            limit_str = request.rel_url.query.get("limit", "100")
            try:
                limit = int(limit_str)
            except ValueError:
                return self._problem(400, "Bad Request", f"Invalid 'limit' value: {limit_str!r}")

            findings = findings[:limit]
            findings_data = [json.loads(f.model_dump_json()) for f in findings]

            return aiohttp.web.json_response(
                {"findings": findings_data, "total": len(findings_data)}
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error in /api/findings")
            return self._problem(500, "Internal Server Error", str(exc))

    async def _handle_api_events(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        """GET /api/events -- list events from events.jsonl.

        Query params:
          since  -- ISO timestamp; only return events on or after this time (optional)
          limit  -- max results (default 100)
        """
        try:
            events = EventWriter(self._tailtest_dir).read_all()

            # Newest-first order.
            events = list(reversed(events))

            since_str = request.rel_url.query.get("since")
            if since_str:
                try:
                    since_dt = datetime.fromisoformat(since_str)
                    if since_dt.tzinfo is None:
                        since_dt = since_dt.replace(tzinfo=UTC)
                    events = [e for e in events if e.timestamp >= since_dt]
                except ValueError:
                    return self._problem(
                        400, "Bad Request", f"Invalid 'since' timestamp: {since_str!r}"
                    )

            limit_str = request.rel_url.query.get("limit", "100")
            try:
                limit = int(limit_str)
            except ValueError:
                return self._problem(400, "Bad Request", f"Invalid 'limit' value: {limit_str!r}")

            events = events[:limit]
            events_data = [json.loads(e.model_dump_json()) for e in events]

            return aiohttp.web.json_response({"events": events_data, "total": len(events_data)})
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error in /api/events")
            return self._problem(500, "Internal Server Error", str(exc))

    async def _handle_api_coverage(self, _request: aiohttp.web.Request) -> aiohttp.web.Response:
        """GET /api/coverage -- delta coverage data from latest report."""
        try:
            report_path = self._tailtest_dir / "reports" / "latest.json"
            if not report_path.exists():
                return aiohttp.web.json_response({"coverage": {}})

            batch = FindingBatch.model_validate_json(report_path.read_text(encoding="utf-8"))
            coverage: dict[str, Any] = {}
            if batch.delta_coverage_pct is not None:
                coverage["delta_coverage_pct"] = batch.delta_coverage_pct
            if batch.uncovered_new_lines:
                coverage["uncovered_new_lines"] = batch.uncovered_new_lines

            return aiohttp.web.json_response({"coverage": coverage})
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error in /api/coverage")
            return self._problem(500, "Internal Server Error", str(exc))

    async def _handle_api_recommendations(
        self, _request: aiohttp.web.Request
    ) -> aiohttp.web.Response:
        """GET /api/recommendations -- actionable recommendations for the project."""
        try:
            project_root = self._tailtest_dir.parent
            scanner = ProjectScanner(project_root)
            profile = scanner.load_profile(tailtest_dir=self._tailtest_dir)
            if profile is None:
                return aiohttp.web.json_response({"recommendations": []})

            engine = RecommendationEngine()
            recs = engine.compute(profile)

            # Apply stored dismissals so is_dismissed is accurate.
            store = DismissalStore(project_root)
            recs = store.apply(recs)

            recs_data = [
                {
                    "id": r.id,
                    "kind": r.kind.value,
                    "priority": r.priority.value,
                    "title": r.title,
                    "why": r.why,
                    "next_step": r.next_step,
                    "is_dismissed": r.is_dismissed,
                }
                for r in recs
            ]
            return aiohttp.web.json_response({"recommendations": recs_data})
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error in /api/recommendations")
            return self._problem(500, "Internal Server Error", str(exc))

    async def _handle_api_timeline(self, _request: aiohttp.web.Request) -> aiohttp.web.Response:
        """GET /api/timeline -- per-day pass/fail counts for the last 7 days."""
        try:
            events = EventWriter(self._tailtest_dir).read_all()

            # Filter to RUN events only.
            run_events = [e for e in events if e.kind == EventKind.RUN]

            # Group by UTC date, count passes and failures.
            # A RUN event's payload may have "passed" / "failed" counts, or
            # we infer from "result": "passed"|"failed".
            day_counts: dict[str, dict[str, int]] = {}
            for event in run_events:
                date_str = event.timestamp.strftime("%Y-%m-%d")
                if date_str not in day_counts:
                    day_counts[date_str] = {"passed": 0, "failed": 0}
                payload = event.payload
                # Try numeric fields first (tests_passed / tests_failed).
                passed = payload.get("tests_passed", 0)
                failed = payload.get("tests_failed", 0)
                # Fall back to result string.
                if passed == 0 and failed == 0:
                    result = str(payload.get("result", "")).lower()
                    if result == "passed":
                        passed = 1
                    elif result == "failed":
                        failed = 1
                    else:
                        # Unknown result -- count as one run, pass by default.
                        passed = 1
                day_counts[date_str]["passed"] += passed
                day_counts[date_str]["failed"] += failed

            # Keep only the last 7 days that have data, sorted chronologically.
            sorted_days = sorted(day_counts.keys())[-7:]
            days_data = [
                {
                    "date": d,
                    "passed": day_counts[d]["passed"],
                    "failed": day_counts[d]["failed"],
                }
                for d in sorted_days
            ]
            return aiohttp.web.json_response({"days": days_data})
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error in /api/timeline")
            return self._problem(500, "Internal Server Error", str(exc))

    async def _handle_api_accept(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        """POST /api/accept/{recommendation_id} -- mark a recommendation as accepted."""
        rec_id = request.match_info["recommendation_id"]
        try:
            accepted_path = self._tailtest_dir / "accepted_recs.json"
            accepted: list[str] = []
            if accepted_path.exists():
                try:
                    accepted = json.loads(accepted_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    accepted = []

            if rec_id not in accepted:
                accepted.append(rec_id)
                tmp = accepted_path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(accepted, indent=2), encoding="utf-8")
                tmp.replace(accepted_path)

            return aiohttp.web.json_response({"ok": True, "id": rec_id})
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error in /api/accept/%s", rec_id)
            return self._problem(500, "Internal Server Error", str(exc))

    async def _handle_api_dismiss(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        """POST /api/dismiss/{finding_id} -- dismiss a finding for N days.

        Body (optional JSON): {"days": 7}
        """
        finding_id = request.match_info["finding_id"]
        try:
            days = 7
            try:
                body_bytes = await request.read()
                if body_bytes.strip():
                    body = json.loads(body_bytes)
                    if isinstance(body, dict) and "days" in body:
                        days = int(body["days"])
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

            project_root = self._tailtest_dir.parent
            store = DismissalStore(project_root)
            until = datetime.now(UTC) + timedelta(days=days)
            store.dismiss(finding_id, until)

            return aiohttp.web.json_response({"ok": True, "id": finding_id})
        except Exception as exc:  # noqa: BLE001
            logger.exception("Error in /api/dismiss/%s", finding_id)
            return self._problem(500, "Internal Server Error", str(exc))

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
