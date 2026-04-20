"""Format compact last_failures summary from session state for cross-turn context injection."""

from __future__ import annotations

import os


def compute_last_failures(session: dict) -> list[dict]:
    """Derive compact failure records from session state.

    Returns a list of {file, status, attempts} for files that had test failures.
    Files that passed with zero fix attempts are excluded.
    """
    generated_tests: dict = session.get("generated_tests", {})
    fix_attempts: dict = session.get("fix_attempts", {})
    deferred_failures: list = session.get("deferred_failures", [])
    deferred_paths = {d["file"] for d in deferred_failures if isinstance(d, dict)}

    failures = []
    for source_path in generated_tests:
        attempts = fix_attempts.get(source_path, 0)
        if source_path in deferred_paths:
            failures.append({"file": source_path, "status": "unresolved", "attempts": attempts})
        elif attempts > 0:
            failures.append({"file": source_path, "status": "fixed", "attempts": attempts})
    return failures


def format_last_failures(last_failures: list[dict], max_entries: int = 5) -> str:
    """Format last_failures list into a compact human-readable context line.

    Returns empty string if there are no failures to report.
    """
    if not last_failures:
        return ""

    entries = last_failures[:max_entries]
    parts = []
    for entry in entries:
        name = os.path.basename(entry.get("file", "?"))
        status = entry.get("status", "unknown")
        attempts = entry.get("attempts", 0)
        if status == "unresolved":
            parts.append(f"{name} (unresolved after {attempts} attempt(s))")
        else:
            parts.append(f"{name} (fixed after {attempts} attempt(s))")

    overflow = len(last_failures) - len(entries)
    suffix = f" (+{overflow} more)" if overflow > 0 else ""
    return f"Previous turn failures: {', '.join(parts)}{suffix}."
