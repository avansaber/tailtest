#!/usr/bin/env python3
"""tailtest SessionStart hook -- project orientation and CLAUDE.md injection.

Fires on session startup, resume, and compact (post-compaction).

startup / resume:
  - Reads and injects CLAUDE.md (plugin intelligence layer)
  - Scans project manifests to detect runners and test locations
  - Creates a fresh .tailtest/session.json
  - Emits project summary as additionalContext

compact:
  - Re-injects CLAUDE.md so Claude has instructions after compaction
  - Re-emits session state summary from .tailtest/session.json

Target: < 2 seconds for startup, < 1 second for compact.
"""

from __future__ import annotations

import datetime
import fnmatch
import json
import os
import random
import re
import string
import subprocess
import sys
from typing import Optional

# ---------------------------------------------------------------------------
# Style-sampling constants
# ---------------------------------------------------------------------------

TEST_FILE_PATTERNS: dict[str, list[str]] = {
    "python": ["test_*.py", "*_test.py"],
    "typescript": ["*.test.ts", "*.spec.ts", "*.test.tsx", "*.spec.tsx"],
    "javascript": ["*.test.js", "*.spec.js", "*.test.ts", "*.spec.ts"],
    "ruby": ["*_spec.rb", "*_test.rb"],
    "go": ["*_test.go"],
    "java": ["*Test.java", "*Tests.java", "*IT.java"],
    "php": ["*Test.php", "*_test.php"],
}

# Regex to spot imports from local test-utility files (not node_modules).
_HELPER_MODULE_RE = re.compile(
    r"test[-_]utils?|helpers?|factories|factory|test[-_]setup",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Ramp-up scan constants
# ---------------------------------------------------------------------------

RAMP_UP_SENTINEL: str = ".ramp-up-initiated"

RAMP_UP_EXT_MAP: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".php": "php",
    ".go": "go",
    ".rb": "ruby",
    ".rs": "rust",
    ".java": "java",
}

RAMP_UP_SKIP_DIRS: frozenset[str] = frozenset({
    "node_modules", ".venv", "venv", "dist", "build",
    "__pycache__", "vendor", ".git", "generated", ".tailtest",
    "coverage", ".next", ".nuxt", "target", ".cargo",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".nyc_output",
    ".svelte-kit",
})

# Path fragments that indicate non-testable content
_RAMP_UP_SKIP_FRAGMENTS: tuple[str, ...] = (
    "node_modules/", ".venv/", "venv/", ".env/", "env/",
    "dist/", "build/", "generated/", ".git/", "vendor/",
    "migrations/", "db/migrate/", "database/migrations/",
    "__pycache__/", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/",
    "target/", ".cargo/", "coverage/", ".nyc_output/",
    ".next/", ".nuxt/", ".svelte-kit/", ".tailtest/",
)

# Test file name patterns (same set as post_tool_use.py TEST_NAME_PATTERNS)
_RAMP_UP_TEST_PATTERNS: tuple[str, ...] = (
    "test_", "_test.", ".test.", ".spec.", "_spec.", "Test.", "Tests.", "IT.",
)

# Framework boilerplate entry points (duplicated from post_tool_use.py)
_RAMP_UP_BOILERPLATE: frozenset[str] = frozenset({
    "manage.py", "wsgi.py", "asgi.py", "__main__.py",
    "middleware.ts", "middleware.js",
})

# Go generated file markers
_RAMP_UP_GO_GENERATED_PREFIXES: tuple[str, ...] = ("mock_",)
_RAMP_UP_GO_GENERATED_SUFFIXES: tuple[str, ...] = ("_mock.go", "_gen.go", ".pb.go")

# JS/TS generated file suffixes
_RAMP_UP_JS_GENERATED_SUFFIXES: tuple[str, ...] = (".generated.ts", ".graphql.ts")

# Path keywords for scoring
_RAMP_UP_PATH_SCORE_HIGH: tuple[str, ...] = ("services/", "models/", "app/", "lib/")
_RAMP_UP_PATH_SCORE_MED: tuple[str, ...] = ("src/", "core/", "api/", "controllers/", "handlers/")

# ---------------------------------------------------------------------------
# Runner detection helpers -- pure functions, unit-tested directly
# ---------------------------------------------------------------------------


def _read_json(path: str) -> Optional[dict]:
    """Read and parse a JSON file.  Returns None on any error."""
    try:
        with open(path) as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None


def _read_toml_text(path: str) -> Optional[str]:
    """Read a TOML file as raw text.  Returns None on any error."""
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return None


def detect_python_runner(directory: str, project_root: str) -> Optional[dict]:
    """Detect Python test runner from pyproject.toml.

    Returns a runner dict {command, args, test_location, needs_bootstrap} or None.
    test_location is relative to project_root.
    """
    pyproject_path = os.path.join(directory, "pyproject.toml")
    if not os.path.exists(pyproject_path):
        return None

    text = _read_toml_text(pyproject_path) or ""
    has_pytest = (
        "[tool.pytest" in text
        or "pytest" in text
    )

    raw_loc = _find_test_location(directory, "python") or "tests/"

    # Make test_location relative to project_root
    abs_loc = os.path.join(directory, raw_loc.rstrip("/"))
    rel_loc = os.path.relpath(abs_loc, project_root).replace("\\", "/") + "/"
    # Clean up any redundant "./": relpath returns "." when same dir
    if rel_loc == "./":
        rel_loc = raw_loc

    # Detect Python framework
    framework = None
    if os.path.exists(os.path.join(directory, "manage.py")):
        framework = "django"
    elif "fastapi" in text:
        framework = "fastapi"

    runner: dict = {
        "command": "pytest",
        "args": ["-q"],
        "test_location": rel_loc,
        "needs_bootstrap": not has_pytest,
    }
    if framework:
        runner["framework"] = framework
    return runner


