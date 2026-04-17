"""Unit tests for the intelligence filter in post_tool_use.py.

Tests the pure functions: is_filtered, is_test_file, detect_language,
extract_file_path.  No Claude Code session required.
"""

import json
import subprocess
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))

import pytest
from post_tool_use import (
    detect_language,
    detect_framework_context,
    extract_file_path,
    find_package_root,
    is_filtered,
    is_test_file,
    build_context_note,
    build_legacy_context_note,
    get_test_file_path,
)

HOOK_PATH = os.path.join(os.path.dirname(__file__), "..", "hooks", "post_tool_use.py")


# ---------------------------------------------------------------------------
# detect_language
# ---------------------------------------------------------------------------


class TestDetectLanguage:
    def test_python(self):
        assert detect_language("main.py") == "python"

    def test_typescript(self):
        assert detect_language("service.ts") == "typescript"

    def test_tsx(self):
        assert detect_language("App.tsx") == "typescript"

    def test_javascript(self):
        assert detect_language("index.js") == "javascript"

    def test_jsx(self):
        assert detect_language("Button.jsx") == "javascript"

    def test_go(self):
        assert detect_language("handler.go") == "go"

    def test_ruby(self):
        assert detect_language("user.rb") == "ruby"

    def test_rust(self):
        assert detect_language("lib.rs") == "rust"

    def test_php(self):
        assert detect_language("Controller.php") == "php"

    def test_java(self):
        assert detect_language("Service.java") == "java"

    def test_unknown_returns_none(self):
        assert detect_language("data.csv") is None

    def test_case_insensitive(self):
        assert detect_language("Main.PY") == "python"

    def test_no_extension(self):
        assert detect_language("Makefile") is None


# ---------------------------------------------------------------------------
# is_test_file
# ---------------------------------------------------------------------------


class TestIsTestFile:
    def test_python_prefix(self):
        assert is_test_file("test_billing.py")

    def test_python_prefix_with_path(self):
        assert is_test_file("tests/test_billing.py")

    def test_go_suffix(self):
        assert is_test_file("handler_test.go")

    def test_js_dot_test(self):
        assert is_test_file("billing.test.ts")

    def test_js_spec(self):
        assert is_test_file("billing.spec.ts")

    def test_ruby_spec(self):
        assert is_test_file("user_spec.rb")

    def test_java_test(self):
        assert is_test_file("BillingServiceTest.java")

    def test_java_tests(self):
        assert is_test_file("BillingServiceTests.java")

    def test_java_it(self):
        assert is_test_file("BillingServiceIT.java")

    def test_not_test_file(self):
        assert not is_test_file("billing.py")

    def test_not_test_file_ts(self):
        assert not is_test_file("service.ts")


# ---------------------------------------------------------------------------
# is_filtered
# ---------------------------------------------------------------------------

PROJECT_ROOT = "/tmp/myproject"


def _filtered(rel_path: str, ignore_patterns: list[str] | None = None) -> bool:
    return is_filtered(
        os.path.join(PROJECT_ROOT, rel_path),
        PROJECT_ROOT,
        ignore_patterns or [],
    )


class TestIsFilteredByExtension:
    def test_yaml_skipped(self):
        assert _filtered("config.yaml")

    def test_yml_skipped(self):
        assert _filtered(".github/workflows/ci.yml")

    def test_json_skipped(self):
        assert _filtered("tsconfig.json")

    def test_toml_skipped(self):
        assert _filtered("pyproject.toml")

    def test_md_skipped(self):
        assert _filtered("README.md")

    def test_html_skipped(self):
        assert _filtered("index.html")

    def test_css_skipped(self):
        assert _filtered("styles.css")

    def test_graphql_skipped(self):
        assert _filtered("schema.graphql")

    def test_dockerfile_skipped(self):
        assert _filtered("Dockerfile")

    def test_dockerfile_variant_skipped(self):
        assert _filtered("api.dockerfile")

    def test_svg_skipped(self):
        assert _filtered("logo.svg")

    def test_sql_skipped(self):
        assert _filtered("schema.sql")


class TestIsFilteredByPath:
    def test_node_modules_skipped(self):
        assert _filtered("node_modules/lodash/index.js")

    def test_venv_skipped(self):
        assert _filtered(".venv/lib/python3.11/site-packages/requests.py")

    def test_dist_skipped(self):
        assert _filtered("dist/bundle.js")

    def test_build_skipped(self):
        assert _filtered("build/output.js")

    def test_git_skipped(self):
        assert _filtered(".git/hooks/pre-commit")

    def test_migrations_skipped(self):
        assert _filtered("migrations/0001_initial.py")

    def test_pycache_skipped(self):
        assert _filtered("app/__pycache__/billing.cpython-311.pyc")

    def test_next_dir_skipped(self):
        assert _filtered(".next/static/chunks/main.js")


