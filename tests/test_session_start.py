"""Unit tests for runner detection and session management in session_start.py.

Tests pure functions: detect_python_runner, detect_node_runner, scan_runners,
detect_project_type, read_depth.  Uses tmp_path fixtures -- no live session.
"""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))

import pytest
from session_start import (
    build_bootstrap_note,
    build_compact_context,
    build_startup_context,
    detect_node_runner,
    detect_project_type,
    detect_python_runner,
    read_depth,
    scan_runners,
)


# ---------------------------------------------------------------------------
# detect_python_runner
# ---------------------------------------------------------------------------


class TestDetectPythonRunner:
    def test_no_pyproject_returns_none(self, tmp_path):
        assert detect_python_runner(str(tmp_path), str(tmp_path)) is None

    def test_pyproject_with_pytest_section(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[tool.pytest.ini_options]\ntestpaths = ['tests']\n"
        )
        result = detect_python_runner(str(tmp_path), str(tmp_path))
        assert result is not None
        assert result["command"] == "pytest"
        assert result["needs_bootstrap"] is False

    def test_pyproject_without_pytest_needs_bootstrap(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[build-system]\nrequires = ['setuptools']\n"
        )
        result = detect_python_runner(str(tmp_path), str(tmp_path))
        assert result is not None
        assert result["needs_bootstrap"] is True

    def test_pyproject_with_pytest_in_deps(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project.optional-dependencies]\ndev = ["pytest>=7"]\n'
        )
        result = detect_python_runner(str(tmp_path), str(tmp_path))
        assert result is not None
        assert result["needs_bootstrap"] is False

    def test_test_location_detected(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
        (tmp_path / "tests").mkdir()
        result = detect_python_runner(str(tmp_path), str(tmp_path))
        assert result["test_location"].endswith("tests/")

    def test_default_test_location_when_no_dir(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
        result = detect_python_runner(str(tmp_path), str(tmp_path))
        assert "tests/" in result["test_location"]


# ---------------------------------------------------------------------------
# detect_node_runner
# ---------------------------------------------------------------------------


class TestDetectNodeRunner:
    def test_no_package_json_returns_none(self, tmp_path):
        assert detect_node_runner(str(tmp_path), str(tmp_path)) is None

    def test_vitest_in_dev_deps(self, tmp_path):
        pkg = {"devDependencies": {"vitest": "^1.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        result = detect_node_runner(str(tmp_path), str(tmp_path))
        assert result is not None
        assert result["command"] == "vitest"
        assert result["needs_bootstrap"] is False

    def test_jest_in_dev_deps(self, tmp_path):
        pkg = {"devDependencies": {"jest": "^29.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        result = detect_node_runner(str(tmp_path), str(tmp_path))
        assert result is not None
        assert result["command"] == "jest"

    def test_vitest_preferred_over_jest(self, tmp_path):
        pkg = {"devDependencies": {"vitest": "^1.0.0", "jest": "^29.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        result = detect_node_runner(str(tmp_path), str(tmp_path))
        assert result["command"] == "vitest"

    def test_vitest_in_scripts(self, tmp_path):
        pkg = {"scripts": {"test": "vitest run"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        result = detect_node_runner(str(tmp_path), str(tmp_path))
        assert result["command"] == "vitest"

    def test_no_runner_needs_bootstrap(self, tmp_path):
        pkg = {"name": "my-app", "version": "1.0.0"}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        result = detect_node_runner(str(tmp_path), str(tmp_path))
        assert result is not None
        assert result["needs_bootstrap"] is True

    def test_tests_dir_detected(self, tmp_path):
        pkg = {"devDependencies": {"vitest": "^1.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        (tmp_path / "__tests__").mkdir()
        result = detect_node_runner(str(tmp_path), str(tmp_path))
        assert "__tests__/" in result["test_location"]

    def test_malformed_json_returns_none(self, tmp_path):
        (tmp_path / "package.json").write_text("not valid json {{{")
        assert detect_node_runner(str(tmp_path), str(tmp_path)) is None


# ---------------------------------------------------------------------------
# scan_runners -- multi-directory detection
# ---------------------------------------------------------------------------


class TestScanRunners:
    def test_root_python(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
        runners = scan_runners(str(tmp_path))
        assert "python" in runners

    def test_root_node(self, tmp_path):
        pkg = {"devDependencies": {"vitest": "^1.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        runners = scan_runners(str(tmp_path))
        assert "javascript" in runners or "typescript" in runners

    def test_subdirectory_python(self, tmp_path):
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
        runners = scan_runners(str(tmp_path))
        assert "python" in runners

    def test_subdirectory_node(self, tmp_path):
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        pkg = {"devDependencies": {"vitest": "^1.0.0"}}
        (frontend / "package.json").write_text(json.dumps(pkg))
        runners = scan_runners(str(tmp_path))
        assert "javascript" in runners or "typescript" in runners

    def test_full_stack_both_detected(self, tmp_path):
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        pkg = {"devDependencies": {"vitest": "^1.0.0"}}
        (frontend / "package.json").write_text(json.dumps(pkg))
        runners = scan_runners(str(tmp_path))
        assert "python" in runners
        assert "javascript" in runners or "typescript" in runners

    def test_node_modules_not_scanned(self, tmp_path):
        node_mods = tmp_path / "node_modules" / "some-pkg"
        node_mods.mkdir(parents=True)
        (node_mods / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
        runners = scan_runners(str(tmp_path))
        # Should not detect runner inside node_modules
        assert "python" not in runners

    def test_typescript_detected_with_tsconfig(self, tmp_path):
        (tmp_path / "package.json").write_text(
            json.dumps({"devDependencies": {"vitest": "^1.0.0"}})
        )
        (tmp_path / "tsconfig.json").write_text("{}")
        runners = scan_runners(str(tmp_path))
        assert "typescript" in runners


# ---------------------------------------------------------------------------
# detect_project_type
# ---------------------------------------------------------------------------


class TestDetectProjectType:
    def test_python_project(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("")
        assert detect_project_type(str(tmp_path)) == "Python"

    def test_typescript_project(self, tmp_path):
        (tmp_path / "package.json").write_text("{}")
        (tmp_path / "tsconfig.json").write_text("{}")
        assert detect_project_type(str(tmp_path)) == "TypeScript"

    def test_javascript_project(self, tmp_path):
        (tmp_path / "package.json").write_text("{}")
        assert detect_project_type(str(tmp_path)) == "JavaScript"

    def test_unknown_project(self, tmp_path):
        assert detect_project_type(str(tmp_path)) == "Unknown"


# ---------------------------------------------------------------------------
# read_depth
# ---------------------------------------------------------------------------


class TestReadDepth:
    def test_default_is_standard(self, tmp_path):
        assert read_depth(str(tmp_path)) == "standard"

    def test_reads_from_config(self, tmp_path):
        tailtest_dir = tmp_path / ".tailtest"
        tailtest_dir.mkdir()
        (tailtest_dir / "config.json").write_text(json.dumps({"depth": "thorough"}))
        assert read_depth(str(tmp_path)) == "thorough"

    def test_simple_depth(self, tmp_path):
        tailtest_dir = tmp_path / ".tailtest"
        tailtest_dir.mkdir()
        (tailtest_dir / "config.json").write_text(json.dumps({"depth": "simple"}))
        assert read_depth(str(tmp_path)) == "simple"

    def test_invalid_depth_falls_back(self, tmp_path):
        tailtest_dir = tmp_path / ".tailtest"
        tailtest_dir.mkdir()
        (tailtest_dir / "config.json").write_text(json.dumps({"depth": "extreme"}))
        assert read_depth(str(tmp_path)) == "standard"


# ---------------------------------------------------------------------------
# build_bootstrap_note
# ---------------------------------------------------------------------------


class TestBuildBootstrapNote:
    def test_no_bootstrap_needed(self):
        runners = {
            "python": {"command": "pytest", "needs_bootstrap": False},
        }
        assert build_bootstrap_note(runners) is None

    def test_python_bootstrap_needed(self):
        runners = {"python": {"command": "pytest", "needs_bootstrap": True}}
        note = build_bootstrap_note(runners)
        assert note is not None
        assert "pytest" in note

    def test_node_bootstrap_needed(self):
        runners = {"typescript": {"command": "vitest", "needs_bootstrap": True}}
        note = build_bootstrap_note(runners)
        assert note is not None
        assert "vitest" in note

    def test_mixed_bootstrap(self):
        runners = {
            "python": {"command": "pytest", "needs_bootstrap": True},
            "typescript": {"command": "vitest", "needs_bootstrap": False},
        }
        note = build_bootstrap_note(runners)
        assert note is not None
        assert "pytest" in note
        assert "vitest" not in note


# ---------------------------------------------------------------------------
# build_startup_context
# ---------------------------------------------------------------------------


class TestBuildStartupContext:
    def test_includes_claude_md(self):
        runners = {"python": {"command": "pytest", "args": ["-q"], "test_location": "tests/"}}
        ctx = build_startup_context("/tmp/proj", runners, "standard", "# tailtest\nInstructions here.")
        assert "# tailtest" in ctx
        assert "Instructions here." in ctx

    def test_includes_runner_summary(self):
        runners = {"python": {"command": "pytest", "args": ["-q"], "test_location": "tests/"}}
        ctx = build_startup_context("/tmp/proj", runners, "standard", "")
        assert "pytest" in ctx
        assert "tests/" in ctx

    def test_includes_depth(self):
        runners = {}
        ctx = build_startup_context("/tmp/proj", runners, "thorough", "")
        assert "thorough" in ctx

    def test_bootstrap_note_included_when_needed(self):
        runners = {"python": {"command": "pytest", "args": ["-q"], "test_location": "tests/", "needs_bootstrap": True}}
        ctx = build_startup_context("/tmp/proj", runners, "standard", "")
        assert "bootstrap" in ctx.lower() or "pytest" in ctx


# ---------------------------------------------------------------------------
# build_compact_context
# ---------------------------------------------------------------------------


class TestBuildCompactContext:
    def test_includes_claude_md(self):
        ctx = build_compact_context("/tmp/proj", {}, "standard", [], {}, "# tailtest\nRules.")
        assert "# tailtest" in ctx

    def test_mentions_compaction(self):
        ctx = build_compact_context("/tmp/proj", {}, "standard", [], {}, "")
        assert "compaction" in ctx

    def test_shows_pending_files(self):
        pending = [{"path": "main.py", "language": "python", "status": "new-file"}]
        ctx = build_compact_context("/tmp/proj", {}, "standard", pending, {}, "")
        assert "main.py" in ctx
        assert "1 file(s) pending" in ctx

    def test_shows_fix_attempts(self):
        ctx = build_compact_context("/tmp/proj", {}, "standard", [], {"main.py": 2}, "")
        assert "main.py" in ctx
        assert "2" in ctx

    def test_no_pending_no_pending_line(self):
        ctx = build_compact_context("/tmp/proj", {}, "standard", [], {}, "")
        assert "pending" not in ctx
