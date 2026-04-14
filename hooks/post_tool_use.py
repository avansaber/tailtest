#!/usr/bin/env python3
"""tailtest PostToolUse hook -- the heartbeat.

Fires after every Write/Edit/MultiEdit.  Applies the intelligence filter.
Passes: appends the file to pending_files in .tailtest/session.json and
emits an additionalContext note.  Filtered: silent exit 0.

Target: < 1 second.  No LLM calls.  One optional git subprocess.
"""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import sys
from typing import Optional

# ---------------------------------------------------------------------------
# Extension -> language mapping
# ---------------------------------------------------------------------------

LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".pyx": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".php": "php",
    ".go": "go",
    ".rb": "ruby",
    ".rs": "rust",
    ".java": "java",
    ".cs": "csharp",
    ".swift": "swift",
    ".kt": "kotlin",
    ".kts": "kotlin",
}

# ---------------------------------------------------------------------------
# Intelligence filter constants
# ---------------------------------------------------------------------------

SKIP_EXTENSIONS: frozenset[str] = frozenset({
    # Config / data
    ".yaml", ".yml", ".json", ".toml", ".env", ".ini", ".lock",
    ".cfg", ".conf", ".properties", ".plist",
    # Docs
    ".md", ".rst", ".txt", ".adoc", ".asciidoc",
    # Templates / markup
    ".html", ".htm", ".jinja", ".jinja2", ".ejs", ".hbs", ".njk",
    ".twig", ".mustache", ".erb", ".haml",
    # GraphQL schemas
    ".graphql", ".gql",
    # Infrastructure-as-code
    ".tf", ".hcl", ".tfvars",
    # Images / media
    ".svg", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp",
    ".mp4", ".mp3", ".wav", ".pdf",
    # Styles
    ".css", ".scss", ".sass", ".less", ".styl",
    # Data formats
    ".xml", ".xsd", ".wsdl", ".csv", ".tsv",
    # Protocols / codegen sources
    ".proto", ".thrift", ".avsc",
    # Shell scripts (no standard test runner for hook use)
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".cmd",
    # SQL
    ".sql",
    # Dockerfile handled via filename check below
})

# Build-tool config compound suffixes (checked before extension)
BUILD_CONFIG_SUFFIXES: tuple[str, ...] = (
    ".config.js",
    ".config.ts",
    ".config.mjs",
    ".config.cjs",
    ".config.jsx",
    ".config.tsx",
)

# Path fragments that indicate non-testable directories
SKIP_PATH_FRAGMENTS: tuple[str, ...] = (
    "node_modules/",
    ".venv/",
    "venv/",
    ".env/",
    "env/",
    "dist/",
    "build/",
    "generated/",
    ".git/",
    "vendor/",
    "migrations/",
    "db/migrate/",
    "database/migrations/",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    "target/",
    ".cargo/",
    "coverage/",
    ".nyc_output/",
    ".next/",
    ".nuxt/",
    ".svelte-kit/",
    "k8s/",
    "deploy/",
    "infra/",
)

# Test file name substrings
TEST_NAME_PATTERNS: tuple[str, ...] = (
    "test_",
    "_test.",
    ".test.",
    ".spec.",
    "_spec.",
    "Test.",
    "Tests.",
    "IT.",
)

# Framework boilerplate entry points
FRAMEWORK_BOILERPLATE: frozenset[str] = frozenset({
    "manage.py",
    "wsgi.py",
    "asgi.py",
    "__main__.py",
    "middleware.ts",
    "middleware.js",
})

# Go generated file markers
GO_GENERATED_PREFIXES: tuple[str, ...] = ("mock_",)
GO_GENERATED_SUFFIXES: tuple[str, ...] = ("_mock.go", "_gen.go", ".pb.go")

# JS/TS generated file suffixes
JS_GENERATED_SUFFIXES: tuple[str, ...] = (".generated.ts", ".graphql.ts")

# Languages that must have a configured runner in session.json to proceed.
# Unlisted languages (python, typescript, javascript) may use the first available runner.
RUNNER_REQUIRED_LANGUAGES: frozenset[str] = frozenset({"php", "go", "ruby", "rust", "java"})


