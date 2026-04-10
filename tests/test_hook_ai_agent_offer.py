"""Tests for Task 3.5: AI-agent depth mode wiring.

Covers:
- SessionStart one-time AI-agent offer logic
- PostToolUse AI checks depth-mode branch
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tailtest.core.scan.profile import AISurface, ProjectProfile, ScanStatus
from tailtest.hook.post_tool_use import _format_additional_context, _maybe_build_ai_checks_note
from tailtest.hook.session_start import _maybe_build_ai_offer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_profile(
    *,
    ai_surface: AISurface = AISurface.AGENT,
    ai_checks_enabled: bool | None = None,
    primary_language: str = "python",
) -> ProjectProfile:
    return ProjectProfile(
        root=Path("/tmp/fake"),
        primary_language=primary_language,
        languages={"python": 5},
        ai_surface=ai_surface,
        ai_checks_enabled=ai_checks_enabled,
    )


# ---------------------------------------------------------------------------
# SessionStart: _maybe_build_ai_offer
# ---------------------------------------------------------------------------


def test_offer_fires_for_agent_with_unset_checks(tmp_path: Path) -> None:
    """Agent project + ai_checks_enabled=None + no flag -> offer in output."""
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()
    profile = _make_profile(ai_surface=AISurface.AGENT, ai_checks_enabled=None)

    result = _maybe_build_ai_offer(profile, tailtest_dir)

    assert result is not None
    assert "AI agent detected" in result
    assert "accept-ai-checks" in result
    assert "dismiss-ai-checks" in result


def test_offer_suppressed_when_flag_file_exists(tmp_path: Path) -> None:
    """Agent project + ai_checks_enabled=None + flag exists -> NO offer."""
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()
    (tailtest_dir / "ai_offer_shown.flag").write_text("", encoding="utf-8")
    profile = _make_profile(ai_surface=AISurface.AGENT, ai_checks_enabled=None)

    result = _maybe_build_ai_offer(profile, tailtest_dir)

    assert result is None


def test_offer_suppressed_when_already_accepted(tmp_path: Path) -> None:
    """Agent project + ai_checks_enabled=True -> NO offer (already decided)."""
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()
    profile = _make_profile(ai_surface=AISurface.AGENT, ai_checks_enabled=True)

    result = _maybe_build_ai_offer(profile, tailtest_dir)

    assert result is None


def test_offer_suppressed_when_dismissed(tmp_path: Path) -> None:
    """Agent project + ai_checks_enabled=False -> NO offer (already decided)."""
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()
    profile = _make_profile(ai_surface=AISurface.AGENT, ai_checks_enabled=False)

    result = _maybe_build_ai_offer(profile, tailtest_dir)

    assert result is None


def test_offer_suppressed_for_non_agent(tmp_path: Path) -> None:
    """Non-agent project + ai_checks_enabled=None -> NO offer (wrong surface)."""
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()

    for surface in (AISurface.NONE, AISurface.UTILITY):
        profile = _make_profile(ai_surface=surface, ai_checks_enabled=None)
        result = _maybe_build_ai_offer(profile, tailtest_dir)
        assert result is None, f"Expected no offer for surface={surface}"


def test_offer_writes_flag_file_on_first_call(tmp_path: Path) -> None:
    """Offer writes .tailtest/ai_offer_shown.flag so it never repeats."""
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()
    profile = _make_profile(ai_surface=AISurface.AGENT, ai_checks_enabled=None)

    flag_path = tailtest_dir / "ai_offer_shown.flag"
    assert not flag_path.exists()

    result = _maybe_build_ai_offer(profile, tailtest_dir)

    assert result is not None  # offer was emitted
    assert flag_path.exists()  # flag was written

    # Calling again immediately returns None (flag now blocks it)
    result2 = _maybe_build_ai_offer(profile, tailtest_dir)
    assert result2 is None


# ---------------------------------------------------------------------------
# PostToolUse: _maybe_build_ai_checks_note
# ---------------------------------------------------------------------------


def _make_config(depth: str = "thorough", ai_checks_enabled: bool | None = None):
    """Build a minimal Config-like object."""
    from tailtest.core.config.schema import Config, DepthMode

    config = Config()
    config = config.model_copy(update={"depth": DepthMode(depth)})
    config = config.model_copy(update={"ai_checks_enabled": ai_checks_enabled})
    return config


def test_ai_checks_note_emitted_at_thorough_depth(tmp_path: Path) -> None:
    """Agent + enabled + thorough -> AI checks note in output."""
    profile = _make_profile(ai_surface=AISurface.AGENT, ai_checks_enabled=True)
    config = _make_config(depth="thorough")

    with patch("tailtest.hook.post_tool_use.ProjectScanner") as MockScanner:
        MockScanner.return_value.load_profile.return_value = profile
        result = _maybe_build_ai_checks_note(tmp_path, config)

    assert result is not None
    assert "AI checks active" in result
    assert "LLM-judge" in result


def test_ai_checks_note_emitted_at_paranoid_depth(tmp_path: Path) -> None:
    """Agent + enabled + paranoid -> AI checks note in output."""
    profile = _make_profile(ai_surface=AISurface.AGENT, ai_checks_enabled=True)
    config = _make_config(depth="paranoid")

    with patch("tailtest.hook.post_tool_use.ProjectScanner") as MockScanner:
        MockScanner.return_value.load_profile.return_value = profile
        result = _maybe_build_ai_checks_note(tmp_path, config)

    assert result is not None
    assert "AI checks active" in result


def test_ai_checks_note_suppressed_at_standard_depth(tmp_path: Path) -> None:
    """Agent + enabled + standard -> NO AI checks note (depth too low)."""
    profile = _make_profile(ai_surface=AISurface.AGENT, ai_checks_enabled=True)
    config = _make_config(depth="standard")

    with patch("tailtest.hook.post_tool_use.ProjectScanner") as MockScanner:
        MockScanner.return_value.load_profile.return_value = profile
        result = _maybe_build_ai_checks_note(tmp_path, config)

    assert result is None
    # Scanner should not even be called when depth is too low
    MockScanner.assert_not_called()


def test_ai_checks_note_suppressed_for_non_agent(tmp_path: Path) -> None:
    """Non-agent + any depth -> NO AI checks note."""
    config = _make_config(depth="thorough")

    for surface in (AISurface.NONE, AISurface.UTILITY):
        profile = _make_profile(ai_surface=surface, ai_checks_enabled=True)
        with patch("tailtest.hook.post_tool_use.ProjectScanner") as MockScanner:
            MockScanner.return_value.load_profile.return_value = profile
            result = _maybe_build_ai_checks_note(tmp_path, config)
        assert result is None, f"Expected no AI checks note for surface={surface}"


def test_ai_checks_note_suppressed_when_not_enabled(tmp_path: Path) -> None:
    """Agent + ai_checks_enabled=False -> skip silently."""
    profile = _make_profile(ai_surface=AISurface.AGENT, ai_checks_enabled=False)
    config = _make_config(depth="thorough")

    with patch("tailtest.hook.post_tool_use.ProjectScanner") as MockScanner:
        MockScanner.return_value.load_profile.return_value = profile
        result = _maybe_build_ai_checks_note(tmp_path, config)

    assert result is None


def test_ai_checks_note_suppressed_when_unset(tmp_path: Path) -> None:
    """Agent + ai_checks_enabled=None -> skip silently (not yet decided)."""
    profile = _make_profile(ai_surface=AISurface.AGENT, ai_checks_enabled=None)
    config = _make_config(depth="thorough")

    with patch("tailtest.hook.post_tool_use.ProjectScanner") as MockScanner:
        MockScanner.return_value.load_profile.return_value = profile
        result = _maybe_build_ai_checks_note(tmp_path, config)

    assert result is None


def test_ai_checks_note_suppressed_when_no_profile(tmp_path: Path) -> None:
    """No profile on disk -> no AI checks note (graceful absence)."""
    config = _make_config(depth="thorough")

    with patch("tailtest.hook.post_tool_use.ProjectScanner") as MockScanner:
        MockScanner.return_value.load_profile.return_value = None
        result = _maybe_build_ai_checks_note(tmp_path, config)

    assert result is None


# ---------------------------------------------------------------------------
# _format_additional_context: ai_checks_note threading
# ---------------------------------------------------------------------------


def _make_empty_batch():
    from tailtest.core.findings.schema import FindingBatch

    return FindingBatch(
        run_id="test-run",
        depth="thorough",
        summary_line="tailtest: 5/5 tests passed",
        duration_ms=100.0,
    )


def test_format_additional_context_includes_ai_checks_note() -> None:
    batch = _make_empty_batch()
    output_json = _format_additional_context(
        batch,
        manifest_rescanned=False,
        ai_checks_note="tailtest: AI checks active (thorough depth). LLM-judge assertions will run on agent outputs.",
    )
    data = json.loads(output_json)
    ctx = data["hookSpecificOutput"]["additionalContext"]
    assert "AI checks active" in ctx


def test_format_additional_context_no_ai_checks_note_when_none() -> None:
    batch = _make_empty_batch()
    output_json = _format_additional_context(
        batch,
        manifest_rescanned=False,
        ai_checks_note=None,
    )
    data = json.loads(output_json)
    ctx = data["hookSpecificOutput"]["additionalContext"]
    assert "AI checks" not in ctx
