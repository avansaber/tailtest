"""Context building -- additionalContext payloads for all three hook events."""

from __future__ import annotations

import os
from typing import Optional

from lib.filter import RUNNER_REQUIRED_LANGUAGES, _norm
from lib.last_failures_formatter import format_last_failures
from lib.session import load_session
from lib.style import build_style_context


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

    session = load_session(project_root)
    last_failures = session.get("last_failures", [])
    failure_line = format_last_failures(last_failures)
    if failure_line:
        lines.append(failure_line)

    if ramp_up_count > 0:
        lines.append(
            f"tailtest: initial coverage scan -- first session detected. "
            f"{ramp_up_count} file(s) queued for coverage."
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
    """Return the absolute path of the expected test file for a source file."""
    runner_info = runners.get(language)
    if not runner_info and runners and language not in RUNNER_REQUIRED_LANGUAGES:
        runner_info = next(iter(runners.values()))
    if not runner_info:
        return None

    basename = os.path.splitext(os.path.basename(rel_path))[0]

    if language == "rust":
        return None

    if language == "go":
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
        if "typescript" in runners:
            test_filename = f"{basename}.test.ts"
        else:
            test_filename = f"{basename}.test.js"
    elif language == "ruby":
        if "spec" in test_location:
            test_filename = f"{basename}_spec.rb"
        else:
            test_filename = f"{basename}_test.rb"
    elif language == "java":
        test_filename = f"{basename}Test.java"
    elif language == "php":
        test_filename = f"{basename}Test.php"
        for subdir in ("tests/Unit", "tests/Feature", "tests"):
            candidate = os.path.join(project_root, subdir, test_filename)
            if os.path.exists(candidate):
                return candidate
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
    """Build the additionalContext note for a legacy file that has existing tests."""
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
    if not runner_info and language == "javascript" and "typescript" in runners:
        runner_info = runners["typescript"]
    elif not runner_info and language == "typescript" and "javascript" in runners:
        runner_info = runners["javascript"]
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
    existing_test_path: Optional[str] = None,
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

    if existing_test_path:
        parts.append(f"update existing test at {existing_test_path}")
    else:
        should_hint = (pending_count == 1) or (language == "php")
        if should_hint and project_root:
            test_abs = get_test_file_path(rel_path, language, runners, project_root)
            if test_abs is None and language == "rust":
                if pending_count == 1:
                    parts.append(f"add #[cfg(test)] block to {rel_path}")
            elif test_abs:
                test_rel = _norm(os.path.relpath(test_abs, project_root))
                parts.append(f"write test to {test_rel}")

        if framework_ctx == "nuxt":
            parts.append(
                "add `import { mountSuspended } from '@nuxt/test-utils'` to test imports"
                " and call `await mountSuspended(Component, { props: ... })`"
                " -- do NOT import or call mount from @vue/test-utils"
            )

        if framework_ctx == "laravel/feature" and project_root:
            env_testing = os.path.join(project_root, ".env.testing")
            if not os.path.exists(env_testing):
                parts.append(
                    "no .env.testing found -- add"
                    " '// tailtest: not run -- .env.testing required."
                    " Run manually after setup.' at top of test"
                )

    if pending_count > 1:
        parts.append(f"{pending_count} files pending -- write tests for all of them")
    if runner_name:
        parts.append(f"runner: {runner_name}")
    parts.append(
        "Read .tailtest/session.json, write test file(s) to disk, run them, "
        "report results -- then respond to the user"
    )
    return ". ".join(parts) + "."
