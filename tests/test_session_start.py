"""Unit tests for runner detection and session management in session_start.py.

Tests pure functions: detect_python_runner, detect_node_runner, scan_runners,
detect_project_type, read_depth.  Uses tmp_path fixtures -- no live session.
"""

import json
import subprocess
import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))

import pytest
from session_start import (
    RAMP_UP_SENTINEL,
    _git_commit_counts,
    _has_existing_test,
    _is_ramp_up_filtered,
    _score_candidate,
    build_bootstrap_note,
    build_compact_context,
    build_startup_context,
    build_style_context,
    create_session,
    detect_custom_helpers,
    detect_go_runner,
    detect_java_runner,
    detect_monorepo,
    detect_node_runner,
    detect_php_runner,
    detect_project_type,
    detect_python_runner,
    detect_ruby_runner,
    detect_rust_runner,
    extract_style_snippet,
    find_recent_test_files,
    is_first_session,
    load_ignore_patterns,
    ramp_up_scan,
    read_depth,
    read_ramp_up_limit,
    scan_packages,
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

    def test_test_dir_without_s_used_when_present(self, tmp_path):
        # Criterion 3: projects with test/ (no s) should route there, not tests/
        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
        (tmp_path / "test").mkdir()  # test/, not tests/
        result = detect_python_runner(str(tmp_path), str(tmp_path))
        assert result is not None
        assert result["test_location"].endswith("test/")

    def test_django_framework_detected(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
        (tmp_path / "manage.py").write_text("#!/usr/bin/env python\n")
        result = detect_python_runner(str(tmp_path), str(tmp_path))
        assert result is not None
        assert result.get("framework") == "django"

    def test_fastapi_framework_detected(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\ndependencies = ["fastapi>=0.100"]\n'
        )
        result = detect_python_runner(str(tmp_path), str(tmp_path))
        assert result is not None
        assert result.get("framework") == "fastapi"

    def test_no_framework_returns_no_framework_key(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
        result = detect_python_runner(str(tmp_path), str(tmp_path))
        assert result is not None
        assert "framework" not in result


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

    def test_nextjs_framework_detected(self, tmp_path):
        pkg = {"devDependencies": {"vitest": "^1.0.0"}, "dependencies": {"next": "14.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        result = detect_node_runner(str(tmp_path), str(tmp_path))
        assert result is not None
        assert result.get("framework") == "nextjs"

    def test_nuxt_framework_via_dep(self, tmp_path):
        pkg = {"devDependencies": {"vitest": "^1.0.0", "nuxt": "^3.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        result = detect_node_runner(str(tmp_path), str(tmp_path))
        assert result is not None
        assert result.get("framework") == "nuxt"

    def test_nuxt_framework_via_config_file(self, tmp_path):
        pkg = {"devDependencies": {"vitest": "^1.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        (tmp_path / "nuxt.config.ts").write_text("export default defineNuxtConfig({})\n")
        result = detect_node_runner(str(tmp_path), str(tmp_path))
        assert result is not None
        assert result.get("framework") == "nuxt"

    def test_no_framework_returns_none_for_framework_key(self, tmp_path):
        pkg = {"devDependencies": {"vitest": "^1.0.0"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        result = detect_node_runner(str(tmp_path), str(tmp_path))
        assert result is not None
        assert "framework" not in result


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


# ---------------------------------------------------------------------------
# detect_php_runner
# ---------------------------------------------------------------------------


class TestDetectPhpRunner:
    def test_no_composer_json_returns_none(self, tmp_path):
        assert detect_php_runner(str(tmp_path), str(tmp_path)) is None

    def test_composer_without_phpunit_returns_none(self, tmp_path):
        composer = {"require": {"php": "^8.1"}, "require-dev": {"mockery/mockery": "^1.6"}}
        (tmp_path / "composer.json").write_text(json.dumps(composer))
        assert detect_php_runner(str(tmp_path), str(tmp_path)) is None

    def test_phpunit_in_require_dev(self, tmp_path):
        composer = {"require-dev": {"phpunit/phpunit": "^10.0"}}
        (tmp_path / "composer.json").write_text(json.dumps(composer))
        result = detect_php_runner(str(tmp_path), str(tmp_path))
        assert result is not None
        assert result["command"] == "./vendor/bin/phpunit"
        assert result["test_location"] == "tests/"

    def test_phpunit_xml_config_without_dep(self, tmp_path):
        (tmp_path / "composer.json").write_text(json.dumps({"require-dev": {}}))
        (tmp_path / "phpunit.xml").write_text("<phpunit/>")
        result = detect_php_runner(str(tmp_path), str(tmp_path))
        assert result is not None

    def test_laravel_framework_detected(self, tmp_path):
        composer = {
            "require": {"laravel/framework": "^10.0", "php": "^8.1"},
            "require-dev": {"phpunit/phpunit": "^10.0"},
        }
        (tmp_path / "composer.json").write_text(json.dumps(composer))
        (tmp_path / "artisan").write_text("#!/usr/bin/env php\n")
        result = detect_php_runner(str(tmp_path), str(tmp_path))
        assert result is not None
        assert result.get("framework") == "laravel"
        assert "unit_test_dir" in result
        assert "feature_test_dir" in result

    def test_non_laravel_has_no_framework_key(self, tmp_path):
        composer = {"require-dev": {"phpunit/phpunit": "^10.0"}}
        (tmp_path / "composer.json").write_text(json.dumps(composer))
        result = detect_php_runner(str(tmp_path), str(tmp_path))
        assert result is not None
        assert "framework" not in result


# ---------------------------------------------------------------------------
# detect_go_runner
# ---------------------------------------------------------------------------


class TestDetectGoRunner:
    def test_no_go_mod_returns_none(self, tmp_path):
        assert detect_go_runner(str(tmp_path), str(tmp_path)) is None

    def test_go_mod_present(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example.com/myapp\n\ngo 1.21\n")
        result = detect_go_runner(str(tmp_path), str(tmp_path))
        assert result is not None
        assert result["command"] == "go test"
        assert result["style"] == "colocated"
        assert "./..." in result["args"]


# ---------------------------------------------------------------------------
# detect_ruby_runner
# ---------------------------------------------------------------------------


class TestDetectRubyRunner:
    def test_no_gemfile_returns_none(self, tmp_path):
        assert detect_ruby_runner(str(tmp_path), str(tmp_path)) is None

    def test_gemfile_without_rspec_or_minitest_returns_none(self, tmp_path):
        (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\ngem 'rails'\n")
        assert detect_ruby_runner(str(tmp_path), str(tmp_path)) is None

    def test_rspec_gemfile(self, tmp_path):
        (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\ngem 'rspec-rails'\n")
        result = detect_ruby_runner(str(tmp_path), str(tmp_path))
        assert result is not None
        assert result["command"] == "bundle exec rspec"
        assert "spec/" in result["test_location"]

    def test_minitest_gemfile(self, tmp_path):
        (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\ngem 'minitest'\n")
        result = detect_ruby_runner(str(tmp_path), str(tmp_path))
        assert result is not None
        assert "rake test" in result["command"]
        assert "test/" in result["test_location"]

    def test_rails_framework_detected(self, tmp_path):
        (tmp_path / "Gemfile").write_text(
            "source 'https://rubygems.org'\ngem 'rails'\ngem 'rspec-rails'\n"
        )
        result = detect_ruby_runner(str(tmp_path), str(tmp_path))
        assert result is not None
        assert result.get("framework") == "rails"

    def test_rspec_preferred_over_minitest(self, tmp_path):
        (tmp_path / "Gemfile").write_text(
            "source 'https://rubygems.org'\ngem 'rspec'\ngem 'minitest'\n"
        )
        result = detect_ruby_runner(str(tmp_path), str(tmp_path))
        assert result is not None
        assert result["command"] == "bundle exec rspec"


# ---------------------------------------------------------------------------
# detect_rust_runner
# ---------------------------------------------------------------------------


class TestDetectRustRunner:
    def test_no_cargo_toml_returns_none(self, tmp_path):
        assert detect_rust_runner(str(tmp_path), str(tmp_path)) is None

    def test_cargo_toml_present(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname = \"myapp\"\nversion = \"0.1.0\"\n")
        result = detect_rust_runner(str(tmp_path), str(tmp_path))
        assert result is not None
        assert result["command"] == "cargo test"
        assert result["style"] == "inline"
        assert result["test_location"] == "inline"


# ---------------------------------------------------------------------------
# detect_java_runner
# ---------------------------------------------------------------------------


class TestDetectJavaRunner:
    def test_no_build_file_returns_none(self, tmp_path):
        assert detect_java_runner(str(tmp_path), str(tmp_path)) is None

    def test_maven_pom_xml(self, tmp_path):
        (tmp_path / "pom.xml").write_text("<project><modelVersion>4.0.0</modelVersion></project>")
        result = detect_java_runner(str(tmp_path), str(tmp_path))
        assert result is not None
        assert result["command"] == "./mvnw test"
        assert result["test_location"] == "src/test/java/"

    def test_gradle_build_gradle(self, tmp_path):
        (tmp_path / "build.gradle").write_text("plugins { id 'java' }\n")
        result = detect_java_runner(str(tmp_path), str(tmp_path))
        assert result is not None
        assert result["command"] == "./gradlew test"

    def test_spring_boot_detected_in_pom(self, tmp_path):
        pom = (
            "<project>\n"
            "  <parent>\n"
            "    <groupId>org.springframework.boot</groupId>\n"
            "    <artifactId>spring-boot-starter-parent</artifactId>\n"
            "  </parent>\n"
            "</project>\n"
        )
        (tmp_path / "pom.xml").write_text(pom)
        result = detect_java_runner(str(tmp_path), str(tmp_path))
        assert result is not None
        assert result.get("framework") == "spring"

    def test_no_spring_no_framework_key(self, tmp_path):
        (tmp_path / "pom.xml").write_text("<project><modelVersion>4.0.0</modelVersion></project>")
        result = detect_java_runner(str(tmp_path), str(tmp_path))
        assert result is not None
        assert "framework" not in result


# ---------------------------------------------------------------------------
# find_recent_test_files
# ---------------------------------------------------------------------------


class TestFindRecentTestFiles:
    def test_empty_project_returns_empty(self, tmp_path):
        runners = {"python": {"command": "pytest", "test_location": "tests/"}}
        result = find_recent_test_files(str(tmp_path), runners)
        assert result == []

    def test_finds_python_test_files(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_billing.py").write_text("def test_x(): pass\n")
        (tests_dir / "test_pricing.py").write_text("def test_y(): pass\n")
        runners = {"python": {"command": "pytest", "test_location": "tests/"}}
        result = find_recent_test_files(str(tmp_path), runners)
        basenames = [os.path.basename(p) for p in result]
        assert "test_billing.py" in basenames
        assert "test_pricing.py" in basenames

    def test_respects_max_files(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        for i in range(5):
            (tests_dir / f"test_module{i}.py").write_text(f"def test_{i}(): pass\n")
        runners = {"python": {"command": "pytest", "test_location": "tests/"}}
        result = find_recent_test_files(str(tmp_path), runners, max_files=3)
        assert len(result) == 3

    def test_returns_most_recent_first(self, tmp_path):
        import time
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        old = tests_dir / "test_old.py"
        new = tests_dir / "test_new.py"
        old.write_text("def test_old(): pass\n")
        time.sleep(0.05)
        new.write_text("def test_new(): pass\n")
        runners = {"python": {"command": "pytest", "test_location": "tests/"}}
        result = find_recent_test_files(str(tmp_path), runners, max_files=2)
        assert os.path.basename(result[0]) == "test_new.py"
        assert os.path.basename(result[1]) == "test_old.py"

    def test_finds_typescript_test_files(self, tmp_path):
        test_dir = tmp_path / "__tests__"
        test_dir.mkdir()
        (test_dir / "Button.test.ts").write_text("describe('Button', () => {})\n")
        runners = {"typescript": {"command": "vitest", "test_location": "__tests__/"}}
        result = find_recent_test_files(str(tmp_path), runners)
        assert len(result) == 1
        assert result[0].endswith("Button.test.ts")

    def test_ignores_node_modules(self, tmp_path):
        nm = tmp_path / "node_modules" / "some-lib" / "tests"
        nm.mkdir(parents=True)
        (nm / "test_lib.py").write_text("def test_x(): pass\n")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_real.py").write_text("def test_real(): pass\n")
        runners = {"python": {"command": "pytest", "test_location": "tests/"}}
        result = find_recent_test_files(str(tmp_path), runners)
        # test_lib.py lives inside node_modules/ -- it must NOT appear in results
        basenames = [os.path.basename(p) for p in result]
        assert "test_lib.py" not in basenames
        assert "test_real.py" in basenames
        assert len(result) == 1

    def test_no_matching_patterns_returns_empty(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "helper.py").write_text("def helper(): pass\n")
        runners = {"python": {"command": "pytest", "test_location": "tests/"}}
        result = find_recent_test_files(str(tmp_path), runners)
        assert result == []


# ---------------------------------------------------------------------------
# extract_style_snippet
# ---------------------------------------------------------------------------


class TestExtractStyleSnippet:
    def test_returns_file_content(self, tmp_path):
        f = tmp_path / "test_billing.py"
        f.write_text("import pytest\n\ndef test_add(): pass\n")
        result = extract_style_snippet(str(f))
        assert result is not None
        assert "import pytest" in result
        assert "def test_add" in result

    def test_truncates_at_max_lines(self, tmp_path):
        f = tmp_path / "test_big.py"
        f.write_text("\n".join(f"line{i}" for i in range(50)))
        result = extract_style_snippet(str(f), max_lines=5)
        assert result is not None
        lines = result.split("\n")
        assert len(lines) == 5

    def test_missing_file_returns_none(self):
        result = extract_style_snippet("/nonexistent/path/test_x.py")
        assert result is None

    def test_strips_trailing_whitespace(self, tmp_path):
        f = tmp_path / "test_x.py"
        f.write_text("def test_x(): pass\n\n\n")
        result = extract_style_snippet(str(f))
        assert result is not None
        assert not result.endswith("\n")


# ---------------------------------------------------------------------------
# detect_custom_helpers
# ---------------------------------------------------------------------------


class TestDetectCustomHelpers:
    def test_detects_conftest_import(self):
        snippet = "import pytest\nfrom conftest import create_client\n\ndef test_x(): pass\n"
        result = detect_custom_helpers([snippet])
        assert any("conftest" in h for h in result)
        assert any("create_client" in h for h in result)

    def test_detects_js_test_utils_import(self):
        snippet = (
            "import { render } from '@testing-library/react'\n"
            "import { renderWithStore } from './test-utils'\n"
            "\ndescribe('X', () => {})\n"
        )
        result = detect_custom_helpers([snippet])
        assert any("renderWithStore" in h for h in result)
        assert any("test-utils" in h for h in result)

    def test_skips_standard_library_imports(self):
        snippet = "import pytest\nimport unittest\nfrom unittest.mock import patch\n"
        result = detect_custom_helpers([snippet])
        assert result == []

    def test_skips_node_modules_imports(self):
        snippet = (
            "import { render } from '@testing-library/react'\n"
            "import { vi } from 'vitest'\n"
        )
        result = detect_custom_helpers([snippet])
        assert result == []

    def test_caps_at_five_helpers(self):
        # Multiple conftest imports across snippets
        snippets = [
            f"from conftest import helper_{i}\n" for i in range(10)
        ]
        result = detect_custom_helpers(snippets)
        assert len(result) <= 5

    def test_deduplicates_same_import(self):
        snippet = "from conftest import create_client\nfrom conftest import create_client\n"
        result = detect_custom_helpers([snippet])
        assert len([h for h in result if "create_client" in h]) == 1


# ---------------------------------------------------------------------------
# build_style_context
# ---------------------------------------------------------------------------


class TestBuildStyleContext:
    def test_no_test_files_returns_none(self, tmp_path):
        runners = {"python": {"command": "pytest", "test_location": "tests/"}}
        result = build_style_context(str(tmp_path), runners)
        assert result is None

    def test_returns_string_when_test_files_exist(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_billing.py").write_text("def test_x(): pass\n")
        runners = {"python": {"command": "pytest", "test_location": "tests/"}}
        result = build_style_context(str(tmp_path), runners)
        assert result is not None
        assert isinstance(result, str)

    def test_includes_snippet_content(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_billing.py").write_text(
            "import pytest\n\ndef test_add():\n    assert 1 + 1 == 2\n"
        )
        runners = {"python": {"command": "pytest", "test_location": "tests/"}}
        result = build_style_context(str(tmp_path), runners)
        assert result is not None
        assert "def test_add" in result

    def test_includes_header_line(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_x.py").write_text("def test_x(): pass\n")
        runners = {"python": {"command": "pytest", "test_location": "tests/"}}
        result = build_style_context(str(tmp_path), runners)
        assert result is not None
        assert "tailtest style context" in result

    def test_includes_custom_helpers_when_detected(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_api.py").write_text(
            "from conftest import create_client\n\ndef test_get(): pass\n"
        )
        runners = {"python": {"command": "pytest", "test_location": "tests/"}}
        result = build_style_context(str(tmp_path), runners)
        assert result is not None
        assert "create_client" in result

    def test_empty_runners_returns_none(self, tmp_path):
        result = build_style_context(str(tmp_path), {})
        assert result is None


# ---------------------------------------------------------------------------
# build_startup_context -- style context integration
# ---------------------------------------------------------------------------


class TestBuildStartupContextStyleIntegration:
    def test_style_context_included_when_tests_exist(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_billing.py").write_text(
            "import pytest\n\ndef test_billing(): pass\n"
        )
        runners = {"python": {"command": "pytest", "args": ["-q"], "test_location": "tests/"}}
        ctx = build_startup_context(str(tmp_path), runners, "standard", "")
        assert "tailtest style context" in ctx
        assert "def test_billing" in ctx

    def test_style_context_absent_for_empty_project(self, tmp_path):
        runners = {"python": {"command": "pytest", "args": ["-q"], "test_location": "tests/"}}
        ctx = build_startup_context(str(tmp_path), runners, "standard", "")
        assert "tailtest style context" not in ctx


# ---------------------------------------------------------------------------
# detect_monorepo
# ---------------------------------------------------------------------------


class TestDetectMonorepo:
    def test_turbo_json_detected(self, tmp_path):
        (tmp_path / "turbo.json").write_text('{"pipeline":{}}')
        assert detect_monorepo(str(tmp_path)) is True

    def test_nx_json_detected(self, tmp_path):
        (tmp_path / "nx.json").write_text('{}')
        assert detect_monorepo(str(tmp_path)) is True

    def test_pnpm_workspace_yaml_detected(self, tmp_path):
        (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - 'packages/*'\n")
        assert detect_monorepo(str(tmp_path)) is True

    def test_multiple_package_json_subdirs_detected(self, tmp_path):
        (tmp_path / "packages").mkdir()
        (tmp_path / "packages" / "web").mkdir()
        (tmp_path / "packages" / "api").mkdir()
        (tmp_path / "packages" / "web" / "package.json").write_text('{"name":"web"}')
        (tmp_path / "packages" / "api" / "package.json").write_text('{"name":"api"}')
        # Both are subdirs of packages/, not direct subdirs of tmp_path → count stays 1
        assert detect_monorepo(str(tmp_path)) is False  # only 1 direct subdir with manifest

    def test_multiple_direct_subdirs_with_manifests(self, tmp_path):
        for name in ("web", "api"):
            (tmp_path / name).mkdir()
            (tmp_path / name / "package.json").write_text(f'{{"name":"{name}"}}')
        assert detect_monorepo(str(tmp_path)) is True

    def test_flat_project_returns_false(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
        assert detect_monorepo(str(tmp_path)) is False


# ---------------------------------------------------------------------------
# scan_packages
# ---------------------------------------------------------------------------


class TestScanPackages:
    def test_finds_python_package_at_depth_2(self, tmp_path):
        pkg_dir = tmp_path / "packages" / "api"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "pyproject.toml").write_text(
            "[tool.pytest.ini_options]\ntestpaths = [\"tests\"]\n"
        )
        result = scan_packages(str(tmp_path))
        assert "packages/api" in result
        assert "python" in result["packages/api"]

    def test_finds_node_package_at_depth_2(self, tmp_path):
        pkg_dir = tmp_path / "packages" / "web"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "package.json").write_text(
            '{"devDependencies":{"vitest":"^1.0.0"}}'
        )
        result = scan_packages(str(tmp_path))
        assert "packages/web" in result
        assert "javascript" in result["packages/web"] or "typescript" in result["packages/web"]

    def test_skips_node_modules(self, tmp_path):
        nm = tmp_path / "node_modules" / "some-pkg"
        nm.mkdir(parents=True)
        (nm / "package.json").write_text('{"name":"some-pkg"}')
        result = scan_packages(str(tmp_path))
        assert not any("node_modules" in k for k in result)

    def test_does_not_include_project_root(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
        result = scan_packages(str(tmp_path))
        # "." (project root) should never appear as a key
        assert "." not in result

    def test_test_location_is_project_root_relative(self, tmp_path):
        pkg_dir = tmp_path / "packages" / "api"
        pkg_dir.mkdir(parents=True)
        tests_dir = pkg_dir / "tests"
        tests_dir.mkdir()
        (pkg_dir / "pyproject.toml").write_text(
            "[tool.pytest.ini_options]\ntestpaths = [\"tests\"]\n"
        )
        result = scan_packages(str(tmp_path))
        py_runner = result.get("packages/api", {}).get("python", {})
        # test_location should be relative to project_root: "packages/api/tests/"
        assert "packages/api" in py_runner.get("test_location", "")


# ---------------------------------------------------------------------------
# load_ignore_patterns
# ---------------------------------------------------------------------------


class TestLoadIgnorePatterns:
    def test_no_file_returns_empty(self, tmp_path):
        assert load_ignore_patterns(str(tmp_path)) == []

    def test_reads_patterns(self, tmp_path):
        (tmp_path / ".tailtest-ignore").write_text("scripts/\n*.generated.py\n")
        result = load_ignore_patterns(str(tmp_path))
        assert "scripts/" in result
        assert "*.generated.py" in result

    def test_skips_blank_lines_and_comments(self, tmp_path):
        (tmp_path / ".tailtest-ignore").write_text("# comment\n\nfoo.py\n")
        result = load_ignore_patterns(str(tmp_path))
        assert result == ["foo.py"]


# ---------------------------------------------------------------------------
# is_first_session
# ---------------------------------------------------------------------------


class TestIsFirstSession:
    def test_no_tailtest_dir_returns_true(self, tmp_path):
        assert is_first_session(str(tmp_path)) is True

    def test_reports_dir_missing_returns_true(self, tmp_path):
        (tmp_path / ".tailtest").mkdir()
        assert is_first_session(str(tmp_path)) is True

    def test_reports_dir_empty_returns_true(self, tmp_path):
        (tmp_path / ".tailtest" / "reports").mkdir(parents=True)
        assert is_first_session(str(tmp_path)) is True

    def test_reports_dir_with_md_returns_false(self, tmp_path):
        reports = tmp_path / ".tailtest" / "reports"
        reports.mkdir(parents=True)
        (reports / "session-abc.md").write_text("report")
        assert is_first_session(str(tmp_path)) is False

    def test_reports_dir_with_non_md_files_only_returns_true(self, tmp_path):
        reports = tmp_path / ".tailtest" / "reports"
        reports.mkdir(parents=True)
        (reports / "stale.json").write_text("{}")
        (reports / "debug.txt").write_text("debug")
        assert is_first_session(str(tmp_path)) is True

    def test_reports_dir_with_sentinel_returns_false(self, tmp_path):
        reports = tmp_path / ".tailtest" / "reports"
        reports.mkdir(parents=True)
        (reports / RAMP_UP_SENTINEL).write_text("")
        assert is_first_session(str(tmp_path)) is False

    def test_reports_dir_scandir_oserror_returns_true(self, tmp_path):
        reports = tmp_path / ".tailtest" / "reports"
        reports.mkdir(parents=True)
        with patch("session_start.os.scandir", side_effect=OSError("fail")):
            assert is_first_session(str(tmp_path)) is True


# ---------------------------------------------------------------------------
# read_ramp_up_limit
# ---------------------------------------------------------------------------


class TestReadRampUpLimit:
    def test_no_config_returns_default_7(self, tmp_path):
        assert read_ramp_up_limit(str(tmp_path)) == 7

    def test_config_with_explicit_value(self, tmp_path):
        cfg = tmp_path / ".tailtest" / "config.json"
        cfg.parent.mkdir()
        cfg.write_text('{"ramp_up_limit": 5}')
        assert read_ramp_up_limit(str(tmp_path)) == 5

    def test_config_value_0_returns_0(self, tmp_path):
        cfg = tmp_path / ".tailtest" / "config.json"
        cfg.parent.mkdir()
        cfg.write_text('{"ramp_up_limit": 0}')
        assert read_ramp_up_limit(str(tmp_path)) == 0

    def test_config_below_min_clamped_to_1(self, tmp_path):
        cfg = tmp_path / ".tailtest" / "config.json"
        cfg.parent.mkdir()
        cfg.write_text('{"ramp_up_limit": -5}')
        assert read_ramp_up_limit(str(tmp_path)) == 1

    def test_config_above_max_clamped_to_15(self, tmp_path):
        cfg = tmp_path / ".tailtest" / "config.json"
        cfg.parent.mkdir()
        cfg.write_text('{"ramp_up_limit": 100}')
        assert read_ramp_up_limit(str(tmp_path)) == 15

    def test_config_malformed_json_returns_7(self, tmp_path):
        cfg = tmp_path / ".tailtest" / "config.json"
        cfg.parent.mkdir()
        cfg.write_text("not-json")
        assert read_ramp_up_limit(str(tmp_path)) == 7

    def test_config_non_integer_value_returns_7(self, tmp_path):
        cfg = tmp_path / ".tailtest" / "config.json"
        cfg.parent.mkdir()
        cfg.write_text('{"ramp_up_limit": "many"}')
        assert read_ramp_up_limit(str(tmp_path)) == 7


# ---------------------------------------------------------------------------
# _git_commit_counts
# ---------------------------------------------------------------------------


class TestGitCommitCounts:
    def test_no_git_dir_returns_empty(self, tmp_path):
        assert _git_commit_counts(str(tmp_path)) == {}

    def test_git_timeout_returns_empty(self, tmp_path):
        (tmp_path / ".git").mkdir()
        with patch(
            "session_start.subprocess.run",
            side_effect=subprocess.TimeoutExpired("git", 5),
        ):
            assert _git_commit_counts(str(tmp_path)) == {}

    def test_git_not_found_returns_empty(self, tmp_path):
        (tmp_path / ".git").mkdir()
        with patch(
            "session_start.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            assert _git_commit_counts(str(tmp_path)) == {}

    def test_no_merges_flag_present(self, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_result = MagicMock()
        mock_result.stdout = ""
        with patch("session_start.subprocess.run", return_value=mock_result) as mock_run:
            _git_commit_counts(str(tmp_path))
            args = mock_run.call_args[0][0]
            assert "--no-merges" in args

    def test_max_count_500_flag_present(self, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_result = MagicMock()
        mock_result.stdout = ""
        with patch("session_start.subprocess.run", return_value=mock_result) as mock_run:
            _git_commit_counts(str(tmp_path))
            args = mock_run.call_args[0][0]
            assert "--max-count=500" in args

    def test_parses_file_counts(self, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_result = MagicMock()
        mock_result.stdout = "\nservices/billing.py\nservices/billing.py\nlib/utils.py\n\n"
        with patch("session_start.subprocess.run", return_value=mock_result):
            counts = _git_commit_counts(str(tmp_path))
        assert counts["services/billing.py"] == 2
        assert counts["lib/utils.py"] == 1

    def test_blank_lines_not_counted(self, tmp_path):
        (tmp_path / ".git").mkdir()
        mock_result = MagicMock()
        mock_result.stdout = "\n\n   \nservices/billing.py\n\n"
        with patch("session_start.subprocess.run", return_value=mock_result):
            counts = _git_commit_counts(str(tmp_path))
        assert "" not in counts
        assert "   " not in counts


# ---------------------------------------------------------------------------
# _is_ramp_up_filtered
# ---------------------------------------------------------------------------


class TestIsRampUpFiltered:
    def test_normal_python_file_not_filtered(self):
        assert _is_ramp_up_filtered("services/billing.py", "billing.py", []) is False

    def test_test_file_filtered(self):
        assert _is_ramp_up_filtered("tests/test_billing.py", "test_billing.py", []) is True

    def test_node_modules_fragment_filtered(self):
        assert _is_ramp_up_filtered("node_modules/pkg/index.js", "index.js", []) is True

    def test_config_file_filtered(self):
        assert _is_ramp_up_filtered("tailwind.config.ts", "tailwind.config.ts", []) is True

    def test_boilerplate_manage_py_filtered(self):
        assert _is_ramp_up_filtered("manage.py", "manage.py", []) is True

    def test_go_generated_mock_filtered(self):
        assert _is_ramp_up_filtered("mocks/mock_service.go", "mock_service.go", []) is True

    def test_go_pb_generated_filtered(self):
        assert _is_ramp_up_filtered("proto/user.pb.go", "user.pb.go", []) is True

    def test_ignore_pattern_directory_filtered(self):
        assert _is_ramp_up_filtered("scripts/deploy.py", "deploy.py", ["scripts/"]) is True

    def test_ignore_pattern_glob_filtered(self):
        assert _is_ramp_up_filtered("lib/foo.generated.py", "foo.generated.py", ["*.generated.py"]) is True

    def test_dockerfile_filtered(self):
        assert _is_ramp_up_filtered("Dockerfile", "Dockerfile", []) is True

    def test_spec_file_filtered(self):
        assert _is_ramp_up_filtered("lib/billing.spec.ts", "billing.spec.ts", []) is True


# ---------------------------------------------------------------------------
# _has_existing_test
# ---------------------------------------------------------------------------


class TestHasExistingTest:
    def test_no_test_files_returns_false(self, tmp_path):
        src = tmp_path / "services" / "billing.py"
        src.parent.mkdir(parents=True)
        src.write_text("def calc(): pass\n" * 50)
        assert _has_existing_test("billing", str(src), str(tmp_path)) is False

    def test_test_file_in_tests_dir_returns_true(self, tmp_path):
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_billing.py").write_text("def test_x(): pass")
        src = tmp_path / "services" / "billing.py"
        src.parent.mkdir(parents=True)
        src.write_text("def calc(): pass\n")
        assert _has_existing_test("billing", str(src), str(tmp_path)) is True

    def test_spec_file_in_spec_dir_returns_true(self, tmp_path):
        spec = tmp_path / "spec"
        spec.mkdir()
        (spec / "billing_spec.rb").write_text("describe Billing do end")
        src = tmp_path / "app" / "billing.rb"
        src.parent.mkdir(parents=True)
        src.write_text("class Billing; end\n")
        assert _has_existing_test("billing", str(src), str(tmp_path)) is True

    def test_js_test_file_returns_true(self, tmp_path):
        tests = tmp_path / "__tests__"
        tests.mkdir()
        (tests / "billing.test.js").write_text("test('x', () => {})")
        src = tmp_path / "src" / "billing.js"
        src.parent.mkdir(parents=True)
        src.write_text("function calc() {}\n")
        assert _has_existing_test("billing", str(src), str(tmp_path)) is True

    def test_different_basename_returns_false(self, tmp_path):
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_other.py").write_text("def test_x(): pass")
        src = tmp_path / "services" / "billing.py"
        src.parent.mkdir(parents=True)
        src.write_text("def calc(): pass\n")
        assert _has_existing_test("billing", str(src), str(tmp_path)) is False

    def test_go_colocated_sibling_returns_true(self, tmp_path):
        src_dir = tmp_path / "internal" / "handler"
        src_dir.mkdir(parents=True)
        src = src_dir / "handler.go"
        src.write_text("package handler\n")
        (src_dir / "handler_test.go").write_text("package handler\n")
        assert _has_existing_test("handler", str(src), str(tmp_path)) is True

    def test_ts_colocated_sibling_returns_true(self, tmp_path):
        src_dir = tmp_path / "src" / "lib"
        src_dir.mkdir(parents=True)
        src = src_dir / "billing.ts"
        src.write_text("export function calc() {}\n")
        (src_dir / "billing.test.ts").write_text("test('x', () => {})\n")
        assert _has_existing_test("billing", str(src), str(tmp_path)) is True


# ---------------------------------------------------------------------------
# _score_candidate
# ---------------------------------------------------------------------------


class TestScoreCandidate:
    def _make_file(self, tmp_path, rel_path: str, lines: int = 200) -> tuple[str, str]:
        abs_path = tmp_path / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text("x = 1\n" * lines)
        return rel_path, str(abs_path)

    def test_services_file_medium_size_scores_high(self, tmp_path):
        rel, abs_p = self._make_file(tmp_path, "services/billing.py", 200)
        score = _score_candidate(rel, "billing", abs_p, {}, str(tmp_path))
        assert score >= 30  # at minimum path_score + size_score

    def test_tiny_file_gets_negative_size_score(self, tmp_path):
        rel, abs_p = self._make_file(tmp_path, "services/billing.py", 10)
        score = _score_candidate(rel, "billing", abs_p, {}, str(tmp_path))
        # path_score=30, size_score=-20 → net 10
        assert score == 10

    def test_git_commits_add_to_score(self, tmp_path):
        rel, abs_p = self._make_file(tmp_path, "utils/helper.py", 200)
        score_no_git = _score_candidate(rel, "helper", abs_p, {}, str(tmp_path))
        score_with_git = _score_candidate(rel, "helper", abs_p, {rel: 10}, str(tmp_path))
        assert score_with_git == score_no_git + 20  # 10 commits * 2

    def test_git_score_capped_at_40(self, tmp_path):
        rel, abs_p = self._make_file(tmp_path, "utils/helper.py", 200)
        score_50 = _score_candidate(rel, "helper", abs_p, {rel: 50}, str(tmp_path))
        score_20 = _score_candidate(rel, "helper", abs_p, {rel: 20}, str(tmp_path))
        assert score_50 == score_20  # both capped at max(20,20)*2=40

    def test_path_priority_services_over_utils(self, tmp_path):
        rel_svc, abs_svc = self._make_file(tmp_path, "services/billing.py", 200)
        rel_util, abs_util = self._make_file(tmp_path, "utils/helper.py", 200)
        score_svc = _score_candidate(rel_svc, "billing", abs_svc, {}, str(tmp_path))
        score_util = _score_candidate(rel_util, "helper", abs_util, {}, str(tmp_path))
        assert score_svc > score_util

    def test_existing_test_penalty_excludes_file(self, tmp_path):
        # Source file with matching test in tests/ -- penalty should drop score <= 0
        src_dir = tmp_path / "services"
        src_dir.mkdir()
        src = src_dir / "billing.py"
        src.write_text("x = 1\n" * 200)
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_billing.py").write_text("def test_x(): pass")
        score = _score_candidate("services/billing.py", "billing", str(src), {}, str(tmp_path))
        assert score <= 0  # penalty of -100 drops below zero


# ---------------------------------------------------------------------------
# ramp_up_scan
# ---------------------------------------------------------------------------


class TestRampUpScan:
    def _make_session(self, tmp_path) -> dict:
        """Create a minimal session dict (as create_session would produce)."""
        session = {
            "session_id": "test-session",
            "project_root": str(tmp_path),
            "runners": {},
            "depth": "standard",
            "pending_files": [],
            "touched_files": [],
            "fix_attempts": {},
            "deferred_failures": [],
            "generated_tests": {},
            "packages": {},
        }
        tailtest_dir = tmp_path / ".tailtest"
        tailtest_dir.mkdir(exist_ok=True)
        (tailtest_dir / "session.json").write_text(json.dumps(session))
        return session

    def _make_source_file(self, tmp_path, rel_path: str, lines: int = 200):
        path = tmp_path / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n" * lines)

    def test_empty_project_does_nothing(self, tmp_path):
        session = self._make_session(tmp_path)
        ramp_up_scan(str(tmp_path), {}, session)
        assert session.get("pending_files") == []
        assert "ramp_up" not in session

    def test_first_session_populates_pending_files(self, tmp_path):
        self._make_source_file(tmp_path, "services/billing.py", 200)
        session = self._make_session(tmp_path)
        ramp_up_scan(str(tmp_path), {}, session)
        assert len(session["pending_files"]) > 0

    def test_queued_files_have_ramp_up_status(self, tmp_path):
        self._make_source_file(tmp_path, "services/billing.py", 200)
        session = self._make_session(tmp_path)
        ramp_up_scan(str(tmp_path), {}, session)
        for entry in session["pending_files"]:
            assert entry["status"] == "ramp-up"

    def test_queued_files_have_correct_language(self, tmp_path):
        self._make_source_file(tmp_path, "services/billing.py", 200)
        session = self._make_session(tmp_path)
        ramp_up_scan(str(tmp_path), {}, session)
        for entry in session["pending_files"]:
            assert entry["language"] == "python"

    def test_limit_respected(self, tmp_path):
        # Create 10 source files, set limit to 3
        for i in range(10):
            self._make_source_file(tmp_path, f"services/svc_{i}.py", 200)
        cfg = tmp_path / ".tailtest" / "config.json"
        cfg.parent.mkdir(exist_ok=True)
        cfg.write_text('{"ramp_up_limit": 3}')
        session = self._make_session(tmp_path)
        ramp_up_scan(str(tmp_path), {}, session)
        assert len(session["pending_files"]) <= 3

    def test_top_scoring_files_selected(self, tmp_path):
        # High-score: services/ + 200 lines. Low-score: root level + 10 lines (tiny)
        self._make_source_file(tmp_path, "services/important.py", 200)
        self._make_source_file(tmp_path, "tiny.py", 5)
        cfg = tmp_path / ".tailtest" / "config.json"
        cfg.parent.mkdir(exist_ok=True)
        cfg.write_text('{"ramp_up_limit": 1}')
        session = self._make_session(tmp_path)
        ramp_up_scan(str(tmp_path), {}, session)
        assert len(session["pending_files"]) == 1
        assert session["pending_files"][0]["path"] == "services/important.py"

    def test_files_with_existing_tests_excluded(self, tmp_path):
        # Source file + matching test file in tests/
        self._make_source_file(tmp_path, "services/billing.py", 200)
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_billing.py").write_text("def test_x(): pass\n")
        session = self._make_session(tmp_path)
        ramp_up_scan(str(tmp_path), {}, session)
        paths = [e["path"] for e in session["pending_files"]]
        assert "services/billing.py" not in paths

    def test_session_json_written_after_scan(self, tmp_path):
        self._make_source_file(tmp_path, "services/billing.py", 200)
        session = self._make_session(tmp_path)
        ramp_up_scan(str(tmp_path), {}, session)
        # Verify both in-memory and on-disk agree
        session_path = tmp_path / ".tailtest" / "session.json"
        on_disk = json.loads(session_path.read_text())
        assert on_disk["pending_files"] == session["pending_files"]
        assert on_disk.get("ramp_up") == session.get("ramp_up")

    def test_ramp_up_flag_set_in_session(self, tmp_path):
        self._make_source_file(tmp_path, "services/billing.py", 200)
        session = self._make_session(tmp_path)
        ramp_up_scan(str(tmp_path), {}, session)
        assert session.get("ramp_up") is True

    def test_ramp_up_flag_not_set_if_no_candidates(self, tmp_path):
        # Only a tiny config file -- nothing qualifies
        (tmp_path / "config.yaml").write_text("key: value\n")
        session = self._make_session(tmp_path)
        ramp_up_scan(str(tmp_path), {}, session)
        assert "ramp_up" not in session

    def test_sentinel_file_written_when_scan_fires(self, tmp_path):
        self._make_source_file(tmp_path, "services/billing.py", 200)
        session = self._make_session(tmp_path)
        ramp_up_scan(str(tmp_path), {}, session)
        sentinel = tmp_path / ".tailtest" / "reports" / RAMP_UP_SENTINEL
        assert sentinel.exists()

    def test_test_files_excluded(self, tmp_path):
        self._make_source_file(tmp_path, "tests/test_billing.py", 200)
        session = self._make_session(tmp_path)
        ramp_up_scan(str(tmp_path), {}, session)
        paths = [e["path"] for e in session["pending_files"]]
        assert not any("test_" in p for p in paths)

    def test_is_filtered_applied_node_modules(self, tmp_path):
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("module.exports = {}\n" * 100)
        session = self._make_session(tmp_path)
        ramp_up_scan(str(tmp_path), {}, session)
        paths = [e["path"] for e in session["pending_files"]]
        assert not any("node_modules" in p for p in paths)

    def test_no_git_graceful_fallback(self, tmp_path):
        # No .git dir -- should still run using path+size scoring
        self._make_source_file(tmp_path, "services/billing.py", 200)
        session = self._make_session(tmp_path)
        ramp_up_scan(str(tmp_path), {}, session)
        assert len(session["pending_files"]) > 0

    def test_git_timeout_graceful_fallback(self, tmp_path):
        (tmp_path / ".git").mkdir()
        self._make_source_file(tmp_path, "services/billing.py", 200)
        session = self._make_session(tmp_path)
        with patch(
            "session_start.subprocess.run",
            side_effect=subprocess.TimeoutExpired("git", 5),
        ):
            ramp_up_scan(str(tmp_path), {}, session)
        assert len(session["pending_files"]) > 0  # still ran on path+size

    def test_oserror_on_json_write_does_not_crash(self, tmp_path):
        self._make_source_file(tmp_path, "services/billing.py", 200)
        session = self._make_session(tmp_path)
        with patch("builtins.open", side_effect=OSError("disk full")):
            ramp_up_scan(str(tmp_path), {}, session)
        # Should not raise -- OSError is swallowed

    def test_limit_0_does_nothing(self, tmp_path):
        self._make_source_file(tmp_path, "services/billing.py", 200)
        cfg = tmp_path / ".tailtest" / "config.json"
        cfg.parent.mkdir(exist_ok=True)
        cfg.write_text('{"ramp_up_limit": 0}')
        session = self._make_session(tmp_path)
        ramp_up_scan(str(tmp_path), {}, session)
        assert session.get("pending_files") == []
        assert "ramp_up" not in session


# ---------------------------------------------------------------------------
# build_startup_context -- ramp_up_count parameter
# ---------------------------------------------------------------------------


class TestBuildStartupContextRampUp:
    def test_ramp_up_count_0_no_ramp_up_line(self):
        ctx = build_startup_context("/proj", {}, "standard", "", ramp_up_count=0)
        assert "ramp-up" not in ctx

    def test_ramp_up_count_7_includes_line_with_count(self):
        ctx = build_startup_context("/proj", {}, "standard", "", ramp_up_count=7)
        assert "7 file(s)" in ctx
        assert "ramp-up" in ctx

    def test_ramp_up_count_positional_arg_ordering(self):
        # Verify ramp_up_count is the 5th param; call positionally to catch ordering mistakes
        ctx = build_startup_context("/proj", {}, "standard", "", 3)
        assert "3 file(s)" in ctx