class TestIsFilteredBuildConfig:
    def test_vite_config_skipped(self):
        assert _filtered("vite.config.ts")

    def test_webpack_config_skipped(self):
        assert _filtered("webpack.config.js")

    def test_tailwind_config_skipped(self):
        assert _filtered("tailwind.config.js")

    def test_next_config_skipped(self):
        assert _filtered("next.config.mjs")

    def test_regular_ts_not_skipped(self):
        assert not _filtered("services/billing.ts")


class TestIsFilteredTestFiles:
    def test_python_test_skipped(self):
        assert _filtered("tests/test_billing.py")

    def test_go_test_skipped(self):
        assert _filtered("handler_test.go")

    def test_js_spec_skipped(self):
        assert _filtered("billing.spec.ts")

    def test_java_test_class_skipped(self):
        assert _filtered("BillingServiceTest.java")


class TestIsFilteredBoilerplate:
    def test_manage_py_skipped(self):
        assert _filtered("manage.py")

    def test_wsgi_skipped(self):
        assert _filtered("wsgi.py")

    def test_asgi_skipped(self):
        assert _filtered("asgi.py")

    def test_middleware_ts_skipped(self):
        assert _filtered("middleware.ts")

    def test_middleware_js_skipped(self):
        assert _filtered("middleware.js")


class TestIsFilteredGenerated:
    def test_go_mock_skipped(self):
        assert _filtered("mock_user.go")

    def test_go_mock_suffix_skipped(self):
        assert _filtered("user_mock.go")

    def test_go_proto_skipped(self):
        assert _filtered("user.pb.go")

    def test_go_gen_skipped(self):
        assert _filtered("schema_gen.go")

    def test_ts_generated_skipped(self):
        assert _filtered("types.generated.ts")

    def test_regular_go_not_skipped(self):
        assert not _filtered("handler.go")

    def test_regular_ts_not_skipped(self):
        assert not _filtered("service.ts")


class TestIsFilteredTailTestIgnore:
    def test_exact_path_match(self):
        patterns = ["scripts/seed.py"]
        assert _filtered("scripts/seed.py", patterns)

    def test_glob_pattern(self):
        patterns = ["scripts/*.py"]
        assert _filtered("scripts/seed.py", patterns)

    def test_filename_glob(self):
        patterns = ["seed.py"]
        assert _filtered("scripts/seed.py", patterns)

    def test_no_match(self):
        patterns = ["other/*.py"]
        assert not _filtered("scripts/seed.py", patterns)

    def test_directory_pattern_trailing_slash(self):
        patterns = ["scripts/"]
        assert _filtered("scripts/deploy.py", patterns)

    def test_directory_pattern_nested(self):
        patterns = ["scripts/"]
        assert _filtered("scripts/nested/deploy.py", patterns)

    def test_directory_pattern_does_not_match_sibling(self):
        patterns = ["scripts/"]
        assert not _filtered("services/billing.py", patterns)

    def test_comment_lines_ignored(self):
        # Comments are stripped by load_ignore_patterns before reaching is_filtered
        # Here we just test that no crash occurs with an empty list
        assert not _filtered("services/billing.py", [])


class TestIsFilteredPassThrough:
    def test_python_source_passes(self):
        assert not _filtered("services/billing.py")

    def test_typescript_source_passes(self):
        assert not _filtered("src/services/billing.ts")

    def test_go_source_passes(self):
        assert not _filtered("internal/handler.go")

    def test_ruby_source_passes(self):
        assert not _filtered("app/models/user.rb")

    def test_rust_source_passes(self):
        assert not _filtered("src/lib.rs")

    def test_php_source_passes(self):
        assert not _filtered("app/Http/Controllers/UserController.php")

    def test_java_source_passes(self):
        assert not _filtered("src/main/java/BillingService.java")


# ---------------------------------------------------------------------------
# extract_file_path
# ---------------------------------------------------------------------------


class TestExtractFilePath:
    def test_write(self):
        assert extract_file_path("Write", {"file_path": "src/main.py"}) == "src/main.py"

    def test_edit(self):
        assert extract_file_path("Edit", {"file_path": "src/main.py"}) == "src/main.py"

    def test_multiedit(self):
        assert extract_file_path("MultiEdit", {"file_path": "src/main.py"}) == "src/main.py"

    def test_bash_returns_none(self):
        assert extract_file_path("Bash", {"command": "ls"}) is None

    def test_missing_file_path_returns_none(self):
        assert extract_file_path("Write", {}) is None

    def test_notebook_edit(self):
        assert extract_file_path("NotebookEdit", {"file_path": "notebook.ipynb"}) == "notebook.ipynb"


