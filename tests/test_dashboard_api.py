"""Tests for the dashboard REST API routes (Phase 4 Task 4.3)."""

from __future__ import annotations

import json
from pathlib import Path

import aiohttp.web
import pytest
from aiohttp.test_utils import TestClient, TestServer

from tailtest.core.events.schema import Event, EventKind
from tailtest.core.events.writer import EventWriter
from tailtest.core.findings.schema import Finding, FindingBatch, FindingKind, Severity
from tailtest.core.scan.profile import ProjectProfile, ScanStatus
from tailtest.dashboard.server import DashboardServer, _localhost_only_middleware

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_server(tailtest_dir: Path) -> TestServer:
    """Create a TestServer wrapping a DashboardServer's aiohttp app."""
    server_obj = DashboardServer(tailtest_dir)
    app = aiohttp.web.Application(middlewares=[_localhost_only_middleware])
    app.router.add_get("/api/status", server_obj._handle_api_status)
    app.router.add_get("/api/findings", server_obj._handle_api_findings)
    app.router.add_get("/api/events", server_obj._handle_api_events)
    app.router.add_get("/api/coverage", server_obj._handle_api_coverage)
    app.router.add_post("/api/accept/{recommendation_id}", server_obj._handle_api_accept)
    app.router.add_post("/api/dismiss/{finding_id}", server_obj._handle_api_dismiss)
    return TestServer(app)


def _write_profile(tailtest_dir: Path, profile: ProjectProfile) -> None:
    tailtest_dir.mkdir(parents=True, exist_ok=True)
    (tailtest_dir / "profile.json").write_text(profile.to_json(), encoding="utf-8")


def _write_batch(tailtest_dir: Path, batch: FindingBatch) -> None:
    reports_dir = tailtest_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "latest.json").write_text(batch.model_dump_json(), encoding="utf-8")


def _make_minimal_batch(findings: list[Finding] | None = None) -> FindingBatch:
    return FindingBatch(
        run_id="run-test",
        depth="standard",
        findings=findings or [],
    )


def _make_finding(kind: FindingKind = FindingKind.LINT) -> Finding:
    return Finding.create(
        kind=kind,
        severity=Severity.LOW,
        file=Path("src/foo.py"),
        line=1,
        message="test finding",
        run_id="run-test",
    )


# ---------------------------------------------------------------------------
# test_api_status_returns_json
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_status_returns_json(tmp_path: Path) -> None:
    """GET /api/status returns JSON with 'profile' and 'config' keys when profile exists."""
    tailtest_dir = tmp_path / ".tailtest"
    profile = ProjectProfile(root=tmp_path, scan_status=ScanStatus.OK)
    _write_profile(tailtest_dir, profile)

    async with TestClient(_make_server(tailtest_dir)) as client:
        resp = await client.get("/api/status")
        assert resp.status == 200
        assert resp.content_type == "application/json"
        data = await resp.json()
        assert "profile" in data
        assert "config" in data
        assert "baseline_count" in data
        assert data["profile"] is not None
        assert "depth" in data["config"]
        assert "ai_checks_enabled" in data["config"]


# ---------------------------------------------------------------------------
# test_api_status_when_no_profile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_status_when_no_profile(tmp_path: Path) -> None:
    """GET /api/status returns profile: null when no profile.json exists."""
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()

    async with TestClient(_make_server(tailtest_dir)) as client:
        resp = await client.get("/api/status")
        assert resp.status == 200
        data = await resp.json()
        assert data["profile"] is None
        assert data["baseline_count"] == 0


# ---------------------------------------------------------------------------
# test_api_findings_empty_when_no_report
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_findings_empty_when_no_report(tmp_path: Path) -> None:
    """GET /api/findings returns empty list when latest.json does not exist."""
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()

    async with TestClient(_make_server(tailtest_dir)) as client:
        resp = await client.get("/api/findings")
        assert resp.status == 200
        data = await resp.json()
        assert data == {"findings": [], "total": 0}


# ---------------------------------------------------------------------------
# test_api_findings_returns_findings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_findings_returns_findings(tmp_path: Path) -> None:
    """GET /api/findings returns findings from latest.json."""
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()
    findings = [_make_finding(FindingKind.LINT), _make_finding(FindingKind.SAST)]
    _write_batch(tailtest_dir, _make_minimal_batch(findings))

    async with TestClient(_make_server(tailtest_dir)) as client:
        resp = await client.get("/api/findings")
        assert resp.status == 200
        data = await resp.json()
        assert data["total"] == 2
        assert len(data["findings"]) == 2


# ---------------------------------------------------------------------------
# test_api_findings_kind_filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_findings_kind_filter(tmp_path: Path) -> None:
    """GET /api/findings?kind=lint returns only lint findings."""
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()
    findings = [_make_finding(FindingKind.LINT), _make_finding(FindingKind.SAST)]
    _write_batch(tailtest_dir, _make_minimal_batch(findings))

    async with TestClient(_make_server(tailtest_dir)) as client:
        resp = await client.get("/api/findings?kind=lint")
        assert resp.status == 200
        data = await resp.json()
        assert data["total"] == 1
        assert data["findings"][0]["kind"] == "lint"


