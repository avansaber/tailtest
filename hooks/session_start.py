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
import json
import os
import random
import string
import sys
from typing import Optional

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
        candidates = ["tests", "test", "src/tests"]
    else:
        candidates = ["__tests__", "tests", "test", "spec"]

    for candidate in candidates:
        if os.path.isdir(os.path.join(directory, candidate)):
            return candidate + "/"
    return None


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
    session = {
        "session_id": make_session_id(),
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "project_root": project_root,
        "runners": {k: {kk: vv for kk, vv in v.items() if kk != "needs_bootstrap"}
                    for k, v in runners.items()},
        "depth": depth,
        "pending_files": [],
        "touched_files": [],
        "fix_attempts": {},
        "deferred_failures": [],
    }
    tailtest_dir = os.path.join(project_root, ".tailtest")
    os.makedirs(tailtest_dir, exist_ok=True)
    session_path = os.path.join(tailtest_dir, "session.json")
    with open(session_path, "w") as fh:
        json.dump(session, fh, indent=2)
        fh.write("\n")
    return session


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

    bootstrap = build_bootstrap_note(runners)
    if bootstrap:
        lines.append("")
        lines.append("tailtest bootstrap needed:")
        lines.append(bootstrap)

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
        runners = scan_runners(project_root)
        depth = read_depth(project_root)
        try:
            create_session(project_root, runners, depth)
        except OSError:
            pass

        context = build_startup_context(project_root, runners, depth, claude_md)

    # For SessionStart, plain stdout is added to Claude's context directly.
    # hookSpecificOutput.additionalContext does NOT work for SessionStart.
    if context:
        print(context)


if __name__ == "__main__":
    main()
