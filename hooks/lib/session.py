"""Session state -- load, save, status determination, orphaned report recovery."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Optional

from lib.filter import _norm


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
    """Return 'new-file' or 'legacy-file'."""
    rel_path = _norm(os.path.relpath(os.path.abspath(file_path), project_root))
    tracked = is_git_tracked(file_path, project_root)
    if tracked is None:
        return "legacy-file" if rel_path in touched_files else "new-file"
    return "legacy-file" if tracked else "new-file"


def find_package_root(
    rel_path: str,
    packages: dict,
) -> Optional[str]:
    """Return the relative path of the deepest package containing rel_path."""
    rel_path = _norm(rel_path)
    best: Optional[str] = None
    best_len = -1
    for pkg_rel in packages:
        pkg_prefix = _norm(pkg_rel).rstrip("/") + "/"
        if rel_path.startswith(pkg_prefix):
            if len(pkg_prefix) > best_len:
                best_len = len(pkg_prefix)
                best = pkg_rel
    return best


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
        "generated_tests": {},
        "packages": {},
    }


def save_session(project_root: str, session: dict) -> None:
    """Write session dict to .tailtest/session.json."""
    tailtest_dir = os.path.join(project_root, ".tailtest")
    os.makedirs(tailtest_dir, exist_ok=True)
    session_path = os.path.join(tailtest_dir, "session.json")
    with open(session_path, "w") as fh:
        json.dump(session, fh, indent=2)
        fh.write("\n")


def _write_orphaned_report(project_root: str) -> None:
    """Write report for previous session if SessionEnd never fired (crash/force-kill)."""
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
        return
    if not old.get("generated_tests"):
        return

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