def detect_php_runner(directory: str, project_root: str) -> Optional[dict]:
    """Detect PHP test runner from composer.json and phpunit.xml.

    Returns runner dict or None.  Sets framework='laravel' when artisan + laravel/framework present.
    """
    composer = _read_json(os.path.join(directory, "composer.json"))
    if composer is None:
        return None

    require_dev: dict = composer.get("require-dev", {})
    has_phpunit = any("phpunit" in k for k in require_dev)
    has_config = (
        os.path.exists(os.path.join(directory, "phpunit.xml")) or
        os.path.exists(os.path.join(directory, "phpunit.xml.dist"))
    )
    if not has_phpunit and not has_config:
        return None

    require: dict = composer.get("require", {})
    is_laravel = (
        "laravel/framework" in require and
        os.path.exists(os.path.join(directory, "artisan"))
    )
    runner: dict = {
        "command": "./vendor/bin/phpunit",
        "args": [],
        "test_location": "tests/",
    }
    if is_laravel:
        runner["framework"] = "laravel"
        runner["unit_test_dir"] = "tests/Unit/"
        runner["feature_test_dir"] = "tests/Feature/"
    return runner


def detect_go_runner(directory: str, project_root: str) -> Optional[dict]:
    """Detect Go test runner from go.mod.

    Tests are co-located with source (same package directory).
    """
    if not os.path.exists(os.path.join(directory, "go.mod")):
        return None
    return {
        "command": "go test",
        "args": ["./..."],
        "test_location": ".",
        "style": "colocated",
    }


def detect_ruby_runner(directory: str, project_root: str) -> Optional[dict]:
    """Detect Ruby test runner from Gemfile.

    Returns runner dict (rspec or minitest) or None.  Sets framework='rails' when detected.
    """
    gemfile_path = os.path.join(directory, "Gemfile")
    if not os.path.exists(gemfile_path):
        return None
    try:
        content = open(gemfile_path).read()
    except OSError:
        return None

    has_rspec = "rspec" in content
    has_minitest = "minitest" in content
    if not has_rspec and not has_minitest:
        return None

    is_rails = "rails" in content

    if has_rspec:
        raw_loc = "spec/"
        command = "bundle exec rspec"
    else:
        raw_loc = "test/"
        command = "bundle exec rake test"

    abs_loc = os.path.join(directory, raw_loc.rstrip("/"))
    rel_loc = os.path.relpath(abs_loc, project_root).replace("\\", "/") + "/"
    if rel_loc == "./":
        rel_loc = raw_loc

    runner: dict = {"command": command, "args": [], "test_location": rel_loc}
    if is_rails:
        runner["framework"] = "rails"
    return runner


def detect_rust_runner(directory: str, project_root: str) -> Optional[dict]:
    """Detect Rust test runner from Cargo.toml.

    Tests are inline in source files (#[cfg(test)] modules).
    """
    if not os.path.exists(os.path.join(directory, "Cargo.toml")):
        return None
    return {
        "command": "cargo test",
        "args": [],
        "test_location": "inline",
        "style": "inline",
    }


def detect_java_runner(directory: str, project_root: str) -> Optional[dict]:
    """Detect Java test runner from pom.xml (Maven) or build.gradle (Gradle).

    Returns runner dict or None.  Sets framework='spring' when spring-boot detected.
    """
    has_maven = os.path.exists(os.path.join(directory, "pom.xml"))
    has_gradle = (
        os.path.exists(os.path.join(directory, "build.gradle")) or
        os.path.exists(os.path.join(directory, "build.gradle.kts"))
    )
    if not has_maven and not has_gradle:
        return None

    command = "./mvnw test" if has_maven else "./gradlew test"
    framework = None
    try:
        build_file = "pom.xml" if has_maven else (
            "build.gradle" if os.path.exists(os.path.join(directory, "build.gradle"))
            else "build.gradle.kts"
        )
        content = open(os.path.join(directory, build_file)).read()
        if "spring-boot" in content:
            framework = "spring"
    except OSError:
        pass

    runner: dict = {
        "command": command,
        "args": [],
        "test_location": "src/test/java/",
    }
    if framework:
        runner["framework"] = framework
    return runner


