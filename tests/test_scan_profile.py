"""Tests for ProjectProfile + related types (Phase 1 Task 1.12a)."""

from __future__ import annotations

from pathlib import Path

from tailtest.core.scan.profile import (
    SCAN_SCHEMA_VERSION,
    AIConfidence,
    AISurface,
    DetectedFramework,
    DetectedInfrastructure,
    DetectedPlanFile,
    DetectedRunner,
    DirectoryClassification,
    InfrastructureKind,
    PlanFileKind,
    ProjectProfile,
    ScanStatus,
)


def test_schema_version_is_1() -> None:
    assert SCAN_SCHEMA_VERSION == 1


def test_empty_profile_has_sensible_defaults() -> None:
    profile = ProjectProfile(root=Path("/fake"))
    assert profile.schema_version == 1
    assert profile.scan_status == ScanStatus.OK
    assert profile.languages == {}
    assert profile.primary_language is None
    assert profile.runners_detected == []
    assert profile.frameworks_detected == []
    assert profile.plan_files_detected == []
    assert profile.ai_surface == AISurface.NONE
    assert profile.ai_confidence == AIConfidence.LOW
    assert profile.likely_vibe_coded is False


def test_profile_roundtrip_json() -> None:
    profile = ProjectProfile(
        root=Path("/fake/project"),
        languages={"python": 12, "yaml": 2},
        primary_language="python",
        frameworks_detected=[
            DetectedFramework(
                name="fastapi",
                source="pyproject.toml",
                category="web",
                confidence=AIConfidence.HIGH,
            )
        ],
        runners_detected=[DetectedRunner(name="pytest", language="python")],
        plan_files_detected=[
            DetectedPlanFile(
                path=Path("CLAUDE.md"),
                kind=PlanFileKind.CLAUDE_CODE_INSTRUCTIONS,
            )
        ],
        infrastructure_detected=[
            DetectedInfrastructure(kind=InfrastructureKind.DOCKER, file=Path("Dockerfile"))
        ],
        ai_surface=AISurface.AGENT,
        ai_confidence=AIConfidence.HIGH,
        ai_signals=["framework:anthropic-sdk"],
        likely_vibe_coded=True,
        vibe_coded_signals=["claude-code-instructions@CLAUDE.md"],
    )
    text = profile.to_json()
    restored = ProjectProfile.from_json(text)
    assert restored == profile


def test_profile_extra_fields_ignored() -> None:
    """Extra fields are silently ignored -- allows profile.json to evolve without breaking loaders."""
    profile = ProjectProfile(root=Path("/x"), unknown_field=True)  # type: ignore[call-arg]
    assert profile.root == Path("/x")


def test_directory_classification_defaults() -> None:
    dc = DirectoryClassification()
    assert dc.source == []
    assert dc.tests == []
    assert dc.docs == []


def test_has_plan_file() -> None:
    profile = ProjectProfile(
        root=Path("/x"),
        plan_files_detected=[
            DetectedPlanFile(path=Path("CLAUDE.md"), kind=PlanFileKind.CLAUDE_CODE_INSTRUCTIONS),
            DetectedPlanFile(path=Path("README.md"), kind=PlanFileKind.README),
        ],
    )
    assert profile.has_plan_file(PlanFileKind.CLAUDE_CODE_INSTRUCTIONS) is True
    assert profile.has_plan_file(PlanFileKind.AGENT_INVENTORY) is False


def test_has_framework() -> None:
    profile = ProjectProfile(
        root=Path("/x"),
        frameworks_detected=[
            DetectedFramework(name="fastapi", source="pyproject.toml", category="web"),
            DetectedFramework(name="anthropic-sdk", source="pyproject.toml", category="agent"),
        ],
    )
    assert profile.has_framework("fastapi") is True
    assert profile.has_framework("django") is False


def test_has_runner() -> None:
    profile = ProjectProfile(
        root=Path("/x"),
        runners_detected=[
            DetectedRunner(name="pytest", language="python"),
            DetectedRunner(name="vitest", language="typescript"),
        ],
    )
    assert profile.has_runner("python") is True
    assert profile.has_runner("typescript") is True
    assert profile.has_runner("rust") is False


def test_summary_line_failed_scan() -> None:
    profile = ProjectProfile(
        root=Path("/x"),
        scan_status=ScanStatus.FAILED,
        scan_error="permission denied",
    )
    assert "scan failed" in profile.summary_line()
    assert "permission denied" in profile.summary_line()


def test_summary_line_success() -> None:
    profile = ProjectProfile(
        root=Path("/x"),
        primary_language="python",
        total_files_walked=42,
        runners_detected=[DetectedRunner(name="pytest", language="python")],
        ai_surface=AISurface.AGENT,
        likely_vibe_coded=True,
    )
    line = profile.summary_line()
    assert "python" in line
    assert "42 files" in line
    assert "pytest" in line
    assert "agent" in line
    assert "vibe-coded" in line


def test_as_dict_for_display_shape() -> None:
    profile = ProjectProfile(
        root=Path("/x"),
        primary_language="python",
        languages={"python": 3},
    )
    display = profile.as_dict_for_display()
    assert display["primary_language"] == "python"
    assert display["languages"] == {"python": 3}
    assert "plan_files" in display
    assert "likely_vibe_coded" in display
