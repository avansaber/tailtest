"""Tests for ProjectScanner + detectors (Phase 1 Task 1.12a + Phase 3 Task 3.1)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tailtest.core.scan import (
    AIConfidence,
    AISurface,
    InfrastructureKind,
    PlanFileKind,
    ProjectScanner,
    ScanStatus,
    detectors,
)
from tailtest.core.scan.scanner import DeepScanResult, ScanRecommendation

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE_PYTHON_AI = FIXTURES / "scanner_python_ai"
FIXTURE_TS_AI = FIXTURES / "scanner_typescript_ai"
FIXTURE_PLAIN = FIXTURES / "scanner_plain"


# --- Unit tests: detectors (language, framework, plan files, content hash) ---


def test_walk_project_respects_ignored_dirs(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "module.py").write_text("x = 1")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("junk")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("junk")

    files, hit_ceiling = detectors.walk_project(tmp_path)
    names = {f.name for f in files}
    assert "module.py" in names
    assert "junk.js" not in names
    assert "config" not in names
    assert hit_ceiling is False


def test_walk_project_hits_ceiling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Patch the ceiling low for this test
    monkeypatch.setattr(detectors, "MAX_FILES_WALKED", 5)
    for i in range(20):
        (tmp_path / f"file_{i}.py").write_text("")
    files, hit_ceiling = detectors.walk_project(tmp_path)
    assert hit_ceiling is True
    assert len(files) == 5


def test_detect_languages_simple() -> None:
    files = [
        Path("a.py"),
        Path("b.py"),
        Path("c.ts"),
        Path("d.rs"),
        Path("e.md"),  # not a language
    ]
    counts, primary = detectors.detect_languages(files)
    assert counts == {"python": 2, "typescript": 1, "rust": 1}
    assert primary == "python"


def test_detect_languages_empty() -> None:
    counts, primary = detectors.detect_languages([])
    assert counts == {}
    assert primary is None


def test_detect_python_frameworks(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "x"
version = "0.0.1"
dependencies = [
    "anthropic>=0.30",
    "fastapi",
    "click >= 8",
]
        """.strip()
    )
    frameworks = detectors.detect_frameworks(tmp_path)
    names = {f.name for f in frameworks}
    assert "anthropic-sdk" in names
    assert "fastapi" in names
    # click is not in our high-signal signature list, so it should NOT appear
    assert "click" not in names


def test_detect_js_frameworks(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {
                    "@anthropic-ai/sdk": "^0.30.0",
                    "next": "^14.0.0",
                    "react": "^18.0.0",
                    "lodash": "^4.0.0",
                },
                "devDependencies": {
                    "vitest": "^1.0.0",
                },
            }
        )
    )
    frameworks = detectors.detect_frameworks(tmp_path)
    names = {f.name for f in frameworks}
    assert "anthropic-sdk" in names
    assert "nextjs" in names
    assert "react" in names
    assert "vitest" in names
    # lodash is not in signatures, should be absent
    assert "lodash" not in names


def test_detect_plan_files(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("instructions")
    (tmp_path / "README.md").write_text("readme")
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".cursor" / "rules.md").write_text("rules")
    (tmp_path / ".claude").mkdir()

    plan_files = detectors.detect_plan_files(tmp_path)
    kinds = {p.kind for p in plan_files}
    assert PlanFileKind.CLAUDE_CODE_INSTRUCTIONS in kinds
    assert PlanFileKind.README in kinds
    assert PlanFileKind.CURSOR_RULES in kinds
    assert PlanFileKind.CLAUDE_CONFIG in kinds