def detect_node_runner(directory: str, project_root: str) -> Optional[dict]:
    """Detect JS/TS test runner from package.json.

    Returns a runner dict {command, args, test_location, needs_bootstrap} or None.
    Prefers vitest over jest when both are present.
    test_location is relative to project_root.
    """
    pkg_path = os.path.join(directory, "package.json")
    pkg = _read_json(pkg_path)
    if pkg is None:
        return None

    scripts: dict = pkg.get("scripts", {})
    dev_deps: dict = pkg.get("devDependencies", {})
    deps: dict = pkg.get("dependencies", {})
    all_deps = {**deps, **dev_deps}

    scripts_text = " ".join(scripts.values())

    has_vitest = "vitest" in all_deps or "vitest" in scripts_text
    has_jest = "jest" in all_deps or "jest" in scripts_text

    raw_loc = _find_test_location(directory, "javascript") or "__tests__/"

    abs_loc = os.path.join(directory, raw_loc.rstrip("/"))
    rel_loc = os.path.relpath(abs_loc, project_root).replace("\\", "/") + "/"
    if rel_loc == "./":
        rel_loc = raw_loc

    command = "vitest" if has_vitest else ("jest" if has_jest else "vitest")
    args = ["run"] if command == "vitest" else ["--passWithNoTests"]

    # Detect JS framework
    framework = None
    if "next" in all_deps:
        framework = "nextjs"
    elif "nuxt" in all_deps or os.path.exists(os.path.join(directory, "nuxt.config.ts")) or os.path.exists(os.path.join(directory, "nuxt.config.js")):
        framework = "nuxt"

    runner: dict = {
        "command": command,
        "args": args,
        "test_location": rel_loc,
        "needs_bootstrap": not (has_vitest or has_jest),
    }
    if framework:
        runner["framework"] = framework
    return runner


def _find_test_location(directory: str, language: str) -> Optional[str]:
    """Return the relative test directory name for the given project dir."""
    candidates: list[str]
    if language == "python":
        candidates = ["tests", "test", "src/tests", "src/test", "testing"]
    else:
        # JavaScript / TypeScript -- includes src-nested variants for unusual layouts
        candidates = ["__tests__", "tests", "test", "spec", "src/__tests__", "src/test", "src/spec"]

    for candidate in candidates:
        if os.path.isdir(os.path.join(directory, candidate)):
            return candidate + "/"
    return None


def find_recent_test_files(
    project_root: str,
    runners: dict,
    max_files: int = 3,
) -> list[str]:
    """Return up to max_files most-recently-modified test file paths (absolute).

    Walks each runner's test_location directory (or the project root for
    colocated styles like Go) looking for files whose name matches the
    language's TEST_FILE_PATTERNS.  Returns paths sorted newest-first.
    """
    candidates: list[tuple[float, str]] = []  # (mtime, abs_path)
    _skip_dirs = {"node_modules", ".venv", "venv", "__pycache__", "dist", "build", "vendor"}

    for language, runner in runners.items():
        patterns = TEST_FILE_PATTERNS.get(language, [])
        if not patterns:
            continue
        test_loc = runner.get("test_location", "")
        if test_loc in (".", "inline"):
            search_root = project_root
        else:
            search_root = os.path.join(project_root, test_loc.rstrip("/"))

        if not os.path.isdir(search_root):
            continue

        for dirpath, dirnames, filenames in os.walk(search_root):
            dirnames[:] = [d for d in dirnames if d not in _skip_dirs]
            for filename in filenames:
                if any(fnmatch.fnmatch(filename, pat) for pat in patterns):
                    abs_path = os.path.join(dirpath, filename)
                    try:
                        mtime = os.path.getmtime(abs_path)
                        candidates.append((mtime, abs_path))
                    except OSError:
                        pass

    candidates.sort(key=lambda x: x[0], reverse=True)
    seen: set[str] = set()
    result: list[str] = []
    for _, path in candidates:
        if path not in seen:
            seen.add(path)
            result.append(path)
            if len(result) >= max_files:
                break
    return result


def extract_style_snippet(file_path: str, max_lines: int = 30) -> Optional[str]:
    """Return the first max_lines lines of a test file as a stripped string.

    Returns None if the file cannot be read.
    """
    try:
        with open(file_path, encoding="utf-8", errors="replace") as fh:
            lines = []
            for i, line in enumerate(fh):
                if i >= max_lines:
                    break
                lines.append(line)
        return "".join(lines).rstrip()
    except OSError:
        return None


def detect_custom_helpers(snippets: list[str]) -> list[str]:
    """Detect custom test helper imports in test file snippets.

    Returns a list of import-statement strings (up to 5).  Only flags:
    - Python: ``from conftest import X``
    - JS/TS: ``import { X } from './test-utils'`` or similar helper-named files
    """
    helpers: list[str] = []
    seen: set[str] = set()

    for snippet in snippets:
        # Python -- conftest imports
        for m in re.finditer(
            r"^from\s+conftest\s+import\s+(.+)$", snippet, re.MULTILINE
        ):
            names = m.group(1).strip()
            key = f"conftest:{names}"
            if key not in seen:
                seen.add(key)
                helpers.append(f"`from conftest import {names}`")

        # JS/TS -- imports from local helper-named files
        for m in re.finditer(
            r"^import\s+\{([^}]+)\}\s+from\s+['\"]([^'\"]+)['\"]",
            snippet,
            re.MULTILINE,
        ):
            names = m.group(1).strip()
            module = m.group(2)
            module_base = module.split("/")[-1]
            if _HELPER_MODULE_RE.search(module_base):
                key = f"js:{module}:{names[:40]}"
                if key not in seen:
                    seen.add(key)
                    helpers.append(f"`import {{{names}}} from '{module}'`")

        if len(helpers) >= 5:
            break

    return helpers[:5]