# ---------------------------------------------------------------------------
# test_api_events_returns_events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_events_returns_events(tmp_path: Path) -> None:
    """GET /api/events returns events from events.jsonl in newest-first order."""
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()

    writer = EventWriter(tailtest_dir)
    event1 = Event(session_id="s1", kind=EventKind.SESSION_START, payload={})
    event2 = Event(session_id="s1", kind=EventKind.EDIT, payload={"file": "foo.py"})
    writer.append(event1)
    writer.append(event2)

    async with TestClient(_make_server(tailtest_dir)) as client:
        resp = await client.get("/api/events")
        assert resp.status == 200
        data = await resp.json()
        assert "events" in data
        assert "total" in data
        assert data["total"] == 2
        # Newest-first: EDIT came after SESSION_START, so it appears first.
        assert data["events"][0]["kind"] == EventKind.EDIT.value
        assert data["events"][1]["kind"] == EventKind.SESSION_START.value


# ---------------------------------------------------------------------------
# test_api_events_empty_when_no_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_events_empty_when_no_file(tmp_path: Path) -> None:
    """GET /api/events returns empty list when events.jsonl does not exist."""
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()

    async with TestClient(_make_server(tailtest_dir)) as client:
        resp = await client.get("/api/events")
        assert resp.status == 200
        data = await resp.json()
        assert data == {"events": [], "total": 0}


# ---------------------------------------------------------------------------
# test_api_coverage_no_report
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_coverage_no_report(tmp_path: Path) -> None:
    """GET /api/coverage returns empty coverage when no report exists."""
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()

    async with TestClient(_make_server(tailtest_dir)) as client:
        resp = await client.get("/api/coverage")
        assert resp.status == 200
        data = await resp.json()
        assert data == {"coverage": {}}


# ---------------------------------------------------------------------------
# test_api_coverage_with_data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_coverage_with_data(tmp_path: Path) -> None:
    """GET /api/coverage returns delta_coverage_pct when present in batch."""
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()
    batch = FindingBatch(
        run_id="r1",
        depth="standard",
        delta_coverage_pct=87.5,
        uncovered_new_lines=[{"file": "src/foo.py", "line": 42}],
    )
    _write_batch(tailtest_dir, batch)

    async with TestClient(_make_server(tailtest_dir)) as client:
        resp = await client.get("/api/coverage")
        assert resp.status == 200
        data = await resp.json()
        assert data["coverage"]["delta_coverage_pct"] == 87.5
        assert len(data["coverage"]["uncovered_new_lines"]) == 1


# ---------------------------------------------------------------------------
# test_api_dismiss_creates_dismissed_entry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_dismiss_creates_dismissed_entry(tmp_path: Path) -> None:
    """POST /api/dismiss/<id> returns 200 with ok: true and writes to dismissed.json."""
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()

    async with TestClient(_make_server(tailtest_dir)) as client:
        resp = await client.post(
            "/api/dismiss/rec-abc",
            json={"days": 7},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True
        assert data["id"] == "rec-abc"

    # Verify the dismissal was written to disk.
    dismissed_path = tmp_path / ".tailtest" / "dismissed.json"
    assert dismissed_path.exists()
    stored = json.loads(dismissed_path.read_text())
    assert "rec-abc" in stored


# ---------------------------------------------------------------------------
# test_api_accept_creates_accepted_entry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_accept_creates_accepted_entry(tmp_path: Path) -> None:
    """POST /api/accept/<id> returns 200 with ok: true and writes to accepted_recs.json."""
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()

    async with TestClient(_make_server(tailtest_dir)) as client:
        resp = await client.post("/api/accept/rec-xyz")
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True
        assert data["id"] == "rec-xyz"

    accepted_path = tailtest_dir / "accepted_recs.json"
    assert accepted_path.exists()
    stored = json.loads(accepted_path.read_text())
    assert "rec-xyz" in stored


# ---------------------------------------------------------------------------
# test_api_error_uses_rfc7807_format
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_error_uses_rfc7807_format(tmp_path: Path) -> None:
    """A 500 error from an API route must use RFC 7807 Problem Details format."""
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()

    # Write a corrupt latest.json to force an error in /api/findings.
    reports_dir = tailtest_dir / "reports"
    reports_dir.mkdir()
    (reports_dir / "latest.json").write_text("THIS IS NOT JSON", encoding="utf-8")

    async with TestClient(_make_server(tailtest_dir)) as client:
        resp = await client.get("/api/findings")
        assert resp.status == 500
        assert resp.content_type == "application/problem+json"
        data = await resp.json()
        assert "type" in data
        assert "title" in data
        assert "status" in data
        assert data["status"] == 500
        assert data["type"] == "about:blank"
