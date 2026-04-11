# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""Default config value helpers for tailtest (Phase 3 Task 3.7).

These helpers let Task 3.8 (first-config write) choose sensible defaults
based on the project profile without coupling the config writer to the
full recommender engine.
"""

from __future__ import annotations

from tailtest.core.config.schema import NexTestPreference, RustRunnerConfig, WorkspaceMode
from tailtest.core.scan.profile import ProjectProfile


def default_scan_mode_for_profile(profile: ProjectProfile) -> str:
    """Return the recommended default scan_mode for a given profile.

    Vibe-coded projects default to 'standard' (not 'off') since they
    benefit most from automatic test and security feedback.
    Non-vibe-coded projects also default to 'standard' for now.
    This helper is a hook for Task 3.8 to use when writing the first
    config -- future revisions may return 'off' for mature projects that
    have their own CI pipeline and prefer manual invocation.
    """
    if getattr(profile, "likely_vibe_coded", False):
        return "standard"
    return "standard"  # same for now; future: return "off" for mature projects


def default_rust_runner_config_for_profile(profile: ProjectProfile) -> RustRunnerConfig:
    """Return a ``RustRunnerConfig`` tailored to the scanned project.

    Called when writing the first ``.tailtest/config.yaml`` for a project
    whose ``primary_language`` is ``"rust"``. The returned config carries
    sensible defaults that reflect the toolchain present at scan time:

    - ``prefer_nextest``: ``"auto"`` always. Users who want to lock the
      choice to always or never can edit the config file.
    - ``workspace_mode``: ``"workspace"`` if the project root
      ``Cargo.toml`` has a ``[workspace]`` table; ``"single"`` otherwise.
      Detected heuristically; the scanner's profile does not yet carry
      explicit workspace information so we re-read ``Cargo.toml``.
    - ``run_doc_tests``: ``True`` always. Doc tests are free (cargo test
      runs them by default) and they catch real regression bugs.

    This function is pure (no side effects) and may be called without a
    real filesystem by passing a minimal ``ProjectProfile``.
    """
    workspace_mode = _detect_workspace_mode(profile)
    return RustRunnerConfig(
        prefer_nextest=NexTestPreference.AUTO,
        workspace_mode=workspace_mode,
        run_doc_tests=True,
    )


def _detect_workspace_mode(profile: ProjectProfile) -> WorkspaceMode:
    """Heuristically determine WorkspaceMode from the project profile.

    Reads ``Cargo.toml`` at the project root. If it contains a
    ``[workspace]`` section, returns ``WORKSPACE``; otherwise ``SINGLE``.
    Falls back to ``AUTO`` on any read or parse error so callers always
    get a valid mode.
    """
    try:
        import tomllib

        cargo_toml = profile.root / "Cargo.toml"
        data = tomllib.loads(cargo_toml.read_text(encoding="utf-8"))
        if "workspace" in data:
            return WorkspaceMode.WORKSPACE
        return WorkspaceMode.SINGLE
    except Exception:
        return WorkspaceMode.AUTO