def build_style_context(project_root: str, runners: dict) -> Optional[str]:
    """Sample recent test files and return a style-context block, or None.

    Returns None when no test files exist (e.g. brand-new project).
    """
    recent = find_recent_test_files(project_root, runners, max_files=3)
    if not recent:
        return None

    snippets: list[str] = []
    parts: list[str] = []

    for file_path in recent:
        snippet = extract_style_snippet(file_path, max_lines=30)
        if snippet is None:
            continue
        snippets.append(snippet)
        rel_path = os.path.relpath(file_path, project_root).replace("\\", "/")
        parts.append(f"--- {rel_path} ---\n{snippet}")

    if not parts:
        return None

    custom_helpers = detect_custom_helpers(snippets)

    lines: list[str] = [
        f"tailtest style context ({len(parts)} recent test file(s) sampled):",
        "Match the style, patterns, and conventions shown below when generating tests.",
        "",
    ]
    lines.extend(parts)

    if custom_helpers:
        lines.append("")
        lines.append(
            "Custom test helpers detected -- use these instead of bare"
            " render/mount/instantiation:"
        )
        for h in custom_helpers:
            lines.append(f"  {h}")

    return "\n".join(lines)


def detect_monorepo(project_root: str) -> bool:
    """Return True if this project looks like a monorepo workspace.

    Detects via known workspace config files OR by finding multiple
    package.json files at immediate subdirectory level.
    """
    markers = (
        "pnpm-workspace.yaml",
        "nx.json",
        "turbo.json",
        "lerna.json",
        "rush.json",
    )
    for marker in markers:
        if os.path.exists(os.path.join(project_root, marker)):
            return True

    # Two or more immediate subdirs with their own package manifests
    _skip = {"node_modules", ".venv", "venv", ".git", "dist", "build", "__pycache__", "vendor"}
    count = 0
    try:
        for entry in os.scandir(project_root):
            if not entry.is_dir() or entry.name.startswith(".") or entry.name in _skip:
                continue
            if (
                os.path.exists(os.path.join(entry.path, "package.json"))
                or os.path.exists(os.path.join(entry.path, "pyproject.toml"))
                or os.path.exists(os.path.join(entry.path, "composer.json"))
            ):
                count += 1
                if count >= 2:
                    return True
    except OSError:
        pass
    return False


def scan_packages(project_root: str) -> dict:
    """Scan for per-package runners in a monorepo.

    Returns a dict keyed by the package's relative path (forward-slash):
      {"packages/web": {"typescript": {...}}, "packages/api": {"python": {...}}}

    Scans up to two directory levels deep, skipping common noise directories.
    Each detected package stores runners with test_location relative to project_root
    (same convention as scan_runners).
    """
    packages: dict = {}
    _skip = {
        "node_modules", ".venv", "venv", ".git", "dist", "build",
        "__pycache__", "vendor", ".svelte-kit", ".next", ".nuxt",
    }

    def _try_package(directory: str) -> None:
        rel = os.path.relpath(directory, project_root).replace("\\", "/")
        if rel == ".":
            return
        runners: dict = {}
        py = detect_python_runner(directory, project_root)
        if py:
            runners["python"] = {k: v for k, v in py.items() if k != "needs_bootstrap"}
        node = detect_node_runner(directory, project_root)
        if node:
            key = "typescript" if os.path.exists(
                os.path.join(directory, "tsconfig.json")
            ) else "javascript"
            runners[key] = {k: v for k, v in node.items() if k != "needs_bootstrap"}
        php = detect_php_runner(directory, project_root)
        if php:
            runners["php"] = php
        go_r = detect_go_runner(directory, project_root)
        if go_r:
            runners["go"] = go_r
        ruby = detect_ruby_runner(directory, project_root)
        if ruby:
            runners["ruby"] = ruby
        rust = detect_rust_runner(directory, project_root)
        if rust:
            runners["rust"] = rust
        java = detect_java_runner(directory, project_root)
        if java:
            runners["java"] = java
        if runners:
            packages[rel] = runners

    try:
        for entry in os.scandir(project_root):
            if not entry.is_dir() or entry.name.startswith(".") or entry.name in _skip:
                continue
            _try_package(entry.path)
            # Depth 2
            try:
                for sub in os.scandir(entry.path):
                    if not sub.is_dir() or sub.name.startswith(".") or sub.name in _skip:
                        continue
                    _try_package(sub.path)
            except OSError:
                pass
    except OSError:
        pass

    return packages


def scan_runners(project_root: str) -> dict:
    """Scan project root and immediate subdirectories for runners.

    Returns a runners dict keyed by language:
      {"python": {...}, "typescript": {...}}

    Scans: project_root + one level of subdirectories (to catch
    backend/pyproject.toml, frontend/package.json patterns).
    """
    runners: dict = {}

    def _try_dir(directory: str) -> None:
        py = detect_python_runner(directory, project_root)
        if py and "python" not in runners:
            runners["python"] = py
        node = detect_node_runner(directory, project_root)
        if node and "typescript" not in runners and "javascript" not in runners:
            # Use 'typescript' as the key if tsconfig.json is present
            if os.path.exists(os.path.join(directory, "tsconfig.json")):
                runners["typescript"] = node
            else:
                runners["javascript"] = node
        php = detect_php_runner(directory, project_root)
        if php and "php" not in runners:
            runners["php"] = php
        go_r = detect_go_runner(directory, project_root)
        if go_r and "go" not in runners:
            runners["go"] = go_r
        ruby = detect_ruby_runner(directory, project_root)
        if ruby and "ruby" not in runners:
            runners["ruby"] = ruby
        rust = detect_rust_runner(directory, project_root)
        if rust and "rust" not in runners:
            runners["rust"] = rust
        java = detect_java_runner(directory, project_root)
        if java and "java" not in runners:
            runners["java"] = java

    _try_dir(project_root)

    # One level of subdirectories
    try:
        for entry in os.scandir(project_root):
            if entry.is_dir() and not entry.name.startswith("."):
                if entry.name in ("node_modules", ".venv", "venv", "dist",
                                  "build", "__pycache__", "vendor"):
                    continue
                _try_dir(entry.path)
    except OSError:
        pass

    return runners