# ---------------------------------------------------------------------------
# Pure helper functions (unit-tested directly)
# ---------------------------------------------------------------------------


def _norm(path: str) -> str:
    """Normalise path separators to forward-slash."""
    return path.replace("\\", "/")


def detect_language(file_path: str) -> Optional[str]:
    """Return the language name for a file path, or None if not recognised."""
    _, ext = os.path.splitext(file_path)
    return LANGUAGE_MAP.get(ext.lower())


def is_test_file(rel_path: str) -> bool:
    """Return True if the filename looks like a test file."""
    name = os.path.basename(rel_path)
    return any(pat in name for pat in TEST_NAME_PATTERNS)


def is_filtered(
    file_path: str,
    project_root: str,
    ignore_patterns: list[str],
) -> bool:
    """Return True when the file should be silently skipped.

    Never reads file content -- content-based checks (re-export barrels,
    type-only TS files, Server Components) are Claude's responsibility.
    """
    abs_path = os.path.abspath(file_path)
    rel_path = _norm(os.path.relpath(abs_path, project_root))
    name = os.path.basename(rel_path)
    lower_name = name.lower()

    # 1. .tailtest-ignore patterns (gitignore-style)
    for pat in ignore_patterns:
        if pat.endswith("/"):
            # Directory pattern: "scripts/" matches scripts/deploy.py, scripts/a/b.py, etc.
            if rel_path.startswith(pat):
                return True
        elif fnmatch.fnmatch(rel_path, pat) or fnmatch.fnmatch(name, pat):
            return True

    # 2. Path fragments (directory-level exclusions)
    for frag in SKIP_PATH_FRAGMENTS:
        if frag in rel_path:
            return True

    # 3. Compound config suffixes before single-ext check
    for suffix in BUILD_CONFIG_SUFFIXES:
        if lower_name.endswith(suffix):
            return True

    # 4. Single-extension exclusions
    _, ext = os.path.splitext(name)
    if ext.lower() in SKIP_EXTENSIONS:
        return True

    # 5. Dockerfile (no extension)
    if lower_name in ("dockerfile",) or lower_name.endswith(".dockerfile"):
        return True

    # 6. Test files
    if is_test_file(rel_path):
        return True

    # 7. Framework boilerplate
    if name in FRAMEWORK_BOILERPLATE:
        return True

    # 8. Go generated files
    if name.endswith(".go"):
        if any(name.startswith(p) for p in GO_GENERATED_PREFIXES):
            return True
        if any(name.endswith(s) for s in GO_GENERATED_SUFFIXES):
            return True

    # 9. JS/TS generated files
    if any(name.endswith(s) for s in JS_GENERATED_SUFFIXES):
        return True

    return False


def load_ignore_patterns(project_root: str) -> list[str]:
    """Read .tailtest-ignore from project root.  Returns [] if absent."""
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


def is_git_tracked(file_path: str, project_root: str) -> Optional[bool]:
    """Return True if tracked by git, False if untracked, None if git unavailable."""
    if not os.path.isdir(os.path.join(project_root, ".git")):
        return None
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", os.path.abspath(file_path)],
            capture_output=True,
            cwd=project_root,
            timeout=2,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def determine_status(
    file_path: str,
    project_root: str,
    touched_files: list[str],
) -> str:
    """Return 'new-file' or 'legacy-file'.

    Git project:  tracked in git -> legacy-file,  untracked -> new-file.
    No-git:       first touch this session -> new-file,  repeat -> legacy-file.
    """
    rel_path = _norm(os.path.relpath(os.path.abspath(file_path), project_root))
    tracked = is_git_tracked(file_path, project_root)
    if tracked is None:
        return "legacy-file" if rel_path in touched_files else "new-file"
    return "legacy-file" if tracked else "new-file"


def load_session(project_root: str) -> dict:
    """Load .tailtest/session.json.  Returns minimal empty dict if absent."""
    session_path = os.path.join(project_root, ".tailtest", "session.json")
    if os.path.exists(session_path):
        try:
            with open(session_path) as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "pending_files": [],
        "touched_files": [],
        "runners": {},
        "fix_attempts": {},
        "deferred_failures": [],
    }