# ---------------------------------------------------------------------------
# build_context_note
# ---------------------------------------------------------------------------


class TestBuildContextNoteRunnerGuard:
    """No output when runners is empty (no manifest project)."""

    def test_empty_runners_shows_no_runner_line(self):
        # build_context_note itself still works; the guard is in main()
        # Just verifying no crash and a valid note is returned
        note = build_context_note("script.py", "new-file", "python", 1, {})
        assert "script.py" in note


class TestBuildContextNote:
    def test_single_file_with_runner(self):
        note = build_context_note(
            "services/billing.py",
            "new-file",
            "python",
            1,
            {"python": {"command": "pytest", "args": ["-q"], "test_location": "tests/"}},
        )
        assert "billing.py" in note
        assert "new-file" in note
        assert "pytest" in note

    def test_multiple_files_shows_count(self):
        note = build_context_note("app.py", "new-file", "python", 3, {})
        assert "3 files pending" in note
        assert "write tests for all of them" in note

    def test_legacy_file_status(self):
        note = build_context_note("app.py", "legacy-file", "python", 1, {})
        assert "legacy-file" in note

    def test_no_runner_still_works(self):
        note = build_context_note("app.py", "new-file", "python", 1, {})
        assert "app.py" in note
        assert "session.json" in note
        assert "write test file(s) to disk" in note

    def test_fallback_runner_from_other_language(self):
        runners = {"typescript": {"command": "vitest", "args": ["run"], "test_location": "__tests__/"}}
        note = build_context_note("app.py", "new-file", "python", 1, runners)
        assert "vitest" in note


# ---------------------------------------------------------------------------
# get_test_file_path
# ---------------------------------------------------------------------------


