#!/usr/bin/env python3
"""tailtest PostToolUse hook -- the heartbeat.

Fires after every Write/Edit/MultiEdit.  Applies the intelligence filter.
Passes: appends the file to pending_files in .tailtest/session.json and
emits an additionalContext note.  Filtered: silent exit 0.

Target: < 1 second.  No LLM calls.  One optional git subprocess.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Optional

from lib.context import (
    build_context_note,
    build_legacy_context_note,
    detect_framework_context,
    extract_file_path,
    get_test_file_path,
)
from lib.filter import (
    FRAMEWORK_BOILERPLATE,
    GO_GENERATED_PREFIXES,
    GO_GENERATED_SUFFIXES,
    JS_GENERATED_SUFFIXES,
    LANGUAGE_MAP,
    RUNNER_REQUIRED_LANGUAGES,
    SKIP_EXTENSIONS,
    SKIP_PATH_FRAGMENTS,
    TEST_NAME_PATTERNS,
    BUILD_CONFIG_SUFFIXES,
    _norm,
    detect_language,
    is_filtered,
    is_test_file,
    load_ignore_patterns,
)
from lib.session import (
    determine_status,
    find_package_root,
    load_session,
    save_session,
)


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

    if session.get("paused", False):
        sys.exit(0)

    global_runners: dict = session.get("runners", {})
    packages: dict = session.get("packages", {})

    touched_files: list[str] = session.get("touched_files", [])
    rel_path = _norm(os.path.relpath(os.path.abspath(file_path), project_root))

    runners: dict = global_runners
    if packages:
        pkg_key = find_package_root(rel_path, packages)
        if pkg_key:
            runners = packages[pkg_key]

    if language in RUNNER_REQUIRED_LANGUAGES and language not in runners:
        sys.exit(0)

    status = determine_status(file_path, project_root, touched_files)

    if rel_path not in touched_files:
        touched_files.append(rel_path)
        session["touched_files"] = touched_files

    if status == "legacy-file":
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

    generated_tests: dict = session.get("generated_tests", {})
    existing_test_path: Optional[str] = None
    if rel_path in generated_tests:
        candidate = generated_tests[rel_path]
        if os.path.exists(os.path.join(project_root, candidate)):
            existing_test_path = candidate

    context = build_context_note(
        rel_path,
        status,
        language,
        len(pending_files),
        runners,
        project_root,
        existing_test_path,
    )
    print(json.dumps({"hookSpecificOutput": {"additionalContext": context}}))


if __name__ == "__main__":
    main()
