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
    build_style_context,
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
    read_depth,
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
