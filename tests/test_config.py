"""Tests for the config loader + schema (Phase 1 Checkpoint E.1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tailtest.core.config import (
    CONFIG_SCHEMA_VERSION,
    Config,
    ConfigLoader,
    DepthMode,
    NexTestPreference,
    RunnersConfig,
    RustRunnerConfig,
    SastConfig,
    ScaConfig,
    SecurityConfig,
    WorkspaceMode,
)
from tailtest.core.config.defaults import (
    _detect_workspace_mode,
    default_rust_runner_config_for_profile,
)


def test_config_defaults() -> None:
    config = Config()
    assert config.schema_version == CONFIG_SCHEMA_VERSION == 1
    assert config.depth == DepthMode.STANDARD
    assert config.runners.auto_detect is True
    # Phase 2 Task 2.5: security scanner trio is on by default.
    # Task 2.9 nested sast and sca into their own config types,
    # so assert via the .enabled attribute and the new ruleset
    # / use_epss defaults.
    assert config.security.secrets is True
    assert config.security.sast.enabled is True
    assert config.security.sast.ruleset == "p/default"
    assert config.security.sca.enabled is True
    assert config.security.sca.use_epss is False
    # block_on_verified_secret stays off until verification lands.
    assert config.security.block_on_verified_secret is False
    assert config.notifications.auto_offer_generation is True
    assert config.interview_completed is False


def test_config_depth_enum() -> None:
    assert {d.value for d in DepthMode} == {
        "off",
        "quick",
        "standard",
        "thorough",
        "paranoid",
    }


def test_config_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        Config(unknown="whatever")  # type: ignore[call-arg]


def test_config_roundtrip(tmp_path: Path) -> None:
    loader = ConfigLoader(tmp_path)
    original = Config(depth=DepthMode.QUICK, interview_completed=True)
    loader.save(original)
    assert loader.exists()

    loaded = loader.load()
    assert loaded.depth == DepthMode.QUICK
    assert loaded.interview_completed is True
    assert loaded.schema_version == 1


def test_config_load_missing_returns_defaults(tmp_path: Path) -> None:
    loader = ConfigLoader(tmp_path)
    config = loader.load()
    # Defaults, but file should NOT have been created
    assert config.depth == DepthMode.STANDARD
    assert not loader.exists()


def test_config_load_malformed_yaml_returns_defaults(tmp_path: Path) -> None:
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()
    (tailtest_dir / "config.yaml").write_text(":: not: valid: yaml :::")
    loader = ConfigLoader(tailtest_dir)
    config = loader.load()
    # Should not raise
    assert config.depth == DepthMode.STANDARD


def test_config_load_yaml_list_returns_defaults(tmp_path: Path) -> None:
    """YAML that parses but is a list (not a dict) should fall back to defaults."""
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()
    (tailtest_dir / "config.yaml").write_text("- item1\n- item2\n")
    loader = ConfigLoader(tailtest_dir)
    config = loader.load()
    assert config.depth == DepthMode.STANDARD


def test_config_load_validation_error_returns_defaults(tmp_path: Path) -> None:
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()
    (tailtest_dir / "config.yaml").write_text("depth: bogus\n")
    loader = ConfigLoader(tailtest_dir)
    config = loader.load()
    assert config.depth == DepthMode.STANDARD


def test_ensure_default_creates_file(tmp_path: Path) -> None:
    loader = ConfigLoader(tmp_path / ".tailtest")
    assert not loader.exists()
    config = loader.ensure_default()
    assert loader.exists()
    assert config.depth == DepthMode.STANDARD

    # Written file contains the header comment
    content = loader.config_path.read_text()
    assert "# tailtest configuration" in content
    assert "# depth: off | quick | standard" in content


def test_ensure_default_preserves_existing(tmp_path: Path) -> None:
    loader = ConfigLoader(tmp_path / ".tailtest")
    # Pre-populate with a non-default
    loader.save(Config(depth=DepthMode.QUICK))
    config = loader.ensure_default()
    assert config.depth == DepthMode.QUICK


def test_save_atomic_via_tempfile(tmp_path: Path) -> None:
    """save() should leave no .tmp files behind after success."""
    loader = ConfigLoader(tmp_path / ".tailtest")
    loader.save(Config())
    files = list((tmp_path / ".tailtest").iterdir())
    names = {f.name for f in files}
    assert "config.yaml" in names
    assert not any(n.endswith(".tmp") for n in names)


def test_save_then_load_preserves_all_fields(tmp_path: Path) -> None:
    loader = ConfigLoader(tmp_path / ".tailtest")
    original = Config(
        depth=DepthMode.THOROUGH,
        interview_completed=True,
    )
    # Flip to False so the roundtrip asserts an explicit non-default
    # (Task 2.5 changed the default to True).
    original.security.secrets = False
    original.notifications.auto_offer_generation = False

    loader.save(original)
    restored = loader.load()

    assert restored.depth == DepthMode.THOROUGH
    assert restored.interview_completed is True
    assert restored.security.secrets is False
    assert restored.notifications.auto_offer_generation is False


# --- Phase 2 Task 2.9: nested SAST + SCA config + legacy coercion ----


def test_sast_config_defaults() -> None:
    sast = SastConfig()
    assert sast.enabled is True
    assert sast.ruleset == "p/default"
    # Truthy iff enabled (backward compat with `if config.security.sast:`).
    assert bool(sast) is True


def test_sast_config_custom_ruleset() -> None:
    sast = SastConfig(ruleset="p/owasp-top-ten")
    assert sast.ruleset == "p/owasp-top-ten"
    assert sast.enabled is True


def test_sast_config_disabled_is_falsy() -> None:
    sast = SastConfig(enabled=False)
    assert bool(sast) is False


def test_sca_config_defaults() -> None:
    sca = ScaConfig()
    assert sca.enabled is True
    assert sca.use_epss is False
    assert bool(sca) is True


def test_sca_config_epss_opt_in() -> None:
    sca = ScaConfig(use_epss=True)
    assert sca.use_epss is True


def test_security_config_accepts_legacy_sast_bool_true(tmp_path: Path) -> None:
    """Phase 1 configs with ``sast: true`` must still parse."""
    legacy = {
        "schema_version": 1,
        "depth": "standard",
        "security": {"sast": True, "sca": False},
    }
    config = Config(**legacy)
    assert config.security.sast.enabled is True
    assert config.security.sast.ruleset == "p/default"  # default inherited
    assert config.security.sca.enabled is False


def test_security_config_accepts_legacy_sast_bool_false(tmp_path: Path) -> None:
    legacy = {"schema_version": 1, "security": {"sast": False}}
    config = Config(**legacy)
    assert config.security.sast.enabled is False
    # Even a disabled scanner still carries the default ruleset so
    # flipping it back on does not require setting ruleset again.
    assert config.security.sast.ruleset == "p/default"


def test_security_config_accepts_nested_sast() -> None:
    """New configs can use the nested form directly."""
    new = {
        "schema_version": 1,
        "security": {
            "sast": {"enabled": True, "ruleset": "p/ci"},
            "sca": {"enabled": True, "use_epss": True},
        },
    }
    config = Config(**new)
    assert config.security.sast.ruleset == "p/ci"
    assert config.security.sca.use_epss is True


def test_security_config_roundtrip_through_loader(tmp_path: Path) -> None:
    """Full save → load preserves the nested ruleset and EPSS toggle."""
    loader = ConfigLoader(tmp_path / ".tailtest")
    original = Config(
        security=SecurityConfig(
            secrets=True,
            sast=SastConfig(enabled=True, ruleset="p/owasp-top-ten"),
            sca=ScaConfig(enabled=True, use_epss=True),
        )
    )
    loader.save(original)
    restored = loader.load()
    assert restored.security.sast.ruleset == "p/owasp-top-ten"
    assert restored.security.sca.use_epss is True


def test_security_config_legacy_yaml_roundtrips_via_loader(tmp_path: Path) -> None:
    """A hand-written YAML with the legacy bool form must load correctly."""
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()
    # Quote "off" so YAML 1.1 does not parse it as a bool.
    legacy_yaml = (
        "schema_version: 1\n"
        'depth: "standard"\n'
        "security:\n"
        "  secrets: true\n"
        "  sast: true\n"
        "  sca: false\n"
    )
    (tailtest_dir / "config.yaml").write_text(legacy_yaml)
    loader = ConfigLoader(tailtest_dir)
    config = loader.load()
    assert config.security.secrets is True
    assert config.security.sast.enabled is True
    assert config.security.sast.ruleset == "p/default"
    assert config.security.sca.enabled is False


def test_security_config_missing_block_uses_defaults(tmp_path: Path) -> None:
    """Phase 1 configs written without any security block must still load.

    A config like ``{schema_version: 1, depth: standard}`` (no
    `security` key at all) should parse and inherit every
    default from SecurityConfig: secrets on, sast nested with
    enabled+ruleset defaults, sca nested with enabled+use_epss
    defaults, block_on_verified_secret False.
    """
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()
    (tailtest_dir / "config.yaml").write_text('schema_version: 1\ndepth: "standard"\n')
    config = ConfigLoader(tailtest_dir).load()
    assert config.security.secrets is True
    assert config.security.sast.enabled is True
    assert config.security.sast.ruleset == "p/default"
    assert config.security.sca.enabled is True
    assert config.security.sca.use_epss is False
    assert config.security.block_on_verified_secret is False


def test_security_config_rejects_unknown_nested_field() -> None:
    """extra='forbid' on SastConfig catches typos."""
    with pytest.raises(ValidationError):
        SastConfig(enabled=True, ruleset="p/default", unknown_field=True)  # type: ignore[call-arg]


# --- Rust runner config (Phase 4.5 Task 4.5.6) ------------------------


def test_rust_runner_config_defaults() -> None:
    cfg = RustRunnerConfig()
    assert cfg.prefer_nextest == NexTestPreference.AUTO
    assert cfg.workspace_mode == WorkspaceMode.AUTO
    assert cfg.run_doc_tests is True


def test_nextest_preference_enum_values() -> None:
    assert {v.value for v in NexTestPreference} == {"auto", "always", "never"}


def test_workspace_mode_enum_values() -> None:
    assert {v.value for v in WorkspaceMode} == {"auto", "single", "workspace"}


def test_runners_config_has_rust_field() -> None:
    runners = RunnersConfig()
    assert isinstance(runners.rust, RustRunnerConfig)
    assert runners.auto_detect is True


def test_config_default_includes_rust_runner() -> None:
    config = Config()
    assert isinstance(config.runners.rust, RustRunnerConfig)
    assert config.runners.rust.prefer_nextest == NexTestPreference.AUTO


def test_config_rust_runner_roundtrip(tmp_path: Path) -> None:
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()
    cfg = Config()
    cfg.runners.rust.prefer_nextest = NexTestPreference.ALWAYS
    cfg.runners.rust.run_doc_tests = False
    loader = ConfigLoader(tailtest_dir)
    loader.save(cfg)
    loaded = loader.load()
    assert loaded.runners.rust.prefer_nextest == NexTestPreference.ALWAYS
    assert loaded.runners.rust.run_doc_tests is False


def test_config_rust_section_from_yaml(tmp_path: Path) -> None:
    """A config.yaml with runners.rust keys must parse into RustRunnerConfig."""
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()
    yaml_content = """\
