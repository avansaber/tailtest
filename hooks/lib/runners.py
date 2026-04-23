"""Runner detection -- scan project manifests to identify test commands and locations."""

from __future__ import annotations

import datetime
import json
import os
import random
import string
from typing import Optional


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


def _detect_py_web_framework(directory: str, text: str) -> Optional[str]:
    """Pick between flask and fastapi from pyproject deps + entry-point inspection.

    When only one is declared, return it directly. When both are declared (rare,
    e.g. mid-migration), inspect common entry-point files for the actual framework
    instantiation. Falls back to fastapi if inspection is ambiguous, preserving
    pre-V12.2 behavior.
    """
    has_fastapi = "fastapi" in text
    has_flask = "flask" in text

    if has_flask and not has_fastapi:
        return "flask"
    if has_fastapi and not has_flask:
        return "fastapi"
    if not has_flask and not has_fastapi:
        return None

    # Both present; inspect entry-point files for Flask() or FastAPI() instantiation
    entry_point_names = (
        "app.py", "main.py", "wsgi.py", "asgi.py",
        "src/app.py", "src/main.py",
    )
    for name in entry_point_names:
        path = os.path.join(directory, name)
        if not os.path.exists(path):
            continue
        try:
            with open(path) as fh:
                content = fh.read()
        except OSError:
            continue
        if "FastAPI(" in content:
            return "fastapi"
        if "Flask(__name__" in content or "Flask('" in content or 'Flask("' in content:
            return "flask"

    return "fastapi"


def detect_python_runner(directory: str, project_root: str) -> Optional[dict]:
    """Detect Python test runner from pyproject.toml."""
    pyproject_path = os.path.join(directory, "pyproject.toml")
    if not os.path.exists(pyproject_path):
        return None

    text = _read_toml_text(pyproject_path) or ""
    has_pytest = "[tool.pytest" in text or "pytest" in text
    has_pytest_asyncio = "pytest-asyncio" in text

    raw_loc = _find_test_location(directory, "python") or "tests/"

    abs_loc = os.path.join(directory, raw_loc.rstrip("/"))
    rel_loc = os.path.relpath(abs_loc, project_root).replace("\\", "/") + "/"
    if rel_loc == "./":
        rel_loc = raw_loc

    framework = None
    if os.path.exists(os.path.join(directory, "manage.py")):
        framework = "django"
    else:
        framework = _detect_py_web_framework(directory, text)

    runner: dict = {
        "command": "pytest",
        "args": ["-q"],
        "test_location": rel_loc,
        "needs_bootstrap": not has_pytest,
    }
    if framework:
        runner["framework"] = framework
    if has_pytest_asyncio:
        runner["async_framework"] = "pytest-asyncio"
    return runner


def detect_php_runner(directory: str, project_root: str) -> Optional[dict]:
    """Detect PHP test runner from composer.json and phpunit.xml."""
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
    """Detect Go test runner from go.mod."""
    if not os.path.exists(os.path.join(directory, "go.mod")):
        return None
    return {
        "command": "go test",
        "args": ["./..."],
        "test_location": ".",
        "style": "colocated",
    }


def detect_ruby_runner(directory: str, project_root: str) -> Optional[dict]:
    """Detect Ruby test runner from Gemfile."""
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
    """Detect Rust test runner from Cargo.toml."""
    if not os.path.exists(os.path.join(directory, "Cargo.toml")):
        return None
    return {
        "command": "cargo test",
        "args": [],
        "test_location": "inline",
        "style": "inline",
    }


def detect_java_runner(directory: str, project_root: str) -> Optional[dict]:
    """Detect Java test runner from pom.xml (Maven) or build.gradle (Gradle)."""
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

    # V12.3: prefer src/test/kotlin/ for Kotlin-only test trees.
    # Mixed Java+Kotlin projects keep src/test/java/ as the default; the rule
    # file's Kotlin Path row overrides per-source-file extension.
    has_java_tests = os.path.isdir(os.path.join(directory, "src", "test", "java"))
    has_kotlin_tests = os.path.isdir(os.path.join(directory, "src", "test", "kotlin"))
    if has_kotlin_tests and not has_java_tests:
        test_location = "src/test/kotlin/"
    else:
        test_location = "src/test/java/"

    runner: dict = {
        "command": command,
        "args": [],
        "test_location": test_location,
    }
    if framework:
        runner["framework"] = framework
    return runner


