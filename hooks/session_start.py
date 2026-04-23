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

import json
import os
import sys

from lib.context import (
    build_bootstrap_note,
    build_compact_context,
    build_startup_context,
    read_claude_md,
)
from lib.filter import load_ignore_patterns
from lib.ramp_up import (
    RAMP_UP_SENTINEL,
    RAMP_UP_EXT_MAP,
    RAMP_UP_SKIP_DIRS,
    _git_commit_counts,
    _has_existing_test,
    _is_ramp_up_filtered,
    _score_candidate,
    is_first_session,
    ramp_up_scan,
    read_ramp_up_limit,
)
from lib.runners import (
    _find_test_location,
    _iter_top_dirs,
    _read_json,
    _read_toml_text,
    create_session,
    detect_deno_runner,
    detect_go_runner,
    detect_java_runner,
    detect_monorepo,
    detect_node_runner,
    detect_php_runner,
    detect_project_type,
    detect_python_runner,
    detect_ruby_runner,
    detect_rust_runner,
    make_session_id,
    read_depth,
    scan_packages,
    scan_runners,
)
from lib.api_validator import (
    build_api_validation_note,
    extract_public_names,
    is_api_validation_enabled,
    validate_file_importable,
)
from lib.complexity_scorer import complexity_context_note, score_file, score_to_depth
from lib.impact_tracer import (
    find_importers,
    format_impact_note,
    is_impact_tracing_enabled,
)
from lib.history_manager import (
    append_session_to_history,
    classify_entry,
    detect_recurring_failures,
    entry_count,
    format_history_context,
    get_recent_failures,
    load_history,
    save_history,
)
from lib.last_failures_formatter import compute_last_failures, format_last_failures
from lib.output_compressor import compress_output
from lib.scenario_log import append_to_log, build_scenario_entries, get_file_history
from lib.session import _write_orphaned_report
from lib.style import (
    TEST_FILE_PATTERNS,
    build_style_context,
    detect_custom_helpers,
    extract_style_snippet,
    find_recent_test_files,
)


def main() -> None:
    try:
        raw = sys.stdin.read()
        event: dict = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        event = {}

    source: str = event.get("source", "startup")
    project_root: str = event.get("cwd", os.getcwd())

    plugin_root = os.environ.get(
        "CLAUDE_PLUGIN_ROOT",
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )

    claude_md = read_claude_md(plugin_root)

    if source == "compact":
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
        _write_orphaned_report(project_root)
        runners = scan_runners(project_root)
        depth = read_depth(project_root)
        first_session = is_first_session(project_root)
        session = {}
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
                ramp_up_count = 0

        context = build_startup_context(
            project_root, runners, depth, claude_md,
            ramp_up_count=ramp_up_count,
        )

    if context:
        print(context)


if __name__ == "__main__":
    main()