class TestGetTestFilePath:
    PYTHON_RUNNERS = {
        "python": {"command": "pytest", "args": ["-q"], "test_location": "tests/"}
    }
    TS_RUNNERS = {
        "typescript": {"command": "vitest", "args": ["run"], "test_location": "__tests__/"}
    }
    JS_RUNNERS = {
        "javascript": {"command": "vitest", "args": ["run"], "test_location": "__tests__/"}
    }

    def test_python_source_file(self):
        path = get_test_file_path(
            "services/billing.py", "python", self.PYTHON_RUNNERS, "/project"
        )
        assert path == "/project/tests/test_billing.py"

    def test_python_nested_source_file(self):
        path = get_test_file_path(
            "app/services/billing.py", "python", self.PYTHON_RUNNERS, "/project"
        )
        assert path == "/project/tests/test_billing.py"

    def test_typescript_source_file(self):
        path = get_test_file_path(
            "src/components/Button.tsx", "typescript", self.TS_RUNNERS, "/project"
        )
        assert path == "/project/__tests__/Button.test.ts"

    def test_javascript_source_file(self):
        path = get_test_file_path(
            "src/utils.js", "javascript", self.JS_RUNNERS, "/project"
        )
        assert path == "/project/__tests__/utils.test.js"

    def test_no_runner_returns_none(self):
        path = get_test_file_path("billing.py", "python", {}, "/project")
        assert path is None

    def test_unsupported_language_returns_none(self):
        path = get_test_file_path(
            "main.go", "go", self.PYTHON_RUNNERS, "/project"
        )
        assert path is None

    def test_test_location_trailing_slash_stripped(self):
        runners = {"python": {"command": "pytest", "test_location": "tests///"}}
        path = get_test_file_path("app.py", "python", runners, "/project")
        assert path == "/project/tests/test_app.py"

    def test_language_not_in_runners_uses_first_runner(self):
        # python file but only typescript runner present --
        # falls back to first runner's test_location with python naming
        path = get_test_file_path(
            "utils.py", "python", self.TS_RUNNERS, "/project"
        )
        assert path == "/project/__tests__/test_utils.py"

    def test_python_with_ts_runner_fallback(self):
        # python language, typescript runner as fallback -- uses TS test_location but python naming
        runners = {"typescript": {"command": "vitest", "test_location": "__tests__/"}}
        path = get_test_file_path("utils.py", "python", runners, "/project")
        assert path == "/project/__tests__/test_utils.py"

    # --- Phase 2: Go ---

    def test_go_colocated_in_subdir(self):
        runners = {"go": {"command": "go test", "args": ["./..."], "test_location": ".", "style": "colocated"}}
        path = get_test_file_path("internal/handler.go", "go", runners, "/project")
        assert path == "/project/internal/handler_test.go"

    def test_go_colocated_root_level(self):
        runners = {"go": {"command": "go test", "args": ["./..."], "test_location": ".", "style": "colocated"}}
        path = get_test_file_path("main.go", "go", runners, "/project")
        assert path == "/project/main_test.go"

    def test_go_requires_configured_runner(self):
        # go is in RUNNER_REQUIRED_LANGUAGES -- no fallback to python runner
        path = get_test_file_path("main.go", "go", self.PYTHON_RUNNERS, "/project")
        assert path is None

    # --- Phase 2: Rust ---

    def test_rust_returns_none(self):
        runners = {"rust": {"command": "cargo test", "args": [], "test_location": "inline", "style": "inline"}}
        path = get_test_file_path("src/lib.rs", "rust", runners, "/project")
        assert path is None

    def test_rust_requires_configured_runner(self):
        path = get_test_file_path("src/lib.rs", "rust", self.PYTHON_RUNNERS, "/project")
        assert path is None

    # --- Phase 2: Ruby ---

    def test_ruby_rspec(self):
        runners = {"ruby": {"command": "bundle exec rspec", "args": [], "test_location": "spec/"}}
        path = get_test_file_path("app/models/user.rb", "ruby", runners, "/project")
        assert path == "/project/spec/user_spec.rb"

    def test_ruby_minitest(self):
        runners = {"ruby": {"command": "bundle exec rake test", "args": [], "test_location": "test/"}}
        path = get_test_file_path("app/models/user.rb", "ruby", runners, "/project")
        assert path == "/project/test/user_test.rb"

    def test_ruby_requires_configured_runner(self):
        path = get_test_file_path("app/models/user.rb", "ruby", self.PYTHON_RUNNERS, "/project")
        assert path is None

    # --- Phase 2: Java ---

    def test_java_maven(self):
        runners = {"java": {"command": "./mvnw test", "args": [], "test_location": "src/test/java/"}}
        path = get_test_file_path("src/main/java/BillingService.java", "java", runners, "/project")
        assert path == "/project/src/test/java/BillingServiceTest.java"

    def test_java_requires_configured_runner(self):
        path = get_test_file_path("src/main/java/BillingService.java", "java", self.PYTHON_RUNNERS, "/project")
        assert path is None

    # --- Phase 2: PHP ---

    def test_php_controller_routes_to_feature(self):
        runners = {"php": {"command": "./vendor/bin/phpunit", "args": [], "test_location": "tests/", "unit_test_dir": "tests/Unit/", "feature_test_dir": "tests/Feature/"}}
        path = get_test_file_path("app/Http/Controllers/UserController.php", "php", runners, "/project")
        # Controllers in Http/Controllers -> Feature test dir
        assert path == "/project/tests/Feature/UserControllerTest.php"

    def test_php_service_routes_to_unit(self):
        runners = {"php": {"command": "./vendor/bin/phpunit", "args": [], "test_location": "tests/", "unit_test_dir": "tests/Unit/"}}
        path = get_test_file_path("app/Services/OrderService.php", "php", runners, "/project")
        # Services -> Unit test dir
        assert path == "/project/tests/Unit/OrderServiceTest.php"

    def test_php_requires_configured_runner(self):
        path = get_test_file_path("app/Http/Controllers/UserController.php", "php", self.PYTHON_RUNNERS, "/project")
        assert path is None


# ---------------------------------------------------------------------------
# build_legacy_context_note
# ---------------------------------------------------------------------------


class TestBuildLegacyContextNote:
    def test_includes_file_path(self):
        note = build_legacy_context_note("services/billing.py", "pytest", "tests/test_billing.py")
        assert "services/billing.py" in note

    def test_includes_do_not_generate_instruction(self):
        note = build_legacy_context_note("services/billing.py", "pytest", "tests/test_billing.py")
        assert "do not generate" in note.lower()

    def test_includes_run_command(self):
        note = build_legacy_context_note("services/billing.py", "pytest", "tests/test_billing.py")
        assert "pytest" in note
        assert "tests/test_billing.py" in note

    def test_existing_file_framing(self):
        note = build_legacy_context_note("app.py", "pytest -q", "tests/test_app.py")
        assert "existing" in note or "session" in note

    def test_vitest_runner(self):
        note = build_legacy_context_note(
            "src/Button.tsx", "npx vitest run", "__tests__/Button.test.ts"
        )
        assert "vitest" in note
        assert "Button.test.ts" in note


# ---------------------------------------------------------------------------
# detect_framework_context
# ---------------------------------------------------------------------------