def detect_node_runner(directory: str, project_root: str) -> Optional[dict]:
    """Detect JS/TS test runner from package.json."""
    pkg_path = os.path.join(directory, "package.json")
    pkg = _read_json(pkg_path)
    if pkg is None:
        return None

    scripts: dict = pkg.get("scripts", {})
    dev_deps: dict = pkg.get("devDependencies", {})
    deps: dict = pkg.get("dependencies", {})
    all_deps = {**deps, **dev_deps}

    scripts_text = " ".join(scripts.values())
    test_script = scripts.get("test", "")
    has_bunfig = os.path.exists(os.path.join(directory, "bunfig.toml"))

    has_vitest = "vitest" in all_deps or "vitest" in scripts_text
    has_jest = "jest" in all_deps or "jest" in scripts_text
    has_bun = "bun test" in test_script or has_bunfig

    raw_loc = _find_test_location(directory, "javascript") or "__tests__/"

    abs_loc = os.path.join(directory, raw_loc.rstrip("/"))
    rel_loc = os.path.relpath(abs_loc, project_root).replace("\\", "/") + "/"
    if rel_loc == "./":
        rel_loc = raw_loc

    # Precedence (highest to lowest):
    # 1. Explicit `test` script names a runner
    # 2. Runner is in deps (vitest > jest legacy preference)
    # 3. bunfig.toml exists (tiebreaker for bun)
    # 4. Default vitest fallback
    if "bun test" in test_script:
        command, args = "bun test", []
    elif "vitest" in test_script:
        command, args = "vitest", ["run"]
    elif "jest" in test_script:
        command, args = "jest", ["--passWithNoTests"]
    elif has_vitest:
        command, args = "vitest", ["run"]
    elif has_jest:
        command, args = "jest", ["--passWithNoTests"]
    elif has_bunfig:
        command, args = "bun test", []
    else:
        command, args = "vitest", ["run"]

    framework = None
    if "@nestjs/core" in all_deps:
        framework = "nestjs"
    elif "next" in all_deps:
        framework = "nextjs"
    elif "nuxt" in all_deps or os.path.exists(os.path.join(directory, "nuxt.config.ts")) or os.path.exists(os.path.join(directory, "nuxt.config.js")):
        framework = "nuxt"

    runner: dict = {
        "command": command,
        "args": args,
        "test_location": rel_loc,
        "needs_bootstrap": not (has_vitest or has_jest or has_bun),
    }
    if framework:
        runner["framework"] = framework
    return runner


def detect_deno_runner(directory: str, project_root: str) -> Optional[dict]:
    """Detect Deno test runner from deno.json or deno.jsonc."""
    has_deno_json = (
        os.path.exists(os.path.join(directory, "deno.json")) or
        os.path.exists(os.path.join(directory, "deno.jsonc"))
    )
    if not has_deno_json:
        return None
    return {
        "command": "deno test",
        "args": [],
        "test_location": ".",
        "style": "colocated",
    }


def _find_test_location(directory: str, language: str) -> Optional[str]:
    """Return the relative test directory name for the given project dir."""
    candidates: list[str]
    if language == "python":
        candidates = ["tests", "test", "src/tests", "src/test", "testing"]
    else:
        candidates = ["__tests__", "tests", "test", "spec", "src/__tests__", "src/test", "src/spec"]

    for candidate in candidates:
        if os.path.isdir(os.path.join(directory, candidate)):
            return candidate + "/"
    return None


def detect_monorepo(project_root: str) -> bool:
    """Return True if this project looks like a monorepo workspace."""
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
    """Scan for per-package runners in a monorepo."""
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
        else:
            deno = detect_deno_runner(directory, project_root)
            if deno:
                runners["typescript"] = deno
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
    """Scan project root and immediate subdirectories for runners."""
    runners: dict = {}

    def _try_dir(directory: str) -> None:
        py = detect_python_runner(directory, project_root)
        if py and "python" not in runners:
            runners["python"] = py
        node = detect_node_runner(directory, project_root)
        if node and "typescript" not in runners and "javascript" not in runners:
            if os.path.exists(os.path.join(directory, "tsconfig.json")):
                runners["typescript"] = node
            else:
                runners["javascript"] = node
        elif "typescript" not in runners and "javascript" not in runners:
            deno = detect_deno_runner(directory, project_root)
            if deno:
                runners["typescript"] = deno
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
        "last_failures": [],
        "scenario_log": [],
        "complexity_scores": {},
    }
    tailtest_dir = os.path.join(project_root, ".tailtest")
    os.makedirs(tailtest_dir, exist_ok=True)
    session_path = os.path.join(tailtest_dir, "session.json")
    with open(session_path, "w") as fh:
        json.dump(session, fh, indent=2)
        fh.write("\n")
    return session
