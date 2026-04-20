#!/usr/bin/env python3
"""tailtest SessionEnd hook -- write markdown session report.

Fires when the Claude Code session ends. Reads .tailtest/session.json
and writes a markdown report to the path stored in session["report_path"].
If no files were tested, writes nothing.
"""

from __future__ import annotations

import json
import os
import sys

from lib.complexity_scorer import score_file
from lib.last_failures_formatter import compute_last_failures
from lib.scenario_log import append_to_log, build_scenario_entries
from lib.session import load_session, save_session


def _file_status(source_path: str, fix_attempts: dict, deferred_failures: list) -> str:
    deferred_paths = {d["file"] for d in deferred_failures if isinstance(d, dict)}
    attempts = fix_attempts.get(source_path, 0)
    if source_path in deferred_paths:
        return "deferred"
    if attempts == 0:
        return "passed"
    if attempts >= 3:
        return "unresolved"
    return f"fixed ({attempts} attempt(s))"


def build_report(session: dict) -> str:
    """Build markdown report content from session data. Returns empty string if nothing to report."""
    generated_tests: dict = session.get("generated_tests", {})
    if not generated_tests:
        return ""

    runners: dict = session.get("runners", {})
    depth: str = session.get("depth", "standard")
    started_at: str = session.get("started_at", "")
    fix_attempts: dict = session.get("fix_attempts", {})
    deferred_failures: list = session.get("deferred_failures", [])

    lines = [f"# tailtest session -- {started_at}", ""]

    runner_parts = [f"{lang}/{info.get('command', '?')}" for lang, info in runners.items()]
    runner_str = ", ".join(runner_parts) if runner_parts else "no runner"
    lines.append(f"Runner: {runner_str}  |  Depth: {depth}")
    lines.append("")

    lines.append("## Files tested")
    lines.append("")
    lines.append("| File | Test file | Result |")
    lines.append("|---|---|---|")

    counts = {"passed": 0, "fixed": 0, "deferred": 0, "unresolved": 0}
    for source_path, test_path in sorted(generated_tests.items()):
        status = _file_status(source_path, fix_attempts, deferred_failures)
        lines.append(f"| {source_path} | {test_path} | {status} |")
        if status == "passed":
            counts["passed"] += 1
        elif status == "deferred":
            counts["deferred"] += 1
        elif status == "unresolved":
            counts["unresolved"] += 1
        else:
            counts["fixed"] += 1

    total = len(generated_tests)
    lines.append("")
    lines.append("## Summary")
    parts = [f"{total} file(s) tested"]
    if counts["passed"]:
        parts.append(f"{counts['passed']} passed")
    if counts["fixed"]:
        parts.append(f"{counts['fixed']} fixed")
    if counts["deferred"]:
        parts.append(f"{counts['deferred']} deferred")
    if counts["unresolved"]:
        parts.append(f"{counts['unresolved']} unresolved")
    lines.append("  |  ".join(parts))

    return "\n".join(lines) + "\n"


def write_report(project_root: str, session: dict) -> bool:
    """Write report to session's report_path. Returns True if written."""
    report_path = session.get("report_path")
    if not report_path:
        return False
    abs_path = os.path.join(project_root, report_path)
    if os.path.exists(abs_path):
        return False
    content = build_report(session)
    if not content:
        return False
    try:
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w") as fh:
            fh.write(content)
        return True
    except OSError:
        return False


def main() -> None:
    try:
        raw = sys.stdin.read()
        event: dict = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        event = {}
    project_root: str = event.get("cwd", os.getcwd())
    session = load_session(project_root)

    changed = False

    last_failures = compute_last_failures(session)
    if last_failures != session.get("last_failures"):
        session["last_failures"] = last_failures
        changed = True

    # H3: append file-level outcomes to scenario_log
    new_entries = build_scenario_entries(session)
    if new_entries:
        existing_log = session.get("scenario_log", [])
        session["scenario_log"] = append_to_log(existing_log, new_entries)
        changed = True

    # H1: store complexity scores for tested files (for future session reference)
    generated_tests: dict = session.get("generated_tests", {})
    if generated_tests:
        scores = session.get("complexity_scores", {})
        for source_path in generated_tests:
            abs_path = os.path.join(project_root, source_path)
            try:
                score, _ = score_file(abs_path)
                scores[source_path] = score
            except Exception:
                pass
        session["complexity_scores"] = scores
        changed = True

    if changed:
        try:
            save_session(project_root, session)
        except OSError:
            pass

    write_report(project_root, session)


if __name__ == "__main__":
    main()