class TestDetectFrameworkContext:
    GO_RUNNERS = {"go": {"command": "go test", "args": ["./..."], "style": "colocated"}}
    RUST_RUNNERS = {"rust": {"command": "cargo test", "args": [], "style": "inline"}}
    LARAVEL_RUNNERS = {"php": {"command": "./vendor/bin/phpunit", "args": [], "framework": "laravel"}}
    NEXTJS_RUNNERS = {"typescript": {"command": "vitest", "args": ["run"], "framework": "nextjs"}}
    NUXT_RUNNERS = {"typescript": {"command": "vitest", "args": ["run"], "framework": "nuxt"}}

    def test_go_colocated_style(self):
        ctx = detect_framework_context("internal/handler.go", "go", self.GO_RUNNERS)
        assert ctx == "go/colocated"

    def test_rust_inline_style(self):
        ctx = detect_framework_context("src/lib.rs", "rust", self.RUST_RUNNERS)
        assert ctx == "rust/inline"

    def test_laravel_feature_controller(self):
        ctx = detect_framework_context(
            "app/Http/Controllers/UserController.php", "php", self.LARAVEL_RUNNERS
        )
        assert ctx == "laravel/feature"

    def test_laravel_unit_model(self):
        ctx = detect_framework_context("app/Models/User.php", "php", self.LARAVEL_RUNNERS)
        assert ctx == "laravel/unit"

    def test_nextjs_framework(self):
        ctx = detect_framework_context("src/components/Button.tsx", "typescript", self.NEXTJS_RUNNERS)
        assert ctx == "nextjs"

    def test_nuxt_framework(self):
        ctx = detect_framework_context("components/MyButton.vue", "typescript", self.NUXT_RUNNERS)
        assert ctx == "nuxt"

    def test_no_framework_returns_empty(self):
        runners = {"python": {"command": "pytest", "args": ["-q"], "test_location": "tests/"}}
        ctx = detect_framework_context("services/billing.py", "python", runners)
        assert ctx == ""

    def test_language_not_in_runners_returns_empty(self):
        ctx = detect_framework_context("services/billing.py", "python", self.GO_RUNNERS)
        assert ctx == ""

    def test_vue_file_with_typescript_runner_gets_nuxt_context(self):
        # .vue files are mapped to "javascript" in LANGUAGE_MAP but runner may be
        # stored under "typescript" when tsconfig.json exists.  Cross-family fallback.
        nuxt_ts_runners = {"typescript": {"command": "vitest", "args": ["run"], "framework": "nuxt"}}
        ctx = detect_framework_context("components/InvoiceCard.vue", "javascript", nuxt_ts_runners)
        assert ctx == "nuxt"

    def test_vue_file_js_runner_gets_nuxt_context(self):
        # .vue with runner under "javascript" key (no tsconfig.json).
        nuxt_js_runners = {"javascript": {"command": "vitest", "args": ["run"], "framework": "nuxt"}}
        ctx = detect_framework_context("components/InvoiceCard.vue", "javascript", nuxt_js_runners)
        assert ctx == "nuxt"

    def test_context_note_includes_framework(self):
        note = build_context_note(
            "internal/handler.go", "new-file", "go", 1, self.GO_RUNNERS
        )
        assert "go/colocated" in note

    def test_context_note_no_framework_unchanged(self):
        runners = {"python": {"command": "pytest", "args": ["-q"], "test_location": "tests/"}}
        note = build_context_note("services/billing.py", "new-file", "python", 1, runners)
        assert "go/colocated" not in note
        assert "billing.py" in note

    def test_context_note_includes_test_path_single_file(self):
        # Single-file queue with project_root → exact test path included
        runners = {"python": {"command": "pytest", "args": ["-q"], "test_location": "tests/"}}
        note = build_context_note(
            "services/billing.py", "new-file", "python", 1, runners, "/project"
        )
        assert "tests/test_billing.py" in note

    def test_context_note_go_test_path(self):
        note = build_context_note(
            "internal/handler.go", "new-file", "go", 1, self.GO_RUNNERS, "/project"
        )
        assert "internal/handler_test.go" in note

    def test_context_note_rust_inline_hint(self):
        note = build_context_note(
            "src/lib.rs", "new-file", "rust", 1, self.RUST_RUNNERS, "/project"
        )
        assert "add #[cfg(test)]" in note
        assert "src/lib.rs" in note

    def test_context_note_laravel_feature_path(self):
        # Feature controller: path hint + .env.testing skip comment when absent.
        note = build_context_note(
            "app/Http/Controllers/UserController.php",
            "new-file", "php", 1, self.LARAVEL_RUNNERS, "/project"
        )
        assert "tests/Feature/UserControllerTest.php" in note
        assert ".env.testing" in note  # skip-comment hint emitted when absent

    def test_context_note_laravel_feature_no_skip_when_env_testing_exists(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, ".env.testing"), "w").close()
            note = build_context_note(
                "app/Http/Controllers/UserController.php",
                "new-file", "php", 1, self.LARAVEL_RUNNERS, tmpdir
            )
        assert "tests/Feature/UserControllerTest.php" in note
        assert ".env.testing" not in note  # no skip-comment hint when file exists

    def test_context_note_php_multi_file_still_includes_path(self):
        # PHP always gets an explicit path hint -- Feature vs Unit routing is non-trivial.
        note = build_context_note(
            "app/Http/Controllers/InvoiceController.php",
            "new-file", "php", 3, self.LARAVEL_RUNNERS, "/project"
        )
        assert "tests/Feature/InvoiceControllerTest.php" in note
        assert "3 files pending" in note

    def test_context_note_multi_file_no_path(self):
        # Multi-file queue for non-PHP → no test path hint (too ambiguous)
        runners = {"python": {"command": "pytest", "test_location": "tests/"}}
        note = build_context_note(
            "services/billing.py", "new-file", "python", 3, runners, "/project"
        )
        assert "test_billing.py" not in note
        assert "3 files pending" in note

    def test_context_note_no_project_root_no_path(self):
        # No project_root → no test path hint (backwards compatible)
        runners = {"python": {"command": "pytest", "test_location": "tests/"}}
        note = build_context_note("services/billing.py", "new-file", "python", 1, runners)
        assert "test_billing.py" not in note


