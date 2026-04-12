"""Tests for profile staleness + structural change detection (Phase 3 Task 3.10).

Covers four scenarios:
1. Cache fresh -- no rescan: load_profile() returns a valid profile and
   is_cache_fresh() returns True. scan_shallow() must NOT be called and
   the structural change nudge must NOT appear.
2. Cache stale -- rescan + nudge: load_profile() returns a profile and
   is_cache_fresh() returns False. scan_shallow() IS called and the
   structural change nudge appears in the output.
3. No cached profile -- fresh scan: load_profile() returns None.
   scan_shallow() IS called and there is no structural change nudge.
4. Cache fresh but empty project: load_profile() returns a profile with
   no source files, is_cache_fresh() returns True. The "ready" message is
   used (not the structural change nudge).

All tests mock the scanner methods so no real filesystem scan is performed.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tailtest.hook.session_start import run as session_start_run

# --- Fixture helpers -------------------------------------------------------


def _get_context(result_stdout_json: str | None) -> str:
    """Return the SessionStart plain-text output."""
    assert result_stdout_json is not None
    return result_stdout_json  # SessionStart outputs plain text, not JSON


def _make_python_project(tmp_path: Path) -> None:
    """Write minimal Python project files so the config bootstrap succeeds."""
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("def main(): pass\n")
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')


def _make_mock_profile(*, empty: bool = False) -> MagicMock:
    """Return a MagicMock that looks like a ProjectProfile."""
    from tailtest.core.scan.profile import ScanStatus

    profile = MagicMock()
    profile.scan_status = ScanStatus.OK
    profile.content_hash = "abc123"
    profile.ai_checks_enabled = None
    profile.ai_surface = MagicMock()
    profile.likely_vibe_coded = False
    profile.frameworks_detected = []
    if empty:
        profile.primary_language = None
        profile.languages = {}
    else:
        profile.primary_language = "python"
        profile.languages = {"python": 5}
    # model_copy must return a similarly-shaped mock so the caller can
    # continue to use .scan_status, etc.
    profile.model_copy.return_value = profile
    return profile


# --- Test cases ------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_fresh_no_rescan(tmp_path: Path) -> None:
    """When the cache is fresh, scan_shallow() must not be called.

    Also verifies that the structural change nudge is absent from the output.
    """
    _make_python_project(tmp_path)
    cached_profile = _make_mock_profile()

    with (
        patch("tailtest.hook.session_start.ProjectScanner") as mock_scanner_cls,
        patch("tailtest.hook.session_start.RecommendationEngine") as mock_engine_cls,
    ):
        mock_scanner = MagicMock()
        mock_scanner.load_profile.return_value = cached_profile
        mock_scanner.is_cache_fresh.return_value = True
        mock_scanner_cls.return_value = mock_scanner

        mock_engine = MagicMock()
        mock_engine.compute.return_value = []
        mock_engine_cls.return_value = mock_engine

        result = await session_start_run('{"session_id": "s1"}', project_root=tmp_path)

    # scan_shallow must NOT have been called
    mock_scanner.scan_shallow.assert_not_called()
    # save_profile must NOT have been called (no new scan)
    mock_scanner.save_profile.assert_not_called()

    context = _get_context(result.stdout_json)
    assert "project structure changed" not in context
    assert "--deep" not in context


@pytest.mark.asyncio
async def test_cache_stale_rescan_and_nudge(tmp_path: Path) -> None:
    """When the cached hash differs, scan_shallow() is called and the nudge appears."""
    _make_python_project(tmp_path)
    cached_profile = _make_mock_profile()
    fresh_profile = _make_mock_profile()

    with (
        patch("tailtest.hook.session_start.ProjectScanner") as mock_scanner_cls,
        patch("tailtest.hook.session_start.RecommendationEngine") as mock_engine_cls,
    ):
        mock_scanner = MagicMock()
        mock_scanner.load_profile.return_value = cached_profile
        mock_scanner.is_cache_fresh.return_value = False
        mock_scanner.scan_shallow.return_value = fresh_profile
        mock_scanner_cls.return_value = mock_scanner

        mock_engine = MagicMock()
        mock_engine.compute.return_value = []
        mock_engine_cls.return_value = mock_engine

        result = await session_start_run('{"session_id": "s1"}', project_root=tmp_path)

    # scan_shallow must have been called to refresh the profile
    mock_scanner.scan_shallow.assert_called_once()
    # save_profile must have been called to persist the new profile
    mock_scanner.save_profile.assert_called_once()

    context = _get_context(result.stdout_json)
    assert "project structure changed" in context
    assert "--deep" in context


@pytest.mark.asyncio
async def test_no_cached_profile_fresh_scan_no_nudge(tmp_path: Path) -> None:
    """When load_profile() returns None, scan_shallow() is called and no nudge appears."""
    _make_python_project(tmp_path)
    new_profile = _make_mock_profile()

    with (
        patch("tailtest.hook.session_start.ProjectScanner") as mock_scanner_cls,
        patch("tailtest.hook.session_start.RecommendationEngine") as mock_engine_cls,
    ):
        mock_scanner = MagicMock()
        mock_scanner.load_profile.return_value = None
        mock_scanner.scan_shallow.return_value = new_profile
        mock_scanner_cls.return_value = mock_scanner

        mock_engine = MagicMock()
        mock_engine.compute.return_value = []
        mock_engine_cls.return_value = mock_engine

        result = await session_start_run('{"session_id": "s1"}', project_root=tmp_path)

    # scan_shallow must have been called (no cache to reuse)
    mock_scanner.scan_shallow.assert_called_once()

    context = _get_context(result.stdout_json)
    # No structural change nudge: this is a first scan, not a hash drift
    assert "project structure changed" not in context
    assert "--deep" not in context


@pytest.mark.asyncio
async def test_cache_fresh_empty_project_ready_message(tmp_path: Path) -> None:
    """Cache fresh + empty project -> 'ready' message, no structural nudge."""
    # Do NOT create source files -- the project is empty.
    cached_profile = _make_mock_profile(empty=True)

    with (
        patch("tailtest.hook.session_start.ProjectScanner") as mock_scanner_cls,
        patch("tailtest.hook.session_start.RecommendationEngine") as mock_engine_cls,
    ):
        mock_scanner = MagicMock()
        mock_scanner.load_profile.return_value = cached_profile
        mock_scanner.is_cache_fresh.return_value = True
        mock_scanner_cls.return_value = mock_scanner

        mock_engine = MagicMock()
        mock_engine.compute.return_value = []
        mock_engine_cls.return_value = mock_engine

        result = await session_start_run('{"session_id": "s1"}', project_root=tmp_path)

    # scan_shallow must NOT have been called
    mock_scanner.scan_shallow.assert_not_called()

    context = _get_context(result.stdout_json)
    assert "ready" in context.lower()
    assert "code to test" in context.lower()
    assert "project structure changed" not in context