schema_version: 1
runners:
  rust:
    prefer_nextest: never
    workspace_mode: workspace
    run_doc_tests: false
"""
    (tailtest_dir / "config.yaml").write_text(yaml_content)
    config = ConfigLoader(tailtest_dir).load()
    assert config.runners.rust.prefer_nextest == NexTestPreference.NEVER
    assert config.runners.rust.workspace_mode == WorkspaceMode.WORKSPACE
    assert config.runners.rust.run_doc_tests is False


def test_config_without_rust_section_uses_defaults(tmp_path: Path) -> None:
    """Legacy configs written before Phase 4.5 (no runners.rust key) must still load."""
    tailtest_dir = tmp_path / ".tailtest"
    tailtest_dir.mkdir()
    (tailtest_dir / "config.yaml").write_text("schema_version: 1\ndepth: standard\n")
    config = ConfigLoader(tailtest_dir).load()
    assert config.runners.rust.prefer_nextest == NexTestPreference.AUTO
    assert config.runners.rust.workspace_mode == WorkspaceMode.AUTO
    assert config.runners.rust.run_doc_tests is True


def test_rust_runner_config_rejects_unknown_field() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RustRunnerConfig(prefer_nextest="auto", bogus=True)  # type: ignore[call-arg]


# --- default_rust_runner_config_for_profile ---------------------------


def test_default_rust_runner_config_single_crate(tmp_path: Path) -> None:
    """Single-crate project (no [workspace] in Cargo.toml) -> WorkspaceMode.SINGLE."""
    from tailtest.core.scan.profile import ProjectProfile

    (tmp_path / "Cargo.toml").write_text('[package]\nname = "t"\nversion = "0.1.0"\n')
    profile = ProjectProfile(root=tmp_path)
    cfg = default_rust_runner_config_for_profile(profile)
    assert cfg.workspace_mode == WorkspaceMode.SINGLE
    assert cfg.prefer_nextest == NexTestPreference.AUTO
    assert cfg.run_doc_tests is True


def test_default_rust_runner_config_workspace(tmp_path: Path) -> None:
    """Workspace project -> WorkspaceMode.WORKSPACE."""
    from tailtest.core.scan.profile import ProjectProfile

    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["crate_a"]\n\n[workspace.package]\nversion = "0.1.0"\n'
    )
    profile = ProjectProfile(root=tmp_path)
    cfg = default_rust_runner_config_for_profile(profile)
    assert cfg.workspace_mode == WorkspaceMode.WORKSPACE


def test_default_rust_runner_config_missing_cargo_toml(tmp_path: Path) -> None:
    """No Cargo.toml -> falls back to WorkspaceMode.AUTO gracefully."""
    from tailtest.core.scan.profile import ProjectProfile

    profile = ProjectProfile(root=tmp_path)
    cfg = default_rust_runner_config_for_profile(profile)
    assert cfg.workspace_mode == WorkspaceMode.AUTO


def test_detect_workspace_mode_single(tmp_path: Path) -> None:
    from tailtest.core.scan.profile import ProjectProfile

    (tmp_path / "Cargo.toml").write_text('[package]\nname = "t"\nversion = "0.1.0"\n')
    profile = ProjectProfile(root=tmp_path)
    assert _detect_workspace_mode(profile) == WorkspaceMode.SINGLE


def test_detect_workspace_mode_workspace(tmp_path: Path) -> None:
    from tailtest.core.scan.profile import ProjectProfile

    (tmp_path / "Cargo.toml").write_text("[workspace]\nmembers = []\n")
    profile = ProjectProfile(root=tmp_path)
    assert _detect_workspace_mode(profile) == WorkspaceMode.WORKSPACE


def test_detect_workspace_mode_auto_on_error(tmp_path: Path) -> None:
    from tailtest.core.scan.profile import ProjectProfile

    profile = ProjectProfile(root=tmp_path)  # no Cargo.toml
    assert _detect_workspace_mode(profile) == WorkspaceMode.AUTO
