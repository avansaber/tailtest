"""Tests for Rust detection in the project scanner (Phase 4.5 Task 4.5.5)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tailtest.core.scan import ProjectScanner, detectors
from tailtest.core.scan.detectors import detect_runners

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PASSING_FIXTURE = FIXTURES / "rust_project_passing"
WORKSPACE_FIXTURE = FIXTURES / "rust_workspace"


# --- Language detection --------------------------------------------------


def test_scanner_detects_rust_language() -> None:
    """Scanner must report 'rust' as the primary language for rust_project_passing."""
    scanner = ProjectScanner(PASSING_FIXTURE)
    profile = scanner.scan_shallow()
    assert profile.primary_language == "rust"


def test_scanner_detects_rust_files_in_counts() -> None:
    """Language counts must include 'rust'."""
    files, _ = detectors.walk_project(PASSING_FIXTURE)
    counts, primary = detectors.detect_languages(files)
    assert "rust" in counts
    assert primary == "rust"


# --- Runner detection ----------------------------------------------------


def test_scanner_detects_cargo_runner() -> None:
    """detect_runners must include a cargo runner for a Rust project."""
    files, _ = detectors.walk_project(PASSING_FIXTURE)
    counts, _ = detectors.detect_languages(files)
    runners = detect_runners(PASSING_FIXTURE, counts)
    runner_names = [r.name for r in runners]
    assert "cargo" in runner_names


def test_scanner_cargo_runner_config_file() -> None:
    """The detected cargo runner must point to Cargo.toml (when cargo is on PATH)."""
    if shutil.which("cargo") is None:
        pytest.skip("cargo not on PATH")
    files, _ = detectors.walk_project(PASSING_FIXTURE)
    counts, _ = detectors.detect_languages(files)
    runners = detect_runners(PASSING_FIXTURE, counts)
    cargo_runner = next((r for r in runners if r.name == "cargo"), None)
    assert cargo_runner is not None
    assert cargo_runner.config_file is not None
    assert cargo_runner.config_file.name == "Cargo.toml"


# --- Workspace detection -------------------------------------------------


def test_scanner_detects_rust_workspace() -> None:
    """Scanner must detect the rust workspace as a Rust project."""
    scanner = ProjectScanner(WORKSPACE_FIXTURE)
    profile = scanner.scan_shallow()
    assert profile.primary_language == "rust"


def test_scanner_workspace_cargo_runner() -> None:
    """detect_runners must detect cargo for the workspace root."""
    files, _ = detectors.walk_project(WORKSPACE_FIXTURE)
    counts, _ = detectors.detect_languages(files)
    runners = detect_runners(WORKSPACE_FIXTURE, counts)
    assert any(r.name == "cargo" for r in runners)


# --- Rust AI framework detection ----------------------------------------


def test_scanner_detects_async_openai_as_ai_framework(tmp_path: Path) -> None:
    """async-openai in Cargo.toml dependencies is an AI surface signal."""
    cargo_toml = tmp_path / "Cargo.toml"
    cargo_toml.write_text(
        '[package]\nname = "myapp"\nversion = "0.1.0"\nedition = "2021"\n\n'
        '[dependencies]\nasync-openai = "0.20"\n'
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.rs").write_text("fn main() {}")

    frameworks = detectors.detect_frameworks(tmp_path)
    names = {f.name for f in frameworks}
    assert "async-openai" in names


def test_scanner_detects_anthropic_crate_as_ai_framework(tmp_path: Path) -> None:
    """anthropic crate in Cargo.toml is an AI surface signal."""
    cargo_toml = tmp_path / "Cargo.toml"
    cargo_toml.write_text(
        '[package]\nname = "myapp"\nversion = "0.1.0"\nedition = "2021"\n\n'
        '[dependencies]\nanthropic = "0.1"\n'
    )
    frameworks = detectors.detect_frameworks(tmp_path)
    names = {f.name for f in frameworks}
    assert "anthropic-rs" in names


def test_scanner_detects_glob_workspace_members(tmp_path: Path) -> None:
    """Glob patterns in workspace members (e.g. 'crates/*') are expanded correctly."""
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/*"]\nresolver = "2"\n'
    )
    crates = tmp_path / "crates"
    # crate with an AI-keyword name
    ai_crate = crates / "rusty-claude-cli"
    ai_crate.mkdir(parents=True)
    (ai_crate / "Cargo.toml").write_text(
        '[package]\nname = "rusty-claude-cli"\nversion = "0.1.0"\nedition = "2021"\n'
    )
    # normal crate
    normal = crates / "core"
    normal.mkdir()
    (normal / "Cargo.toml").write_text(
        '[package]\nname = "core"\nversion = "0.1.0"\nedition = "2021"\n'
    )

    frameworks = detectors.detect_frameworks(tmp_path)
    names = {f.name for f in frameworks}
    assert "rusty-claude-cli" in names


def test_scanner_detects_ai_surface_from_crate_name(tmp_path: Path) -> None:
    """A workspace member whose crate name contains an AI keyword triggers ai_surface: agent."""
    from tailtest.core.scan.profile import AISurface

    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/*"]\nresolver = "2"\n'
    )
    crate = tmp_path / "crates" / "mock-anthropic-service"
    crate.mkdir(parents=True)
    (crate / "Cargo.toml").write_text(
        '[package]\nname = "mock-anthropic-service"\nversion = "0.1.0"\nedition = "2021"\n'
    )

    frameworks = detectors.detect_frameworks(tmp_path)
    ai_surface, ai_confidence, ai_signals = detectors.detect_ai_surface(
        tmp_path, [], frameworks
    )
    assert ai_surface == AISurface.AGENT
    assert any("mock-anthropic-service" in s for s in ai_signals)


def test_detect_ai_surface_uses_framework_category_agent(tmp_path: Path) -> None:
    """detect_ai_surface respects category='agent' on DetectedFramework objects."""
    from tailtest.core.scan.profile import AIConfidence, AISurface, DetectedFramework

    framework = DetectedFramework(
        name="custom-agent-crate",
        confidence=AIConfidence.MEDIUM,
        source="Cargo.toml",
        category="agent",
    )
    ai_surface, ai_confidence, ai_signals = detectors.detect_ai_surface(
        tmp_path, [], [framework]
    )
    assert ai_surface == AISurface.AGENT
    assert "framework:custom-agent-crate" in ai_signals


def test_collect_cargo_tomls_handles_explicit_and_glob(tmp_path: Path) -> None:
    """_collect_cargo_tomls handles both explicit paths and glob patterns."""
    from tailtest.core.scan.detectors import _collect_cargo_tomls

    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crates/*", "extras/special"]\nresolver = "2"\n'
    )
    for name in ["alpha", "beta"]:
        crate = tmp_path / "crates" / name
        crate.mkdir(parents=True)
        (crate / "Cargo.toml").write_text(
            f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2021"\n'
        )
    special = tmp_path / "extras" / "special"
    special.mkdir(parents=True)
    (special / "Cargo.toml").write_text(
        '[package]\nname = "special"\nversion = "0.1.0"\nedition = "2021"\n'
    )

    tomls = _collect_cargo_tomls(tmp_path)
    names = {t.parent.name for t in tomls}
    assert "alpha" in names
    assert "beta" in names
    assert "special" in names
    assert tmp_path.name in names  # root Cargo.toml
