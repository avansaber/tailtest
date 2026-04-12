"""Tests for Phase 3 Task 3.7: vibe-coded-repo detection UX integration.

Covers:
1. SessionStart with likely_vibe_coded=True -> warmer phrasing
2. SessionStart with likely_vibe_coded=False -> neutral phrasing
3. SessionStart with likely_vibe_coded=None -> neutral phrasing
4. PostToolUse: vibe-coded + .py file with def + 0 tests -> gen offer appended
5. PostToolUse: vibe-coded + .py file + tests ran -> NO gen offer
6. PostToolUse: non-vibe-coded -> NO gen offer regardless
7. PostToolUse: gen offer only fires once per file (second call -> no second offer)
8. RecommendationEngine: vibe-coded -> add_test recs sorted before other same-priority recs
9. RecommendationEngine: non-vibe-coded -> standard priority sort (add_test not promoted)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tailtest.core.recommendations.schema import (
    Recommendation,
    RecommendationKind,
    RecommendationPriority,
)
from tailtest.core.recommender.engine import RecommendationEngine
from tailtest.core.scan.profile import (
    AISurface,
    DirectoryClassification,
    ProjectProfile,
    ScanStatus,
)
from tailtest.hook.session_start import run as session_start_run

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_profile(
    root: Path,
    *,
    likely_vibe_coded: bool | None = False,
    runners_detected: list | None = None,
    test_dirs: list | None = None,
    recommendations: list | None = None,
) -> ProjectProfile:
    """Build a minimal ProjectProfile for testing."""
    directories = DirectoryClassification(
        tests=test_dirs or [],
    )
    profile = ProjectProfile(
        root=root,
        primary_language="python",
        languages={"python": 5},
        scan_status=ScanStatus.OK,
        likely_vibe_coded=likely_vibe_coded if likely_vibe_coded is not None else False,
        runners_detected=runners_detected or [],
        directories=directories,
        recommendations=recommendations or [],
    )
    return profile


def _make_python_project(tmp_path: Path) -> None:
    """Write minimal Python project files so the scan does not produce an empty profile."""
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("def main(): pass\n")
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')


# ---------------------------------------------------------------------------
# 1-3. SessionStart phrasing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_start_vibe_coded_uses_warmer_phrasing(tmp_path: Path) -> None:
    """likely_vibe_coded=True -> warm phrasing appears in the output."""
    _make_python_project(tmp_path)
    profile = _make_profile(tmp_path, likely_vibe_coded=True)

    with (
        patch("tailtest.hook.session_start.ProjectScanner") as mock_scanner_cls,
        patch("tailtest.hook.session_start.RecommendationEngine") as mock_engine_cls,
    ):
        mock_scanner = MagicMock()
        mock_scanner.load_profile.return_value = None  # no cache -> fresh scan
        mock_scanner.scan_shallow.return_value = profile
        mock_scanner.save_profile.return_value = None
        mock_scanner_cls.return_value = mock_scanner

        mock_engine = MagicMock()
        mock_engine.compute.return_value = []
        mock_engine_cls.return_value = mock_engine

        result = await session_start_run('{"session_id": "s1"}', project_root=tmp_path)

    assert result.stdout_json is not None
    message = result.stdout_json  # SessionStart outputs plain text
    assert "ready to help you test this" in message
    assert "/tailtest setup" in message


@pytest.mark.asyncio
async def test_session_start_non_vibe_coded_uses_neutral_phrasing(tmp_path: Path) -> None:
    """likely_vibe_coded=False -> neutral phrasing (no warm text)."""
    _make_python_project(tmp_path)
    profile = _make_profile(tmp_path, likely_vibe_coded=False)

    with (
        patch("tailtest.hook.session_start.ProjectScanner") as mock_scanner_cls,
        patch("tailtest.hook.session_start.RecommendationEngine") as mock_engine_cls,
    ):
        mock_scanner = MagicMock()
        mock_scanner.load_profile.return_value = None  # no cache -> fresh scan
        mock_scanner.scan_shallow.return_value = profile
        mock_scanner.save_profile.return_value = None
        mock_scanner_cls.return_value = mock_scanner

        mock_engine = MagicMock()
        mock_engine.compute.return_value = []
        mock_engine_cls.return_value = mock_engine

        result = await session_start_run('{"session_id": "s2"}', project_root=tmp_path)

    assert result.stdout_json is not None
    message = result.stdout_json  # SessionStart outputs plain text
    assert "ready to help you test this" not in message
    assert "initialized in" in message


@pytest.mark.asyncio
async def test_session_start_none_vibe_coded_uses_neutral_phrasing(tmp_path: Path) -> None:
    """likely_vibe_coded=None falls back to False -> neutral phrasing."""
    _make_python_project(tmp_path)
    # ProjectProfile field is bool, so None coerces to False. We use getattr
    # patching to simulate None being returned by getattr.
    profile = _make_profile(tmp_path, likely_vibe_coded=False)

    with (
        patch("tailtest.hook.session_start.ProjectScanner") as mock_scanner_cls,
        patch("tailtest.hook.session_start.RecommendationEngine") as mock_engine_cls,
    ):
        mock_scanner = MagicMock()
        # Simulate getattr returning None for likely_vibe_coded
        profile_mock = MagicMock(spec=profile)
        profile_mock.primary_language = "python"
        profile_mock.frameworks_detected = []
        profile_mock.scan_status = ScanStatus.OK
        profile_mock.likely_vibe_coded = None
        profile_mock.languages = {"python": 5}
        profile_mock.ai_surface = AISurface.NONE
        profile_mock.ai_checks_enabled = None
        mock_scanner.scan_shallow.return_value = profile_mock
        mock_scanner.save_profile.return_value = None
        mock_scanner.load_profile.return_value = profile_mock
        mock_scanner_cls.return_value = mock_scanner

        mock_engine = MagicMock()
        mock_engine.compute.return_value = []
        mock_engine_cls.return_value = mock_engine

        result = await session_start_run('{"session_id": "s3"}', project_root=tmp_path)

    assert result.stdout_json is not None
    message = result.stdout_json  # SessionStart outputs plain text
    assert "ready to help you test this" not in message


# ---------------------------------------------------------------------------
# 4-7. PostToolUse vibe gen offer
# ---------------------------------------------------------------------------


def _make_post_tool_use_payload(file_path: str) -> str:
    """Build a minimal PostToolUse payload for an Edit tool on file_path."""
    return json.dumps(
        {
            "tool_name": "Edit",
            "tool_input": {
                "file_path": file_path,
                "old_string": "pass",
                "new_string": "def foo():\n    pass",
            },
            "session_id": "test-session",
        }
    )


@pytest.mark.asyncio
async def test_post_tool_use_vibe_coded_py_no_tests_shows_gen_offer(tmp_path: Path) -> None:
    """Vibe-coded + .py file with def + no tests ran -> gen offer in output."""
    import tailtest.hook.post_tool_use as ptu_module

    ptu_module._gen_offered.clear()

    # Create a minimal Python file with a function definition.
    src_file = tmp_path / "app.py"
    src_file.write_text("def my_func():\n    pass\n")

    profile = _make_profile(tmp_path, likely_vibe_coded=True)

    with (
        patch("tailtest.hook.post_tool_use.ProjectScanner") as mock_scanner_cls,
        patch("tailtest.hook.post_tool_use._pick_runner_for_file") as mock_pick,
        patch("tailtest.hook.post_tool_use._maybe_surface_rec_line", return_value=None),
        patch("tailtest.hook.post_tool_use._maybe_build_ai_checks_note", return_value=None),
        patch("tailtest.hook.post_tool_use._collect_auto_offer_suggestions", return_value=[]),
        patch("tailtest.hook.post_tool_use.BaselineManager") as mock_baseline_cls,
        patch("tailtest.hook.post_tool_use._persist_report"),
    ):
        # Scanner: scan_shallow for manifest check; load_profile for vibe check
        mock_scanner = MagicMock()
        mock_scanner.load_profile.return_value = profile
        mock_scanner_cls.return_value = mock_scanner

        # Runner returns 0 tests passed/failed (no tests ran)
        from tailtest.core.findings.schema import FindingBatch

        empty_batch = FindingBatch(run_id="r1", depth="standard", tests_passed=0, tests_failed=0)
        mock_runner = MagicMock()
        mock_runner.language = "python"
        mock_runner.impacted = MagicMock(return_value=[])

        async def _fake_run(*args, **kwargs):
            return empty_batch

        mock_runner.run = _fake_run
        mock_pick.return_value = mock_runner

        mock_baseline = MagicMock()
        mock_baseline.apply_to.return_value = empty_batch
        mock_baseline_cls.return_value = mock_baseline

        payload = _make_post_tool_use_payload(str(src_file))
        result = await ptu_module.run(payload, project_root=tmp_path)

    assert result.stdout_json is not None
    envelope = json.loads(result.stdout_json)
    context = envelope["additionalContext"]
    assert "no tests found for this function" in context
    assert "/tailtest:gen" in context


@pytest.mark.asyncio
async def test_post_tool_use_vibe_coded_py_with_tests_no_gen_offer(tmp_path: Path) -> None:
    """Vibe-coded + .py file + tests ran -> no gen offer."""
    import tailtest.hook.post_tool_use as ptu_module

    ptu_module._gen_offered.clear()

    src_file = tmp_path / "app.py"
    src_file.write_text("def my_func():\n    pass\n")

    profile = _make_profile(tmp_path, likely_vibe_coded=True)

    with (
        patch("tailtest.hook.post_tool_use.ProjectScanner") as mock_scanner_cls,
        patch("tailtest.hook.post_tool_use._pick_runner_for_file") as mock_pick,
        patch("tailtest.hook.post_tool_use._maybe_surface_rec_line", return_value=None),
        patch("tailtest.hook.post_tool_use._maybe_build_ai_checks_note", return_value=None),
        patch("tailtest.hook.post_tool_use._collect_auto_offer_suggestions", return_value=[]),
        patch("tailtest.hook.post_tool_use.BaselineManager") as mock_baseline_cls,
        patch("tailtest.hook.post_tool_use._persist_report"),
    ):
        mock_scanner = MagicMock()
        mock_scanner.load_profile.return_value = profile
        mock_scanner_cls.return_value = mock_scanner

        from tailtest.core.findings.schema import FindingBatch

        # tests_passed=3 means tests ran
        batch_with_tests = FindingBatch(
            run_id="r2", depth="standard", tests_passed=3, tests_failed=0
        )
        mock_runner = MagicMock()
        mock_runner.language = "python"
        mock_runner.impacted = MagicMock(return_value=[])

        async def _fake_run(*args, **kwargs):
            return batch_with_tests

        mock_runner.run = _fake_run
        mock_pick.return_value = mock_runner

        mock_baseline = MagicMock()
        mock_baseline.apply_to.return_value = batch_with_tests
        mock_baseline_cls.return_value = mock_baseline

        payload = _make_post_tool_use_payload(str(src_file))
        result = await ptu_module.run(payload, project_root=tmp_path)

    assert result.stdout_json is not None
    envelope = json.loads(result.stdout_json)
    context = envelope["additionalContext"]
    assert "no tests found for this function" not in context


@pytest.mark.asyncio
async def test_post_tool_use_non_vibe_coded_no_gen_offer(tmp_path: Path) -> None:
    """Non-vibe-coded project -> no gen offer regardless of test count."""
    import tailtest.hook.post_tool_use as ptu_module

    ptu_module._gen_offered.clear()

    src_file = tmp_path / "app.py"
    src_file.write_text("def my_func():\n    pass\n")

    profile = _make_profile(tmp_path, likely_vibe_coded=False)

    with (
        patch("tailtest.hook.post_tool_use.ProjectScanner") as mock_scanner_cls,
        patch("tailtest.hook.post_tool_use._pick_runner_for_file") as mock_pick,
        patch("tailtest.hook.post_tool_use._maybe_surface_rec_line", return_value=None),
        patch("tailtest.hook.post_tool_use._maybe_build_ai_checks_note", return_value=None),
        patch("tailtest.hook.post_tool_use._collect_auto_offer_suggestions", return_value=[]),
        patch("tailtest.hook.post_tool_use.BaselineManager") as mock_baseline_cls,
        patch("tailtest.hook.post_tool_use._persist_report"),
    ):
        mock_scanner = MagicMock()
        mock_scanner.load_profile.return_value = profile
        mock_scanner_cls.return_value = mock_scanner

        from tailtest.core.findings.schema import FindingBatch

        empty_batch = FindingBatch(run_id="r3", depth="standard", tests_passed=0, tests_failed=0)
        mock_runner = MagicMock()
        mock_runner.language = "python"
        mock_runner.impacted = MagicMock(return_value=[])

        async def _fake_run(*args, **kwargs):
            return empty_batch

        mock_runner.run = _fake_run
        mock_pick.return_value = mock_runner

        mock_baseline = MagicMock()
        mock_baseline.apply_to.return_value = empty_batch
        mock_baseline_cls.return_value = mock_baseline

        payload = _make_post_tool_use_payload(str(src_file))
        result = await ptu_module.run(payload, project_root=tmp_path)

    assert result.stdout_json is not None
    envelope = json.loads(result.stdout_json)
    context = envelope["additionalContext"]
    assert "no tests found for this function" not in context


@pytest.mark.asyncio
async def test_post_tool_use_gen_offer_fires_once_per_file(tmp_path: Path) -> None:
    """Gen offer fires at most once per file per session."""
    import tailtest.hook.post_tool_use as ptu_module

    ptu_module._gen_offered.clear()

    src_file = tmp_path / "app.py"
    src_file.write_text("def my_func():\n    pass\n")

    profile = _make_profile(tmp_path, likely_vibe_coded=True)

    async def _run_once() -> str:
        with (
            patch("tailtest.hook.post_tool_use.ProjectScanner") as mock_scanner_cls,
            patch("tailtest.hook.post_tool_use._pick_runner_for_file") as mock_pick,
            patch("tailtest.hook.post_tool_use._maybe_surface_rec_line", return_value=None),
            patch("tailtest.hook.post_tool_use._maybe_build_ai_checks_note", return_value=None),
            patch("tailtest.hook.post_tool_use._collect_auto_offer_suggestions", return_value=[]),
            patch("tailtest.hook.post_tool_use.BaselineManager") as mock_baseline_cls,
            patch("tailtest.hook.post_tool_use._persist_report"),
        ):
            mock_scanner = MagicMock()
            mock_scanner.load_profile.return_value = profile
            mock_scanner_cls.return_value = mock_scanner

            from tailtest.core.findings.schema import FindingBatch

            empty_batch = FindingBatch(
                run_id="r4", depth="standard", tests_passed=0, tests_failed=0
            )
            mock_runner = MagicMock()
            mock_runner.language = "python"
            mock_runner.impacted = MagicMock(return_value=[])

            async def _fake_run(*args, **kwargs):
                return empty_batch

            mock_runner.run = _fake_run
            mock_pick.return_value = mock_runner

            mock_baseline = MagicMock()
            mock_baseline.apply_to.return_value = empty_batch
            mock_baseline_cls.return_value = mock_baseline

            payload = _make_post_tool_use_payload(str(src_file))
            result = await ptu_module.run(payload, project_root=tmp_path)
            assert result.stdout_json is not None
            return json.loads(result.stdout_json)["additionalContext"]

    # First call: offer fires
    context1 = await _run_once()
    assert "no tests found for this function" in context1

    # Second call with same file: offer does NOT fire again
    context2 = await _run_once()
    assert "no tests found for this function" not in context2


# ---------------------------------------------------------------------------
# 8-9. RecommendationEngine vibe-coded sort
# ---------------------------------------------------------------------------


def _make_profile_for_engine(
    root: Path,
    *,
    likely_vibe_coded: bool = False,
) -> ProjectProfile:
    return ProjectProfile(
        root=root,
        primary_language="python",
        languages={"python": 10},
        likely_vibe_coded=likely_vibe_coded,
        # No runners, no test dirs -> vibe_coder_test_gen rule fires
        runners_detected=[],
        directories=DirectoryClassification(tests=[]),
    )


def test_engine_vibe_coded_add_test_promoted(tmp_path: Path) -> None:
    """Vibe-coded profile: add_test recs sorted before other same-priority recs."""
    profile = _make_profile_for_engine(tmp_path, likely_vibe_coded=True)

    # Patch rules so we get a predictable mix: one high add_test + one high non-add_test.
    non_add_test_rec = Recommendation(
        kind=RecommendationKind.enable_ai_checks,
        priority=RecommendationPriority.high,
        title="Enable AI checks",
        why="Agent project.",
        next_step="Run /tailtest accept-ai-checks.",
    )
    add_test_rec = Recommendation(
        kind=RecommendationKind.add_test,
        priority=RecommendationPriority.high,
        title="Generate tests",
        why="Vibe-coded project.",
        next_step="Run /tailtest gen.",
    )

    engine = RecommendationEngine()
    with (
        patch.object(engine, "_rule_playwright", return_value=non_add_test_rec),
        patch.object(engine, "_rule_testcontainers", return_value=None),
        patch.object(engine, "_rule_db_fixtures", return_value=None),
        patch.object(engine, "_rule_enable_ai_checks", return_value=None),
        patch.object(engine, "_rule_vibe_coder_test_gen", return_value=add_test_rec),
        patch.object(engine, "_rule_sca_upgrade", return_value=None),
    ):
        results = engine.compute(profile)

    assert len(results) == 2
    # add_test should come first when vibe-coded
    assert results[0].kind == RecommendationKind.add_test
    assert results[1].kind == RecommendationKind.enable_ai_checks


def test_engine_non_vibe_coded_standard_sort(tmp_path: Path) -> None:
    """Non-vibe-coded profile: add_test not promoted; standard priority sort applies."""
    profile = _make_profile_for_engine(tmp_path, likely_vibe_coded=False)

    non_add_test_rec = Recommendation(
        kind=RecommendationKind.enable_ai_checks,
        priority=RecommendationPriority.high,
        title="Enable AI checks",
        why="Agent project.",
        next_step="Run /tailtest accept-ai-checks.",
    )
    add_test_rec = Recommendation(
        kind=RecommendationKind.add_test,
        priority=RecommendationPriority.high,
        title="Generate tests",
        why="Vibe-coded project.",
        next_step="Run /tailtest gen.",
    )

    engine = RecommendationEngine()
    # With non-vibe-coded, rule order determines output order (both same priority).
    # _rule_playwright fires first -> non_add_test_rec comes first.
    with (
        patch.object(engine, "_rule_playwright", return_value=non_add_test_rec),
        patch.object(engine, "_rule_testcontainers", return_value=None),
        patch.object(engine, "_rule_db_fixtures", return_value=None),
        patch.object(engine, "_rule_enable_ai_checks", return_value=None),
        patch.object(engine, "_rule_vibe_coder_test_gen", return_value=add_test_rec),
        patch.object(engine, "_rule_sca_upgrade", return_value=None),
    ):
        results = engine.compute(profile)

    assert len(results) == 2
    # Standard sort: both high priority, stable insert order preserved
    # non_add_test_rec was appended first so it stays first
    assert results[0].kind == RecommendationKind.enable_ai_checks
    assert results[1].kind == RecommendationKind.add_test