def test_detect_infrastructure_docker(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM python:3.11")
    (tmp_path / "docker-compose.yml").write_text("version: '3'")
    infra = detectors.detect_infrastructure(tmp_path)
    kinds = {i.kind for i in infra}
    assert InfrastructureKind.DOCKER in kinds
    assert InfrastructureKind.DOCKER_COMPOSE in kinds


def test_detect_infrastructure_ci_github(tmp_path: Path) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("name: CI")
    infra = detectors.detect_infrastructure(tmp_path)
    assert any(i.kind == InfrastructureKind.CI for i in infra)


def test_classify_directories(tmp_path: Path) -> None:
    for name in ("src", "tests", "docs", "examples", "dist", "node_modules"):
        (tmp_path / name).mkdir()
    dc = detectors.classify_directories(tmp_path)
    assert any(p.name == "src" for p in dc.source)
    assert any(p.name == "tests" for p in dc.tests)
    assert any(p.name == "docs" for p in dc.docs)
    assert any(p.name == "examples" for p in dc.examples)
    assert any(p.name == "dist" for p in dc.generated)
    assert any(p.name == "node_modules" for p in dc.ignored)


def test_compute_likely_vibe_coded_true(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("x")
    plan_files = detectors.detect_plan_files(tmp_path)
    likely, signals = detectors.compute_likely_vibe_coded(plan_files)
    assert likely is True
    assert any("claude-code-instructions" in s for s in signals)


def test_compute_likely_vibe_coded_false_with_only_readme(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("x")
    plan_files = detectors.detect_plan_files(tmp_path)
    likely, signals = detectors.compute_likely_vibe_coded(plan_files)
    assert likely is False
    assert signals == []


def test_content_hash_stable(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    a = detectors.compute_content_hash(tmp_path)
    b = detectors.compute_content_hash(tmp_path)
    assert a == b


def test_content_hash_changes_on_manifest_edit(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    a = detectors.compute_content_hash(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x2'\n")
    b = detectors.compute_content_hash(tmp_path)
    assert a != b


def test_content_hash_changes_on_new_manifest(tmp_path: Path) -> None:
    a = detectors.compute_content_hash(tmp_path)
    (tmp_path / "Dockerfile").write_text("FROM python")
    b = detectors.compute_content_hash(tmp_path)
    assert a != b


# --- End-to-end scanner tests against fixture projects ------------------


def test_scanner_on_python_ai_fixture() -> None:
    scanner = ProjectScanner(FIXTURE_PYTHON_AI)
    profile = scanner.scan_shallow()

    assert profile.scan_status == ScanStatus.OK
    assert profile.primary_language == "python"
    assert "python" in profile.languages
    assert profile.languages["python"] >= 2  # at least src/__init__ and tests/test_*

    # Frameworks: anthropic-sdk + fastapi should be detected from pyproject
    framework_names = {f.name for f in profile.frameworks_detected}
    assert "anthropic-sdk" in framework_names
    assert "fastapi" in framework_names

    # Runner: pytest (via pytest config in pyproject)
    assert profile.has_runner("python")
    assert any(r.name == "pytest" for r in profile.runners_detected)

    # Infrastructure: Dockerfile
    infra_kinds = {i.kind for i in profile.infrastructure_detected}
    assert InfrastructureKind.DOCKER in infra_kinds

    # Plan files: CLAUDE.md + AGENTS.md + README.md
    plan_kinds = {p.kind for p in profile.plan_files_detected}
    assert PlanFileKind.CLAUDE_CODE_INSTRUCTIONS in plan_kinds
    assert PlanFileKind.AGENT_INVENTORY in plan_kinds
    assert PlanFileKind.README in plan_kinds

    # AI surface: anthropic SDK = utility at minimum; combined with system
    # prompt + imports it should elevate to agent with at least medium confidence.
    assert profile.ai_surface in (AISurface.AGENT, AISurface.UTILITY)
    # With an explicit system_prompt + import, we expect at least medium confidence
    assert profile.ai_confidence in (AIConfidence.HIGH, AIConfidence.MEDIUM)

    # Vibe-coded: True because of CLAUDE.md + AGENTS.md
    assert profile.likely_vibe_coded is True

    # Performance: under 5 seconds (massively — this fixture is tiny)
    assert profile.scan_duration_ms < 2000.0


def test_scanner_on_typescript_ai_fixture() -> None:
    scanner = ProjectScanner(FIXTURE_TS_AI)
    profile = scanner.scan_shallow()

    assert profile.scan_status == ScanStatus.OK
    assert profile.primary_language == "typescript"

    # Frameworks: @anthropic-ai/sdk + next + react + vitest
    framework_names = {f.name for f in profile.frameworks_detected}
    assert "anthropic-sdk" in framework_names
    assert "nextjs" in framework_names
    assert "react" in framework_names

    # Runner: vitest (declared in devDependencies)
    assert any(r.name == "vitest" for r in profile.runners_detected)

    # AI surface: anthropic SDK + system prompt + imports → agent
    assert profile.ai_surface == AISurface.AGENT

    # Vibe-coded: True because of AGENTS.md
    assert profile.likely_vibe_coded is True


def test_scanner_on_plain_fixture() -> None:
    scanner = ProjectScanner(FIXTURE_PLAIN)
    profile = scanner.scan_shallow()

    assert profile.scan_status == ScanStatus.OK
    assert profile.primary_language == "python"

    # No AI
    assert profile.ai_surface == AISurface.NONE

    # No plan files → not vibe-coded
    assert profile.likely_vibe_coded is False

    # No frameworks from our signature list (click isn't in the list)
    assert profile.frameworks_detected == []


def test_scanner_on_empty_directory(tmp_path: Path) -> None:
    """Scanning an empty directory should not crash."""
    scanner = ProjectScanner(tmp_path)
    profile = scanner.scan_shallow()
    assert profile.scan_status == ScanStatus.OK
    assert profile.primary_language is None
    assert profile.total_files_walked == 0
    assert profile.ai_surface == AISurface.NONE
    assert profile.likely_vibe_coded is False


def test_scanner_handles_permission_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If walk_project raises, the scanner should return a FAILED profile, not crash."""

    def raising_walk(_root: Path) -> tuple[list[Path], bool]:
        raise PermissionError("denied")

    monkeypatch.setattr(detectors, "walk_project", raising_walk)
    scanner = ProjectScanner(tmp_path)
    profile = scanner.scan_shallow()
    assert profile.scan_status == ScanStatus.FAILED
    assert profile.scan_error is not None
    assert "PermissionError" in profile.scan_error


# --- Persistence --------------------------------------------------------


def test_save_and_load_profile(tmp_path: Path) -> None:
    scanner = ProjectScanner(FIXTURE_PLAIN)
    profile = scanner.scan_shallow()

    saved_path = scanner.save_profile(profile, tailtest_dir=tmp_path)
    assert saved_path == tmp_path / "profile.json"
    assert saved_path.exists()

    loaded = scanner.load_profile(tailtest_dir=tmp_path)
    assert loaded is not None
    assert loaded.primary_language == profile.primary_language
    assert loaded.total_files_walked == profile.total_files_walked


def test_load_profile_missing_returns_none(tmp_path: Path) -> None:
    scanner = ProjectScanner(tmp_path)
    assert scanner.load_profile(tailtest_dir=tmp_path / "nowhere") is None


def test_load_profile_corrupt_returns_none(tmp_path: Path) -> None:
    (tmp_path / "profile.json").write_text("{not valid json")
    scanner = ProjectScanner(tmp_path)
    assert scanner.load_profile(tailtest_dir=tmp_path) is None


def test_is_cache_fresh(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    scanner = ProjectScanner(tmp_path)
    profile = scanner.scan_shallow()
    assert scanner.is_cache_fresh(profile) is True

    # Modify the manifest -- cache should invalidate
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x2'\n")
    assert scanner.is_cache_fresh(profile) is False


# ---------------------------------------------------------------------------
# scan_deep() tests (Phase 3 Task 3.1)
# ---------------------------------------------------------------------------

_VALID_LLM_JSON = json.dumps({
    "summary": "A demo agent that answers questions using the Anthropic SDK.",
    "concerns": ["No tests found.", "No CI configuration detected."],
    "recommendations": [
        {
            "kind": "add_test",
            "priority": "high",
            "title": "Add unit tests",
            "why": "The project has no test coverage.",
            "next_step": "Run `pytest --cov` and add test_*.py files.",
        }
    ],
})

_CLAUDE_JSON_ENVELOPE = json.dumps({
    "type": "result",
    "result": _VALID_LLM_JSON,
    "total_cost_usd": 0.001,
})


def _make_mock_subprocess(stdout: bytes, returncode: int = 0):
    """Return a coroutine that yields a mock subprocess result."""
    mock_proc = AsyncMock()
    mock_proc.returncode = returncode
    mock_proc.communicate = AsyncMock(return_value=(stdout, b""))
    return mock_proc


@pytest.mark.asyncio
async def test_scan_deep_valid_response(tmp_path: Path) -> None:
    """Valid JSON response from the LLM populates DeepScanResult correctly."""
    scanner = ProjectScanner(tmp_path)
    mock_proc = _make_mock_subprocess(stdout=_CLAUDE_JSON_ENVELOPE.encode())

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await scanner.scan_deep()

    assert result is not None
    assert isinstance(result, DeepScanResult)
    assert "Anthropic SDK" in result.summary
    assert len(result.concerns) == 2
    assert len(result.recommendations) == 1
    assert result.recommendations[0].kind == "add_test"
    assert result.recommendations[0].priority == "high"
    assert result.cached is False


@pytest.mark.asyncio
async def test_scan_deep_invalid_json_returns_none(tmp_path: Path, caplog) -> None:
    """Non-JSON LLM response returns None and logs a warning."""
    scanner = ProjectScanner(tmp_path)
    bad_envelope = json.dumps({"type": "result", "result": "not json at all {{}}"})
    mock_proc = _make_mock_subprocess(stdout=bad_envelope.encode())

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        import logging
        with caplog.at_level(logging.WARNING, logger="tailtest.core.scan.scanner"):
            result = await scanner.scan_deep()

    assert result is None


@pytest.mark.asyncio
async def test_scan_deep_llm_unavailable_returns_none(tmp_path: Path) -> None:
    """FileNotFoundError (claude not on PATH) returns None without raising."""
    scanner = ProjectScanner(tmp_path)

    with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("claude")):
        result = await scanner.scan_deep()

    assert result is None


@pytest.mark.asyncio
async def test_scan_deep_nonzero_exit_returns_none(tmp_path: Path) -> None:
    """Non-zero exit code from the claude CLI returns None."""
    scanner = ProjectScanner(tmp_path)
    mock_proc = _make_mock_subprocess(stdout=b"", returncode=1)
    mock_proc.communicate = AsyncMock(return_value=(b"", b"some error"))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await scanner.scan_deep()

    assert result is None


@pytest.mark.asyncio
async def test_scan_deep_cache_hit_skips_llm(tmp_path: Path) -> None:
    """When a fresh cache file exists, the LLM is not called."""
    scanner = ProjectScanner(tmp_path)
    cache_dir = tmp_path / ".tailtest" / "cache"
    cache_dir.mkdir(parents=True)

    # Pre-populate a cache file with a recent mtime.
    cached_data = {
        "summary": "Cached summary.",
        "concerns": ["Cached concern."],
        "recommendations": [],
        "content_hash": "abc123",
    }
    # We need to know what cache key the scanner will compute.
    # Use the scanner's own method to compute it.
    _, gathered = scanner._gather_context()
    key = scanner._compute_deep_cache_key(gathered)
    cache_file = cache_dir / f"deep_scan_{key[:16]}.json"
    cache_file.write_text(json.dumps(cached_data), encoding="utf-8")

    call_count = 0

    async def mock_exec(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return _make_mock_subprocess(stdout=_CLAUDE_JSON_ENVELOPE.encode())

    with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
        result = await scanner.scan_deep()

    assert result is not None
    assert result.summary == "Cached summary."
    assert result.cached is True
    assert call_count == 0, "LLM was called despite a valid cache hit"


@pytest.mark.asyncio
async def test_scan_deep_cache_miss_calls_llm(tmp_path: Path) -> None:
    """When no cache file exists, the LLM is called and the result is cached."""
    scanner = ProjectScanner(tmp_path)
    mock_proc = _make_mock_subprocess(stdout=_CLAUDE_JSON_ENVELOPE.encode())

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await scanner.scan_deep()

    assert result is not None
    assert result.cached is False

    # Cache file should now exist.
    cache_dir = tmp_path / ".tailtest" / "cache"
    cache_files = list(cache_dir.glob("deep_scan_*.json"))
    assert len(cache_files) == 1
    cached = json.loads(cache_files[0].read_text())
    assert cached["summary"] == result.summary


@pytest.mark.asyncio
async def test_scan_deep_force_bypasses_cache(tmp_path: Path) -> None:
    """force=True causes the LLM to be called even when a valid cache exists."""
    scanner = ProjectScanner(tmp_path)
    cache_dir = tmp_path / ".tailtest" / "cache"
    cache_dir.mkdir(parents=True)

    cached_data = {
        "summary": "Stale cached summary.",
        "concerns": [],
        "recommendations": [],
        "content_hash": "stale",
    }
    _, gathered = scanner._gather_context()
    key = scanner._compute_deep_cache_key(gathered)
    cache_file = cache_dir / f"deep_scan_{key[:16]}.json"
    cache_file.write_text(json.dumps(cached_data), encoding="utf-8")

    mock_proc = _make_mock_subprocess(stdout=_CLAUDE_JSON_ENVELOPE.encode())

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await scanner.scan_deep(force=True)

    assert result is not None
    # The forced call should return the fresh LLM result, not the cached one.
    assert "Anthropic SDK" in result.summary
    assert result.cached is False


@pytest.mark.asyncio
async def test_scan_deep_writes_scan_md(tmp_path: Path) -> None:
    """scan_deep() writes .tailtest/scan.md on success."""
    scanner = ProjectScanner(tmp_path)
    mock_proc = _make_mock_subprocess(stdout=_CLAUDE_JSON_ENVELOPE.encode())

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await scanner.scan_deep()

    assert result is not None
    scan_md = tmp_path / ".tailtest" / "scan.md"
    assert scan_md.exists()
    content = scan_md.read_text()
    assert "# Project Deep Scan" in content
    assert result.summary in content


@pytest.mark.asyncio
async def test_scan_deep_expired_cache_calls_llm(tmp_path: Path) -> None:
    """A cache file older than 24 hours is ignored; LLM is called."""
    scanner = ProjectScanner(tmp_path)
    cache_dir = tmp_path / ".tailtest" / "cache"
    cache_dir.mkdir(parents=True)

    cached_data = {
        "summary": "Old cached summary.",
        "concerns": [],
        "recommendations": [],
        "content_hash": "old",
    }
    _, gathered = scanner._gather_context()
    key = scanner._compute_deep_cache_key(gathered)
    cache_file = cache_dir / f"deep_scan_{key[:16]}.json"
    cache_file.write_text(json.dumps(cached_data), encoding="utf-8")

    # Backdate the mtime by more than 24 hours.
    old_mtime = time.time() - (25 * 3600)
    import os
    os.utime(cache_file, (old_mtime, old_mtime))

    mock_proc = _make_mock_subprocess(stdout=_CLAUDE_JSON_ENVELOPE.encode())

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await scanner.scan_deep()

    assert result is not None
    assert "Anthropic SDK" in result.summary
    assert result.cached is False