# ---------------------------------------------------------------------------
# Phase 4: existing_test_path (update vs regenerate)
# ---------------------------------------------------------------------------


class TestContextNoteExistingTest:
    PYTHON_RUNNERS = {"python": {"command": "pytest", "test_location": "tests/"}}

    def test_existing_test_path_emits_update(self):
        note = build_context_note(
            "services/billing.py", "new-file", "python", 1,
            self.PYTHON_RUNNERS, "/project",
            existing_test_path="tests/test_billing.py",
        )
        assert "update existing test at tests/test_billing.py" in note
        assert "write test to" not in note

    def test_no_existing_test_path_emits_write(self):
        note = build_context_note(
            "services/billing.py", "new-file", "python", 1,
            self.PYTHON_RUNNERS, "/project",
        )
        assert "write test to" in note
        assert "update existing test" not in note

    def test_existing_test_path_skips_env_testing_hint(self):
        # When updating a test, the .env.testing hint is irrelevant
        import tempfile, os as _os
        with tempfile.TemporaryDirectory() as tmpdir:
            laravel_runners = {
                "php": {
                    "command": "./vendor/bin/phpunit",
                    "args": [],
                    "test_location": "tests/",
                    "framework": "laravel",
                    "unit_test_dir": "tests/Unit/",
                    "feature_test_dir": "tests/Feature/",
                }
            }
            # No .env.testing present, but existing_test_path provided → no skip hint
            note = build_context_note(
                "app/Http/Controllers/OrderController.php",
                "new-file", "php", 1,
                laravel_runners, tmpdir,
                existing_test_path="tests/Feature/OrderControllerTest.php",
            )
        assert "update existing test at tests/Feature/OrderControllerTest.php" in note
        assert ".env.testing" not in note

    def test_existing_test_path_runner_name_still_included(self):
        note = build_context_note(
            "services/billing.py", "new-file", "python", 1,
            self.PYTHON_RUNNERS, "/project",
            existing_test_path="tests/test_billing.py",
        )
        assert "pytest" in note

    def test_existing_test_path_pending_count_still_included(self):
        note = build_context_note(
            "services/billing.py", "new-file", "python", 3,
            self.PYTHON_RUNNERS, "/project",
            existing_test_path="tests/test_billing.py",
        )
        assert "3 files pending" in note


