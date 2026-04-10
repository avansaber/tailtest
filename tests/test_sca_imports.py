"""Tests for import-based SCA discovery (Phase 3 Task 3.1)."""

from __future__ import annotations

from pathlib import Path

from tailtest.security.sca.imports import discover_imports

# --- Single-file scenarios ---


def test_import_flask_returns_flask(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("import flask\n")
    result = discover_imports(tmp_path)
    assert result == {"flask": "Flask"}


def test_from_import_requests(tmp_path: Path) -> None:
    (tmp_path / "client.py").write_text("from requests import get\n")
    result = discover_imports(tmp_path)
    assert result == {"requests": "requests"}


def test_dotted_from_import_uses_top_level(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("from flask.templating import render_template\n")
    result = discover_imports(tmp_path)
    assert result == {"flask": "Flask"}


def test_stdlib_imports_excluded(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("import os\nimport sys\nimport json\nimport pathlib\n")
    result = discover_imports(tmp_path)
    assert result == {}


def test_unknown_third_party_excluded(tmp_path: Path) -> None:
    """Imports not in the known mapping should not appear in results."""
    (tmp_path / "module.py").write_text("import totally_unknown_package\n")
    result = discover_imports(tmp_path)
    assert result == {}


def test_multiple_known_imports(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("import flask\nimport requests\nimport numpy\n")
    result = discover_imports(tmp_path)
    assert result.get("flask") == "Flask"
    assert result.get("requests") == "requests"
    assert result.get("numpy") == "numpy"


def test_import_alias_mapping_sklearn(tmp_path: Path) -> None:
    """sklearn maps to scikit-learn (different PyPI name)."""
    (tmp_path / "model.py").write_text("import sklearn\n")
    result = discover_imports(tmp_path)
    assert result == {"sklearn": "scikit-learn"}


def test_import_pil_maps_to_pillow(tmp_path: Path) -> None:
    (tmp_path / "image.py").write_text("from PIL import Image\n")
    result = discover_imports(tmp_path)
    assert result == {"PIL": "Pillow"}


def test_import_yaml_maps_to_pyyaml(tmp_path: Path) -> None:
    (tmp_path / "config.py").write_text("import yaml\n")
    result = discover_imports(tmp_path)
    assert result == {"yaml": "PyYAML"}


# --- Error handling ---


def test_syntax_error_file_skipped_gracefully(tmp_path: Path) -> None:
    (tmp_path / "broken.py").write_text("def this is not valid python!!!\n")
    (tmp_path / "good.py").write_text("import flask\n")
    result = discover_imports(tmp_path)
    # broken.py is skipped; good.py is still parsed
    assert result == {"flask": "Flask"}


def test_empty_python_file_skipped(tmp_path: Path) -> None:
    (tmp_path / "empty.py").write_text("")
    result = discover_imports(tmp_path)
    assert result == {}


# --- Directory pruning ---


def test_venv_dir_is_pruned(tmp_path: Path) -> None:
    venv = tmp_path / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "site_pkg.py").write_text("import flask\n")

    result = discover_imports(tmp_path)
    assert result == {}


def test_node_modules_pruned(tmp_path: Path) -> None:
    nm = tmp_path / "node_modules" / "some_tool"
    nm.mkdir(parents=True)
    (nm / "helper.py").write_text("import requests\n")

    result = discover_imports(tmp_path)
    assert result == {}


def test_pycache_pruned(tmp_path: Path) -> None:
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "cached_module.py").write_text("import flask\n")

    result = discover_imports(tmp_path)
    assert result == {}


# --- Multi-file / directory walk ---


def test_walks_nested_subdirs(tmp_path: Path) -> None:
    subdir = tmp_path / "src" / "utils"
    subdir.mkdir(parents=True)
    (subdir / "http_client.py").write_text("import httpx\n")

    result = discover_imports(tmp_path)
    assert result == {"httpx": "httpx"}


def test_deduplicates_same_import_across_files(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("import flask\n")
    (tmp_path / "b.py").write_text("import flask\nfrom flask import request\n")

    result = discover_imports(tmp_path)
    # flask appears only once
    assert list(result.keys()).count("flask") == 1
    assert result["flask"] == "Flask"


def test_non_py_files_ignored(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("import: flask\n")
    (tmp_path / "script.sh").write_text("import flask\n")

    result = discover_imports(tmp_path)
    assert result == {}