def detect_project_type(project_root: str) -> str:
    """Return a human-readable project type string."""
    if os.path.exists(os.path.join(project_root, "pyproject.toml")):
        return "Python"
    if os.path.exists(os.path.join(project_root, "package.json")):
        if os.path.exists(os.path.join(project_root, "tsconfig.json")):
            return "TypeScript"
        return "JavaScript"
    # Check subdirectories
    for entry in _iter_top_dirs(project_root):
        if os.path.exists(os.path.join(entry, "pyproject.toml")):
            return "Python"
        if os.path.exists(os.path.join(entry, "package.json")):
            return "TypeScript/JavaScript"
    return "Unknown"


def _iter_top_dirs(project_root: str):
    """Yield paths of immediate subdirectories (excluding common noise)."""
    skip = {"node_modules", ".venv", "venv", "dist", "build", "__pycache__", "vendor"}
    try:
        for entry in os.scandir(project_root):
            if entry.is_dir() and not entry.name.startswith(".") and entry.name not in skip:
                yield entry.path
    except OSError:
        pass


def read_depth(project_root: str) -> str:
    """Read depth from .tailtest/config.json.  Defaults to 'standard'."""
    config_path = os.path.join(project_root, ".tailtest", "config.json")
    if os.path.exists(config_path):
        cfg = _read_json(config_path)
        if cfg and cfg.get("depth") in ("simple", "standard", "thorough"):
            return cfg["depth"]
    return "standard"