def save_session(project_root: str, session: dict) -> None:
    """Write session dict to .tailtest/session.json."""
    tailtest_dir = os.path.join(project_root, ".tailtest")
    os.makedirs(tailtest_dir, exist_ok=True)
    session_path = os.path.join(tailtest_dir, "session.json")
    with open(session_path, "w") as fh:
        json.dump(session, fh, indent=2)
        fh.write("\n")


def extract_file_path(tool_name: str, tool_input: dict) -> Optional[str]:
    """Extract the file path written or edited from a tool_input dict."""
    if tool_name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return tool_input.get("file_path")
    return None


def get_test_file_path(
    rel_path: str,
    language: str,
    runners: dict,
    project_root: str,
) -> Optional[str]:
    """Return the absolute path of the expected test file for a source file.

    Returns None if the language is unsupported or no runner is found.
    Does NOT check whether the file exists on disk.
    """
    runner_info = runners.get(language)
    if not runner_info and runners and language not in RUNNER_REQUIRED_LANGUAGES:
        runner_info = next(iter(runners.values()))
    if not runner_info:
        return None

    basename = os.path.splitext(os.path.basename(rel_path))[0]

    if language == "rust":
        # Rust tests are inline in the source file -- no separate test file
        return None

    if language == "go":
        # Co-located: {same_dir}/{basename}_test.go
        source_dir = os.path.dirname(rel_path)
        test_filename = f"{basename}_test.go"
        if source_dir:
            return os.path.join(project_root, source_dir, test_filename)
        return os.path.join(project_root, test_filename)

    test_location = runner_info.get("test_location", "tests/").rstrip("/")

    if language == "python":
        test_filename = f"test_{basename}.py"
    elif language == "typescript":
        test_filename = f"{basename}.test.ts"
    elif language == "javascript":
        test_filename = f"{basename}.test.js"
    elif language == "ruby":
        # rspec → _spec.rb, minitest → _test.rb
        if "spec" in test_location:
            test_filename = f"{basename}_spec.rb"
        else:
            test_filename = f"{basename}_test.rb"
    elif language == "java":
        test_filename = f"{basename}Test.java"
    elif language == "php":
        # Check both Unit and Feature dirs for existing test files (legacy-file lookup)
        test_filename = f"{basename}Test.php"
        for subdir in ("tests/Unit", "tests/Feature", "tests"):
            candidate = os.path.join(project_root, subdir, test_filename)
            if os.path.exists(candidate):
                return candidate
        # New file: route Unit vs Feature based on source path (Laravel convention)
        is_feature = "/Http/" in rel_path or "/Controllers/" in rel_path
        if is_feature:
            feature_dir = runner_info.get("feature_test_dir", "tests/Feature").rstrip("/")
            return os.path.join(project_root, feature_dir, test_filename)
        unit_dir = runner_info.get("unit_test_dir", "tests/Unit").rstrip("/")
        return os.path.join(project_root, unit_dir, test_filename)
    else:
        return None

    return os.path.join(project_root, test_location, test_filename)


def build_legacy_context_note(
    rel_path: str,
    runner_cmd: str,
    test_rel_path: str,
) -> str:
    """Build the additionalContext note for a legacy file that has existing tests.

    Outputs a direct run-and-verify instruction.  No generate step.
    """
    return (
        f"tailtest: {rel_path} edited (existing file). "
        f"Do not generate new tests. "
        f"Run: `{runner_cmd} {test_rel_path}`"
    )


def detect_framework_context(
    rel_path: str,
    language: str,
    runners: dict,
) -> str:
    """Return a framework context hint for the additionalContext note, or empty string."""
    runner_info = runners.get(language)
    if not runner_info:
        return ""
    framework = runner_info.get("framework")
    style = runner_info.get("style")
    if framework == "laravel":
        if "/Http/" in rel_path or "/Controllers/" in rel_path:
            return "laravel/feature"
        return "laravel/unit"
    if style == "inline":
        return "rust/inline"
    if style == "colocated":
        return "go/colocated"
    return framework or ""


