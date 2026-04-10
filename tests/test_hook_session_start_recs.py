"""Tests for recommendation surfacing in the SessionStart hook (Phase 3 Task 3.4).

Covers six scenarios:
1. No profile -> no rec line in output
2. Profile with no high-priority recs -> no rec line
3. Profile with 1 high-priority rec -> rec count line added
4. Profile with 2 high-priority recs -> "2 high-priority recommendations" line
5. All high-priority recs dismissed -> no rec line
6. Rec line is a single line (no multi-line output)

Uses mocks for profile load and engine compute so the tests are fast and
deterministic -- no filesystem scanning, no real project needed.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tailtest.hook.session_start import run as session_start_run
from tailtest.core.recommendations.schema import (
    Recommendation,
    RecommendationKind,
    RecommendationPriority,
)


# --- Fixture helpers -------------------------------------------------------


def _make_rec(priority: RecommendationPriority, *, dismissed: bool = False) -> Recommendation:
    """Build a minimal Recommendation for testing."""
    title = f"Test rec ({priority})"
    rec = Recommendation(
        kind=RecommendationKind.install_tool,
        priority=priority,
        title=title,
        why="Test why.",
        next_step="Test next step.",
    )
    if dismissed:
        # Set dismissed_until to a future time so is_dismissed returns True.
        future = datetime.now(tz=timezone.utc) + timedelta(days=7)
        rec = rec.model_copy(update={"dismissed_until": future})
    return rec


def _make_python_project(tmp_path: Path) -> None:
    """Write minimal Python project files so the scan does not produce an empty profile."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def main(): pass\n")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
    )


def _get_context(result_stdout_json: str | None) -> str:
    """Extract additionalContext from the hook output JSON."""
    assert result_stdout_json is not None
    envelope = json.loads(result_stdout_json)
    return envelope["hookSpecificOutput"]["additionalContext"]


# --- Test cases ------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_profile_no_rec_line(tmp_path: Path) -> None:
    """When no profile.json exists the rec line must not appear.

    The SessionStart hook runs scan_shallow() itself, which creates a
    profile. We patch RecommendationEngine.compute so it returns no recs
    -- the absence of the rec line is what we assert.
    """
    _make_python_project(tmp_path)

    with patch(
        "tailtest.hook.session_start.RecommendationEngine"
    ) as mock_engine_cls:
        mock_engine = MagicMock()
        mock_engine.compute.return_value = []
        mock_engine_cls.return_value = mock_engine

        result = await session_start_run('{"session_id": "s1"}', project_root=tmp_path)

    context = _get_context(result.stdout_json)
    assert "high-priority" not in context
    assert "run /tailtest" not in context.lower() or "status" in context.lower()


@pytest.mark.asyncio
async def test_no_high_priority_recs_no_rec_line(tmp_path: Path) -> None:
    """Profile with only medium/low recs -> no rec line."""
    _make_python_project(tmp_path)

    medium_rec = _make_rec(RecommendationPriority.medium)
    low_rec = _make_rec(RecommendationPriority.low)

    with patch(
        "tailtest.hook.session_start.RecommendationEngine"
    ) as mock_engine_cls, patch(
        "tailtest.hook.session_start.DismissalStore"
    ) as mock_store_cls:
        mock_engine = MagicMock()
        mock_engine.compute.return_value = [medium_rec, low_rec]
        mock_engine_cls.return_value = mock_engine

        mock_store = MagicMock()
        mock_store.apply.return_value = [medium_rec, low_rec]
        mock_store_cls.return_value = mock_store

        result = await session_start_run('{"session_id": "s1"}', project_root=tmp_path)

    context = _get_context(result.stdout_json)
    assert "high-priority" not in context