def make_session_id() -> str:
    """Generate a unique session ID."""
    now = datetime.datetime.now(datetime.timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H-%M-%S")
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{ts}-{suffix}"


def create_session(project_root: str, runners: dict, depth: str) -> dict:
    """Build and write a fresh session.json.  Returns the dict."""
    packages = scan_packages(project_root) if detect_monorepo(project_root) else {}

    session_id = make_session_id()
    session = {
        "session_id": session_id,
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "project_root": project_root,
        "runners": {k: {kk: vv for kk, vv in v.items() if kk != "needs_bootstrap"}
                    for k, v in runners.items()},
        "depth": depth,
        "paused": False,
        "report_path": f".tailtest/reports/{session_id}.md",
        "pending_files": [],
        "touched_files": [],
        "fix_attempts": {},
        "deferred_failures": [],
        "generated_tests": {},
        "packages": packages,
    }
    tailtest_dir = os.path.join(project_root, ".tailtest")
    os.makedirs(tailtest_dir, exist_ok=True)
    session_path = os.path.join(tailtest_dir, "session.json")
    with open(session_path, "w") as fh:
        json.dump(session, fh, indent=2)
        fh.write("\n")
    return session


def _write_orphaned_report(project_root: str) -> None:
    """Write report for previous session if SessionEnd never fired (crash/force-kill).

    Called at startup before creating the new session.json, so the old session
    data is still present on disk.
    """
    session_path = os.path.join(project_root, ".tailtest", "session.json")
    if not os.path.exists(session_path):
        return
    try:
        with open(session_path) as fh:
            old = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return

    report_path = old.get("report_path")
    if not report_path:
        return
    abs_report = os.path.join(project_root, report_path)
    if os.path.exists(abs_report):
        return  # Already written by SessionEnd
    if not old.get("generated_tests"):
        return  # Nothing to report

    # Build minimal report from saved session state
    runners: dict = old.get("runners", {})
    depth: str = old.get("depth", "standard")
    started_at: str = old.get("started_at", "")
    fix_attempts: dict = old.get("fix_attempts", {})
    deferred_failures: list = old.get("deferred_failures", [])
    generated_tests: dict = old.get("generated_tests", {})

    runner_parts = [f"{lang}/{info.get('command', '?')}" for lang, info in runners.items()]
    runner_str = ", ".join(runner_parts) if runner_parts else "no runner"

    lines = [f"# tailtest session -- {started_at}", "",
             f"Runner: {runner_str}  |  Depth: {depth}", "",
             "## Files tested", "",
             "| File | Test file | Result |",
             "|---|---|---|"]

    deferred_paths = {d["file"] for d in deferred_failures if isinstance(d, dict)}
    counts = {"passed": 0, "fixed": 0, "deferred": 0, "unresolved": 0}

    for source_path, test_path in sorted(generated_tests.items()):
        attempts = fix_attempts.get(source_path, 0)
        if source_path in deferred_paths:
            status = "deferred"
            counts["deferred"] += 1
        elif attempts == 0:
            status = "passed"
            counts["passed"] += 1
        elif attempts >= 3:
            status = "unresolved"
            counts["unresolved"] += 1
        else:
            status = f"fixed ({attempts} attempt(s))"
            counts["fixed"] += 1
        lines.append(f"| {source_path} | {test_path} | {status} |")

    total = len(generated_tests)
    parts = [f"{total} file(s) tested"]
    if counts["passed"]: parts.append(f"{counts['passed']} passed")
    if counts["fixed"]: parts.append(f"{counts['fixed']} fixed")
    if counts["deferred"]: parts.append(f"{counts['deferred']} deferred")
    if counts["unresolved"]: parts.append(f"{counts['unresolved']} unresolved")
    lines.extend(["", "## Summary", "  |  ".join(parts)])

    content = "\n".join(lines) + "\n"
    try:
        os.makedirs(os.path.dirname(abs_report), exist_ok=True)
        with open(abs_report, "w") as fh:
            fh.write(content)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Ramp-up scan -- first-session coverage bootstrap
# ---------------------------------------------------------------------------


def load_ignore_patterns(project_root: str) -> list[str]:
    """Read .tailtest-ignore from project root.  Returns [] if absent.

    Duplicated from post_tool_use.py -- the two hooks share no import path.
    """
    ignore_path = os.path.join(project_root, ".tailtest-ignore")
    if not os.path.exists(ignore_path):
        return []
    patterns: list[str] = []
    try:
        with open(ignore_path) as fh:
            for line in fh:
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    patterns.append(stripped)
    except OSError:
        pass
    return patterns


def is_first_session(project_root: str) -> bool:
    """True if .tailtest/reports/ has no .md files and no ramp-up sentinel.

    Reports are written only after actual test generation (via /summary,
    SessionEnd, or _write_orphaned_report).  An empty reports dir means
    no coverage has ever been produced -- ramp-up should fire.
    """
    reports_dir = os.path.join(project_root, ".tailtest", "reports")
    if not os.path.isdir(reports_dir):
        return True
    try:
        for entry in os.scandir(reports_dir):
            if entry.name == RAMP_UP_SENTINEL:
                return False
            if entry.name.endswith(".md"):
                return False
    except OSError:
        return True
    return True


def read_ramp_up_limit(project_root: str) -> int:
    """Read ramp_up_limit from .tailtest/config.json.

    Default 7.  Set to 0 to disable ramp-up entirely.  Clamped to [1, 15].
    """
    config_path = os.path.join(project_root, ".tailtest", "config.json")
    cfg = _read_json(config_path)
    if cfg is None:
        return 7
    try:
        raw = cfg.get("ramp_up_limit", 7)
        val = int(raw)
        if val == 0:
            return 0
        return max(1, min(15, val))
    except (TypeError, ValueError):
        return 7


def _git_commit_counts(project_root: str) -> dict[str, int]:
    """Return {rel_path: commit_count} for all files.  Single git call.

    --no-merges: prevents merge commits from inflating scores.
    --max-count=500: bounds execution time on large repos.
    Returns {} if git is unavailable, times out, or fails for any reason.
    """
    if not os.path.isdir(os.path.join(project_root, ".git")):
        return {}
    try:
        result = subprocess.run(
            [
                "git", "-C", project_root, "log",
                "--name-only", "--pretty=format:", "--no-merges", "--max-count=500",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        counts: dict[str, int] = {}
        for line in result.stdout.splitlines():
            line = line.strip()
            if line:
                counts[line] = counts.get(line, 0) + 1
        return counts
    except Exception:
        return {}


def _is_ramp_up_filtered(
    rel_path: str,
    fname: str,
    ignore_patterns: list[str],
) -> bool:
    """True when the file should be excluded from ramp-up candidates.

    Mirrors the path/name checks from post_tool_use.is_filtered() without
    reading file content.  Content checks (re-export barrels, type-only TS)
    are delegated to Claude's Step 2 filter at processing time.
    """
    lower = fname.lower()

    # .tailtest-ignore patterns
    for pat in ignore_patterns:
        if pat.endswith("/"):
            if rel_path.startswith(pat):
                return True
        elif fnmatch.fnmatch(rel_path, pat) or fnmatch.fnmatch(fname, pat):
            return True

    # Path fragment exclusions
    for frag in _RAMP_UP_SKIP_FRAGMENTS:
        if frag in rel_path:
            return True

    # Build-tool config compound suffixes
    for suffix in (".config.js", ".config.ts", ".config.mjs", ".config.cjs",
                   ".config.jsx", ".config.tsx"):
        if lower.endswith(suffix):
            return True

    # Test file name patterns
    if any(pat in fname for pat in _RAMP_UP_TEST_PATTERNS):
        return True

    # Framework boilerplate
    if fname in _RAMP_UP_BOILERPLATE:
        return True

    # Go generated files
    if fname.endswith(".go"):
        if any(fname.startswith(p) for p in _RAMP_UP_GO_GENERATED_PREFIXES):
            return True
        if any(fname.endswith(s) for s in _RAMP_UP_GO_GENERATED_SUFFIXES):
            return True

    # JS/TS generated files
    if any(fname.endswith(s) for s in _RAMP_UP_JS_GENERATED_SUFFIXES):
        return True

    # Dockerfile (no extension)
    if lower == "dockerfile" or lower.endswith(".dockerfile"):
        return True

    return False


def _has_existing_test(basename: str, abs_source_path: str, project_root: str) -> bool:
    """True if any test file for this source already exists.

    Checks co-located siblings first (Go _test.go, TS/JS .test.ts, etc.),
    then standard test directories.
    """
    source_dir = os.path.dirname(abs_source_path)
    siblings = [
        f"{basename}_test.go",
        f"{basename}.test.ts", f"{basename}.spec.ts",
        f"{basename}.test.tsx", f"{basename}.spec.tsx",
        f"{basename}.test.js", f"{basename}.spec.js",
        f"{basename}.test.jsx", f"{basename}.spec.jsx",
    ]
    for sibling in siblings:
        if os.path.exists(os.path.join(source_dir, sibling)):
            return True

    stems = {
        f"test_{basename}", f"{basename}_test",
        f"{basename}.test", f"{basename}.spec",
        f"{basename}_spec",
        f"{basename}Test", f"{basename}Tests",
    }
    for tdir in ("tests/", "__tests__/", "spec/", "test/", "src/test/"):
        abs_tdir = os.path.join(project_root, tdir)
        if not os.path.isdir(abs_tdir):
            continue
        try:
            for _root, _dirs, files in os.walk(abs_tdir):
                for f in files:
                    if os.path.splitext(f)[0] in stems:
                        return True
        except OSError:
            pass
    return False


def _score_candidate(
    rel_path: str,
    basename: str,
    abs_path: str,
    commit_counts: dict[str, int],
    project_root: str,
) -> int:
    """Score a source file for ramp-up selection.  Higher = more important.

    score = git_score + path_score + size_score - existing_test_penalty

    git_score:   min(commit_count, 20) * 2   (max +40)
    path_score:  +30 services/models/app/lib; +20 src/core/api/controllers/handlers
    size_score:  -20 <30 lines; 0 30-79; +30 80-800; +10 801-1500; 0 >1500
    penalty:     -100 if a test already exists (effectively excludes the file)
    """
    rel_lower = rel_path.lower()

    git_score = min(commit_counts.get(rel_path, 0), 20) * 2

    if any(frag in rel_lower for frag in _RAMP_UP_PATH_SCORE_HIGH):
        path_score = 30
    elif any(frag in rel_lower for frag in _RAMP_UP_PATH_SCORE_MED):
        path_score = 20
    else:
        path_score = 0

    size_score = 0
    try:
        with open(abs_path, encoding="utf-8", errors="ignore") as fh:
            line_count = sum(1 for _ in fh)
        if line_count < 30:
            size_score = -20
        elif line_count < 80:
            size_score = 0
        elif line_count <= 800:
            size_score = 30
        elif line_count <= 1500:
            size_score = 10
        # else: >1500 lines, 0
    except OSError:
        pass

    penalty = 100 if _has_existing_test(basename, abs_path, project_root) else 0

    return git_score + path_score + size_score - penalty


def ramp_up_scan(project_root: str, runners: dict, session: dict) -> None:
    """Pre-populate pending_files with the project's most important source files.

    Called only on the first-ever startup (is_first_session() returned True).
    Modifies `session` in-place and rewrites .tailtest/session.json.
    If nothing qualifies, returns silently without setting ramp_up.
    """
    limit = read_ramp_up_limit(project_root)
    if limit == 0:
        return

    # Write sentinel before scanning so that if Claude crashes before any tests
    # are generated (no .md report written), the next startup won't re-fire ramp-up.
    reports_dir = os.path.join(project_root, ".tailtest", "reports")
    try:
        os.makedirs(reports_dir, exist_ok=True)
        open(os.path.join(reports_dir, RAMP_UP_SENTINEL), "w").close()  # noqa: WPS515
    except OSError:
        pass  # sentinel is best-effort; continue regardless

    ignore_patterns = load_ignore_patterns(project_root)
    commit_counts = _git_commit_counts(project_root)

    candidates: list[tuple[int, str, str]] = []  # (score, rel_path, language)

    for root, dirnames, files in os.walk(project_root):
        # Prune known-noise dirs in-place -- this is the traversal performance bound
        dirnames[:] = [
            d for d in dirnames
            if d not in RAMP_UP_SKIP_DIRS and not d.startswith(".")
        ]

        for fname in files:
            abs_path = os.path.join(root, fname)

            # Skip symlinks -- may point outside the project
            if os.path.islink(abs_path):
                continue

            rel_path = os.path.relpath(abs_path, project_root).replace("\\", "/")

            language = RAMP_UP_EXT_MAP.get(os.path.splitext(fname)[1].lower())
            if not language:
                continue

            if _is_ramp_up_filtered(rel_path, fname, ignore_patterns):
                continue

            basename = os.path.splitext(fname)[0]
            score = _score_candidate(rel_path, basename, abs_path, commit_counts, project_root)
            if score > 0:
                candidates.append((score, rel_path, language))

    if not candidates:
        return  # Nothing qualifies -- silent, ramp_up flag NOT set in session

    candidates.sort(key=lambda x: x[0], reverse=True)
    top = candidates[:limit]

    session["pending_files"] = [
        {"path": rel_path, "language": lang, "status": "ramp-up"}
        for _, rel_path, lang in top
    ]
    session["ramp_up"] = True

    session_path = os.path.join(project_root, ".tailtest", "session.json")
    try:
        with open(session_path, "w") as fh:
            json.dump(session, fh, indent=2)
            fh.write("\n")
    except OSError:
        pass  # Never crash startup


def read_claude_md(plugin_root: str) -> str:
    """Read CLAUDE.md from plugin root.  Returns empty string if not found."""
    claude_md_path = os.path.join(plugin_root, "CLAUDE.md")
    try:
        with open(claude_md_path) as fh:
            return fh.read()
    except OSError:
        return ""


def build_bootstrap_note(runners: dict) -> Optional[str]:
    """Return a bootstrap instruction if any runner needs setup, else None."""
    notes: list[str] = []
    for lang, info in runners.items():
        if info.get("needs_bootstrap"):
            if lang == "python":
                notes.append(
                    "No pytest found in pyproject.toml -- silently add "
                    "pytest to [project.optional-dependencies.dev] before "
                    "generating the first test."
                )
            elif lang in ("typescript", "javascript"):
                notes.append(
                    "No vitest/jest found in package.json -- silently add "
                    "vitest and a minimal vitest.config.ts before generating "
                    "the first test. Check package.json dependencies first: "
                    "if react/vue/next is present use environment: 'jsdom', "
                    "otherwise environment: 'node'."
                )
    return "\n".join(notes) if notes else None


def build_startup_context(
    project_root: str,
    runners: dict,
    depth: str,
    claude_md: str,
    ramp_up_count: int = 0,
) -> str:
    """Build the full additionalContext payload for startup/resume."""
    lines: list[str] = []

    if claude_md:
        lines.append(claude_md)
        lines.append("")

    runner_summaries = []
    for lang, info in runners.items():
        cmd = info.get("command", "?")
        loc = info.get("test_location", "tests/")
        runner_summaries.append(f"{lang}: {cmd} (tests in {loc})")

    runner_text = ", ".join(runner_summaries) if runner_summaries else "none detected"
    lines.append(
        f"tailtest: session started. Project root: {project_root}. "
        f"Runners: {runner_text}. Depth: {depth}."
    )

    if ramp_up_count > 0:
        lines.append(
            f"tailtest ramp-up: first session detected -- {ramp_up_count} file(s) "
            f"queued for initial coverage scan."
        )

    bootstrap = build_bootstrap_note(runners)
    if bootstrap:
        lines.append("")
        lines.append("tailtest bootstrap needed:")
        lines.append(bootstrap)

    style_ctx = build_style_context(project_root, runners)
    if style_ctx:
        lines.append("")
        lines.append(style_ctx)

    return "\n".join(lines)


def build_compact_context(
    project_root: str,
    runners: dict,
    depth: str,
    pending_files: list[dict],
    fix_attempts: dict,
    claude_md: str,
) -> str:
    """Build the additionalContext payload for post-compaction re-injection."""
    lines: list[str] = []

    if claude_md:
        lines.append(claude_md)
        lines.append("")

    runner_summaries = []
    for lang, info in runners.items():
        cmd = info.get("command", "?")
        loc = info.get("test_location", "tests/")
        runner_summaries.append(f"{lang}: {cmd} (tests in {loc})")

    runner_text = ", ".join(runner_summaries) if runner_summaries else "none"
    lines.append(
        f"tailtest: session resumed after compaction. "
        f"Runners: {runner_text}. Depth: {depth}."
    )

    if pending_files:
        pending_paths = ", ".join(p["path"] for p in pending_files)
        lines.append(f"tailtest: {len(pending_files)} file(s) pending from before compaction: {pending_paths}.")
        lines.append("Read .tailtest/session.json and process pending files before responding to the user.")
    if fix_attempts:
        attempts_text = ", ".join(f"{k}: {v}" for k, v in fix_attempts.items())
        lines.append(f"tailtest: fix attempts this session: {attempts_text}.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    try:
        raw = sys.stdin.read()
        event: dict = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        event = {}

    source: str = event.get("source", "startup")
    project_root: str = event.get("cwd", os.getcwd())

    # Resolve plugin root: CLAUDE_PLUGIN_ROOT env var, else parent of this file
    plugin_root = os.environ.get(
        "CLAUDE_PLUGIN_ROOT",
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )

    claude_md = read_claude_md(plugin_root)

    if source == "compact":
        # Re-inject instructions + session state after compaction
        session_path = os.path.join(project_root, ".tailtest", "session.json")
        session: dict = {}
        if os.path.exists(session_path):
            try:
                with open(session_path) as fh:
                    session = json.load(fh)
            except (json.JSONDecodeError, OSError):
                pass

        runners = session.get("runners", {})
        depth = session.get("depth", "standard")
        pending_files = session.get("pending_files", [])
        fix_attempts = session.get("fix_attempts", {})

        context = build_compact_context(
            project_root, runners, depth, pending_files, fix_attempts, claude_md
        )
    else:
        # startup or resume -- full project orientation
        _write_orphaned_report(project_root)
        runners = scan_runners(project_root)
        depth = read_depth(project_root)
        first_session = is_first_session(project_root)
        session: dict = {}
        try:
            session = create_session(project_root, runners, depth)
        except OSError:
            pass

        ramp_up_count = 0
        if source == "startup" and first_session and session:
            try:
                ramp_up_scan(project_root, runners, session)
                ramp_up_count = len(session.get("pending_files", []))
            except Exception:
                ramp_up_count = 0  # Never crash startup

        context = build_startup_context(
            project_root, runners, depth, claude_md,
            ramp_up_count=ramp_up_count,
        )

    # For SessionStart, plain stdout is added to Claude's context directly.
    # hookSpecificOutput.additionalContext does NOT work for SessionStart.
    if context:
        print(context)


if __name__ == "__main__":
    main()