def build_context_note(
    rel_path: str,
    status: str,
    language: str,
    pending_count: int,
    runners: dict,
    project_root: Optional[str] = None,
) -> str:
    """Build the one-line additionalContext note for Claude (new-file path)."""
    runner_name: Optional[str] = None
    if language in runners:
        runner_name = runners[language].get("command")
    elif runners:
        runner_name = next(iter(runners.values())).get("command")

    framework_ctx = detect_framework_context(rel_path, language, runners)
    lang_info = f"{language}, {framework_ctx}" if framework_ctx else language
    parts = [f"tailtest: {rel_path} queued ({status}, {lang_info})"]

    # For single-file queues, include the exact target path so Claude doesn't
    # have to infer it from CLAUDE.md rules (same approach as legacy-file note).
    if pending_count == 1 and project_root:
        test_abs = get_test_file_path(rel_path, language, runners, project_root)
        if test_abs is None and language == "rust":
            # Rust: tests are inline in the source file
            parts.append(f"add #[cfg(test)] block to {rel_path}")
        elif test_abs:
            test_rel = _norm(os.path.relpath(test_abs, project_root))
            parts.append(f"write test to {test_rel}")

    if pending_count > 1:
        parts.append(f"{pending_count} files pending")
    if runner_name:
        parts.append(f"runner: {runner_name}")
    parts.append("Read .tailtest/session.json before responding to the user")
    return ". ".join(parts) + "."


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    try:
        raw = sys.stdin.read()
        event: dict = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        sys.exit(0)

    tool_name: str = event.get("tool_name", "")
    tool_input: dict = event.get("tool_input", {})
    project_root: str = event.get("cwd", os.getcwd())

    file_path = extract_file_path(tool_name, tool_input)
    if not file_path:
        sys.exit(0)

    if not os.path.isabs(file_path):
        file_path = os.path.join(project_root, file_path)

    ignore_patterns = load_ignore_patterns(project_root)

    if is_filtered(file_path, project_root, ignore_patterns):
        sys.exit(0)

    language = detect_language(file_path)
    if not language:
        sys.exit(0)

    session = load_session(project_root)

    # No manifest found at session start → no runner → stay completely silent.
    # (Standalone scripts with no package manager are not tailtest targets.)
    if not session.get("runners"):
        sys.exit(0)

    # RUNNER_REQUIRED_LANGUAGES must have an explicitly configured runner.
    # Unlike Python/TS/JS, these cannot be bootstrapped from a first-available fallback.
    if language in RUNNER_REQUIRED_LANGUAGES and language not in session.get("runners", {}):
        sys.exit(0)

    touched_files: list[str] = session.get("touched_files", [])
    rel_path = _norm(os.path.relpath(os.path.abspath(file_path), project_root))

    status = determine_status(file_path, project_root, touched_files)

    # Update touched_files regardless of status
    if rel_path not in touched_files:
        touched_files.append(rel_path)
        session["touched_files"] = touched_files

    runners: dict = session.get("runners", {})

    if status == "legacy-file":
        # Legacy files do NOT go into pending_files.
        # Emit a direct "run existing tests" instruction if a test file exists;
        # stay silent otherwise.
        try:
            save_session(project_root, session)
        except OSError:
            pass

        test_abs = get_test_file_path(rel_path, language, runners, project_root)
        if not test_abs or not os.path.exists(test_abs):
            sys.exit(0)

        runner_info = runners.get(language) or (next(iter(runners.values())) if runners else None)
        runner_cmd = runner_info.get("command", "pytest") if runner_info else "pytest"
        test_rel = _norm(os.path.relpath(test_abs, project_root))
        context = build_legacy_context_note(rel_path, runner_cmd, test_rel)
        print(json.dumps({"hookSpecificOutput": {"additionalContext": context}}))
        return

    # new-file: batch into pending_files for next-turn generation
    pending_files: list[dict] = session.get("pending_files", [])
    if rel_path not in [p["path"] for p in pending_files]:
        pending_files.append({
            "path": rel_path,
            "language": language,
            "status": status,
        })
        session["pending_files"] = pending_files

    try:
        save_session(project_root, session)
    except OSError:
        pass

    context = build_context_note(
        rel_path,
        status,
        language,
        len(pending_files),
        runners,
        project_root,
    )
    print(json.dumps({"hookSpecificOutput": {"additionalContext": context}}))


if __name__ == "__main__":
    main()
