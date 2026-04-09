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
)


def test_config_defaults() -> None:
    config = Config()
    assert config.schema_version == CONFIG_SCHEMA_VERSION == 1
    assert config.depth == DepthMode.STANDARD
    assert config.runners.auto_detect is True
    assert config.security.secrets is False
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
    original.security.secrets = True
    original.notifications.auto_offer_generation = False

    loader.save(original)
    restored = loader.load()

    assert restored.depth == DepthMode.THOROUGH
    assert restored.interview_completed is True
    assert restored.security.secrets is True
    assert restored.notifications.auto_offer_generation is False