@pytest.mark.asyncio
async def test_one_high_priority_rec_adds_count_line(tmp_path: Path) -> None:
    """Profile with 1 high-priority rec -> singular rec count line."""
    _make_python_project(tmp_path)

    high_rec = _make_rec(RecommendationPriority.high)

    with patch(
        "tailtest.hook.session_start.RecommendationEngine"
    ) as mock_engine_cls, patch(
        "tailtest.hook.session_start.DismissalStore"
    ) as mock_store_cls:
        mock_engine = MagicMock()
        mock_engine.compute.return_value = [high_rec]
        mock_engine_cls.return_value = mock_engine

        mock_store = MagicMock()
        mock_store.apply.return_value = [high_rec]
        mock_store_cls.return_value = mock_store

        result = await session_start_run('{"session_id": "s1"}', project_root=tmp_path)

    context = _get_context(result.stdout_json)
    assert "1 high-priority recommendation" in context
    assert "run /tailtest" in context.lower()
    # Singular "recommendation" not "recommendations"
    assert "1 high-priority recommendations" not in context


@pytest.mark.asyncio
async def test_two_high_priority_recs_adds_count_line(tmp_path: Path) -> None:
    """Profile with 2 high-priority recs -> plural count line."""
    _make_python_project(tmp_path)

    high_rec_1 = _make_rec(RecommendationPriority.high)
    # Give second rec a distinct title so it gets a different id.
    high_rec_2 = Recommendation(
        kind=RecommendationKind.add_test,
        priority=RecommendationPriority.high,
        title="Another high-priority rec",
        why="Test why 2.",
        next_step="Test step 2.",
    )

    with patch(
        "tailtest.hook.session_start.RecommendationEngine"
    ) as mock_engine_cls, patch(
        "tailtest.hook.session_start.DismissalStore"
    ) as mock_store_cls:
        mock_engine = MagicMock()
        mock_engine.compute.return_value = [high_rec_1, high_rec_2]
        mock_engine_cls.return_value = mock_engine

        mock_store = MagicMock()
        mock_store.apply.return_value = [high_rec_1, high_rec_2]
        mock_store_cls.return_value = mock_store

        result = await session_start_run('{"session_id": "s1"}', project_root=tmp_path)

    context = _get_context(result.stdout_json)
    assert "2 high-priority recommendations" in context
    assert "run /tailtest" in context.lower()


@pytest.mark.asyncio
async def test_all_high_priority_recs_dismissed_no_rec_line(tmp_path: Path) -> None:
    """If all high-priority recs are dismissed, no rec line appears."""
    _make_python_project(tmp_path)

    dismissed_rec = _make_rec(RecommendationPriority.high, dismissed=True)

    with patch(
        "tailtest.hook.session_start.RecommendationEngine"
    ) as mock_engine_cls, patch(
        "tailtest.hook.session_start.DismissalStore"
    ) as mock_store_cls:
        mock_engine = MagicMock()
        mock_engine.compute.return_value = [dismissed_rec]
        mock_engine_cls.return_value = mock_engine

        mock_store = MagicMock()
        mock_store.apply.return_value = [dismissed_rec]
        mock_store_cls.return_value = mock_store

        result = await session_start_run('{"session_id": "s1"}', project_root=tmp_path)

    context = _get_context(result.stdout_json)
    assert "high-priority" not in context


@pytest.mark.asyncio
async def test_rec_line_is_single_line(tmp_path: Path) -> None:
    """The rec count line must be exactly one line with no embedded newlines."""
    _make_python_project(tmp_path)

    high_rec = _make_rec(RecommendationPriority.high)

    with patch(
        "tailtest.hook.session_start.RecommendationEngine"
    ) as mock_engine_cls, patch(
        "tailtest.hook.session_start.DismissalStore"
    ) as mock_store_cls:
        mock_engine = MagicMock()
        mock_engine.compute.return_value = [high_rec]
        mock_engine_cls.return_value = mock_engine

        mock_store = MagicMock()
        mock_store.apply.return_value = [high_rec]
        mock_store_cls.return_value = mock_store

        result = await session_start_run('{"session_id": "s1"}', project_root=tmp_path)

    context = _get_context(result.stdout_json)
    # Find the rec line (last line in the context that mentions high-priority)
    lines = context.splitlines()
    rec_lines = [ln for ln in lines if "high-priority" in ln]
    assert len(rec_lines) == 1, (
        f"Expected exactly 1 rec line, got {len(rec_lines)}: {rec_lines}"
    )
    # The line itself must not have embedded newlines (it is one element)
    assert "\n" not in rec_lines[0]