class TestMainGeneratedTestsIntegration:
    """Integration tests: hook reads generated_tests from session.json."""

    def _write_session(self, tmpdir: str, generated_tests: dict) -> None:
        tailtest = os.path.join(tmpdir, ".tailtest")
        os.makedirs(tailtest, exist_ok=True)
        session = {
            "session_id": "test-123",
            "started_at": "2026-01-01T00:00:00Z",
            "project_root": tmpdir,
            "runners": {"python": {"command": "pytest", "args": ["-q"], "test_location": "tests/"}},
            "depth": "standard",
            "pending_files": [],
            "touched_files": [],
            "fix_attempts": {},
            "deferred_failures": {},
            "generated_tests": generated_tests,
        }
        with open(os.path.join(tailtest, "session.json"), "w") as fh:
            json.dump(session, fh)

    def _write_source(self, tmpdir: str, rel_path: str) -> str:
        abs_path = os.path.join(tmpdir, rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w") as fh:
            fh.write("def add(a, b): return a + b\n")
        return abs_path

    def test_emits_update_when_generated_test_exists_on_disk(self, tmp_path):
        tmpdir = str(tmp_path)
        test_rel = "tests/test_billing.py"
        source_rel = "services/billing.py"
        # Write the test file so it "exists on disk"
        test_abs = os.path.join(tmpdir, test_rel)
        os.makedirs(os.path.dirname(test_abs), exist_ok=True)
        open(test_abs, "w").write("def test_add(): pass\n")
        self._write_source(tmpdir, source_rel)
        self._write_session(tmpdir, {source_rel: test_rel})

        result = subprocess.run(
            [sys.executable, HOOK_PATH],
            input=json.dumps({
                "tool_name": "Write",
                "tool_input": {"file_path": os.path.join(tmpdir, source_rel)},
                "cwd": tmpdir,
            }),
            capture_output=True, text=True,
        )
        out = json.loads(result.stdout)
        note = out["hookSpecificOutput"]["additionalContext"]
        assert "update existing test at tests/test_billing.py" in note
        assert "write test to" not in note

    def test_emits_write_when_generated_test_missing_from_disk(self, tmp_path):
        tmpdir = str(tmp_path)
        test_rel = "tests/test_billing.py"
        source_rel = "services/billing.py"
        # generated_tests has entry but the test file does NOT exist
        self._write_source(tmpdir, source_rel)
        self._write_session(tmpdir, {source_rel: test_rel})

        result = subprocess.run(
            [sys.executable, HOOK_PATH],
            input=json.dumps({
                "tool_name": "Write",
                "tool_input": {"file_path": os.path.join(tmpdir, source_rel)},
                "cwd": tmpdir,
            }),
            capture_output=True, text=True,
        )
        out = json.loads(result.stdout)
        note = out["hookSpecificOutput"]["additionalContext"]
        # Test file was deleted -- fall back to write-new
        assert "write test to" in note
        assert "update existing test" not in note

    def test_emits_write_when_no_generated_tests_entry(self, tmp_path):
        tmpdir = str(tmp_path)
        source_rel = "services/billing.py"
        self._write_source(tmpdir, source_rel)
        self._write_session(tmpdir, {})  # empty generated_tests

        result = subprocess.run(
            [sys.executable, HOOK_PATH],
            input=json.dumps({
                "tool_name": "Write",
                "tool_input": {"file_path": os.path.join(tmpdir, source_rel)},
                "cwd": tmpdir,
            }),
            capture_output=True, text=True,
        )
        out = json.loads(result.stdout)
        note = out["hookSpecificOutput"]["additionalContext"]
        assert "write test to" in note
        assert "update existing test" not in note


# ---------------------------------------------------------------------------
# Phase 6: find_package_root
# ---------------------------------------------------------------------------


class TestFindPackageRoot:
    def test_file_in_package_returns_package(self):
        packages = {"packages/api": {"python": {}}}
        assert find_package_root("packages/api/src/billing.py", packages) == "packages/api"

    def test_file_not_in_any_package_returns_none(self):
        packages = {"packages/api": {"python": {}}}
        assert find_package_root("src/billing.py", packages) is None

    def test_deepest_package_wins_over_shallower(self):
        # "packages/api" is deeper than "packages" -- should win
        packages = {"packages": {"python": {}}, "packages/api": {"python": {}}}
        assert find_package_root("packages/api/src/billing.py", packages) == "packages/api"

    def test_empty_packages_returns_none(self):
        assert find_package_root("services/billing.py", {}) is None

    def test_sibling_package_does_not_match(self):
        packages = {"packages/web": {"typescript": {}}, "packages/api": {"python": {}}}
        assert find_package_root("packages/web/src/Button.tsx", packages) == "packages/web"
        assert find_package_root("packages/api/src/billing.py", packages) == "packages/api"

    def test_backslash_path_normalised(self):
        packages = {"packages/api": {"python": {}}}
        assert find_package_root("packages\\api\\src\\billing.py", packages) == "packages/api"

    def test_file_at_package_root(self):
        packages = {"packages/api": {"python": {}}}
        assert find_package_root("packages/api/index.py", packages) == "packages/api"


# ---------------------------------------------------------------------------
# Phase 6: monorepo runner resolution integration
# ---------------------------------------------------------------------------


class TestMonorepoRunnerResolutionIntegration:
    """Integration tests: hook uses per-package runners when file is in a package."""

    def _write_session(self, tmpdir: str, global_runners: dict, packages: dict) -> None:
        tailtest = os.path.join(tmpdir, ".tailtest")
        os.makedirs(tailtest, exist_ok=True)
        session = {
            "session_id": "test-456",
            "started_at": "2026-01-01T00:00:00Z",
            "project_root": tmpdir,
            "runners": global_runners,
            "packages": packages,
            "depth": "standard",
            "pending_files": [],
            "touched_files": [],
            "fix_attempts": {},
            "deferred_failures": {},
            "generated_tests": {},
        }
        with open(os.path.join(tailtest, "session.json"), "w") as fh:
            json.dump(session, fh)

    def _write_source(self, tmpdir: str, rel_path: str) -> str:
        abs_path = os.path.join(tmpdir, rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w") as fh:
            fh.write("def add(a, b): return a + b\n")
        return abs_path

    def test_file_in_package_uses_package_runner(self, tmp_path):
        tmpdir = str(tmp_path)
        source_rel = "packages/api/services/billing.py"
        self._write_source(tmpdir, source_rel)
        pkg_runner = {
            "python": {
                "command": "pytest",
                "args": ["-q"],
                "test_location": "packages/api/tests/",
            }
        }
        self._write_session(tmpdir, global_runners={}, packages={"packages/api": pkg_runner})

        result = subprocess.run(
            [sys.executable, HOOK_PATH],
            input=json.dumps({
                "tool_name": "Write",
                "tool_input": {"file_path": os.path.join(tmpdir, source_rel)},
                "cwd": tmpdir,
            }),
            capture_output=True, text=True,
        )
        out = json.loads(result.stdout)
        note = out["hookSpecificOutput"]["additionalContext"]
        assert "pytest" in note
        # test path should be inside the package's test_location
        assert "packages/api/tests" in note

    def test_file_outside_all_packages_uses_global_runner(self, tmp_path):
        tmpdir = str(tmp_path)
        source_rel = "shared/utils.py"
        self._write_source(tmpdir, source_rel)
        global_runner = {
            "python": {
                "command": "pytest",
                "args": ["-q"],
                "test_location": "tests/",
            }
        }
        pkg_runner = {
            "python": {
                "command": "pytest",
                "args": ["-q"],
                "test_location": "packages/api/tests/",
            }
        }
        self._write_session(
            tmpdir, global_runners=global_runner, packages={"packages/api": pkg_runner}
        )

        result = subprocess.run(
            [sys.executable, HOOK_PATH],
            input=json.dumps({
                "tool_name": "Write",
                "tool_input": {"file_path": os.path.join(tmpdir, source_rel)},
                "cwd": tmpdir,
            }),
            capture_output=True, text=True,
        )
        out = json.loads(result.stdout)
        note = out["hookSpecificOutput"]["additionalContext"]
        assert "pytest" in note
        # Should use root tests/ not packages/api/tests/
        assert "tests/test_utils.py" in note

    def test_python_queued_with_no_runners(self, tmp_path):
        # Python/TS/JS should queue even when session_start detected no runner.
        # Claude falls back to bash execution or simulation (CLAUDE.md Step 5).
        tmpdir = str(tmp_path)
        source_rel = "services/billing.py"
        self._write_source(tmpdir, source_rel)
        self._write_session(tmpdir, global_runners={}, packages={})

        result = subprocess.run(
            [sys.executable, HOOK_PATH],
            input=json.dumps({
                "tool_name": "Write",
                "tool_input": {"file_path": os.path.join(tmpdir, source_rel)},
                "cwd": tmpdir,
            }),
            capture_output=True, text=True,
        )
        out = json.loads(result.stdout)
        note = out["hookSpecificOutput"]["additionalContext"]
        assert "services/billing.py" in note
        assert "queued" in note

    def test_runner_required_language_exits_silently_with_no_runners(self, tmp_path):
        # PHP/Go/Ruby/Rust/Java still require an explicitly detected runner.
        tmpdir = str(tmp_path)
        source_rel = "app/Services/OrderService.php"
        self._write_source(tmpdir, source_rel)
        self._write_session(tmpdir, global_runners={}, packages={})

        result = subprocess.run(
            [sys.executable, HOOK_PATH],
            input=json.dumps({
                "tool_name": "Write",
                "tool_input": {"file_path": os.path.join(tmpdir, source_rel)},
                "cwd": tmpdir,
            }),
            capture_output=True, text=True,
        )
        assert result.stdout == ""
