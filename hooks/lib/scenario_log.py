"""H3 scenario log -- file-level test outcome tracking across sessions.

Appends one entry per tested file to `scenario_log` in session.json.
Enforces a 500-entry cap (drops oldest when over).
Foundational for H6 (recurring failure detection) and Phase 3 history.

Entry schema:
  {file, status, attempts, session_id, timestamp}
  status: "passed" | "fixed" | "unresolved" | "deferred"
"""

from __future__ import annotations

import datetime

_MAX_ENTRIES = 500


def build_scenario_entries(session: dict) -> list[dict]:
    """Build scenario log entries from session state.

    Called at session end. Returns one entry per tested file.
    """
    generated_tests: dict = session.get("generated_tests", {})
    fix_attempts: dict = session.get("fix_attempts", {})
    deferred_failures: list = session.get("deferred_failures", [])
    session_id: str = session.get("session_id", "")
    deferred_paths = {d["file"] for d in deferred_failures if isinstance(d, dict)}

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    entries = []

    for source_path in generated_tests:
        attempts = fix_attempts.get(source_path, 0)
        if source_path in deferred_paths:
            status = "deferred"
        elif attempts == 0:
            status = "passed"
        elif attempts >= 3:
            status = "unresolved"
        else:
            status = "fixed"

        entries.append({
            "file": source_path,
            "status": status,
            "attempts": attempts,
            "session_id": session_id,
            "timestamp": now,
        })

    return entries


def append_to_log(existing_log: list[dict], new_entries: list[dict]) -> list[dict]:
    """Append new entries to existing log, enforcing the 500-entry cap.

    Drops oldest entries first when over cap.
    """
    combined = existing_log + new_entries
    if len(combined) > _MAX_ENTRIES:
        combined = combined[-_MAX_ENTRIES:]
    return combined


def get_file_history(scenario_log: list[dict], file_path: str, last_n: int = 10) -> list[dict]:
    """Return the last_n entries for a specific file path."""
    matches = [e for e in scenario_log if e.get("file") == file_path]
    return matches[-last_n:]
