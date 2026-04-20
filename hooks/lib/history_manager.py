"""A1/A3/H8/H6 -- persistent cross-session test history.

Writes to `.tailtest/history.json` (project dir, all three platforms).
Cap: 1000 entries. Drops oldest when over.

Entry schema (same as scenario_log entries, with added `classification`):
  {file, status, attempts, session_id, timestamp, classification}
  classification: "gap" (first time tested) | "regression" (was passing, now failing) |
                  "fixed" | "passed" | "recurring" (failed 3+ times across sessions)
"""

from __future__ import annotations

import json
import os

_MAX_ENTRIES = 1000
_HISTORY_FILE = ".tailtest/history.json"
_RECURRENCE_THRESHOLD = 3  # failures across different sessions to flag as recurring


def _history_path(project_root: str) -> str:
    return os.path.join(project_root, _HISTORY_FILE)


def load_history(project_root: str) -> list[dict]:
    """Load history.json. Returns empty list if absent or corrupt."""
    path = _history_path(project_root)
    if not os.path.exists(path):
        return []
    try:
        with open(path) as fh:
            data = json.load(fh)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_history(project_root: str, history: list[dict]) -> None:
    """Write history.json, enforcing the 1000-entry cap."""
    if len(history) > _MAX_ENTRIES:
        history = history[-_MAX_ENTRIES:]
    path = _history_path(project_root)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(history, fh, indent=2)
            fh.write("\n")
    except OSError:
        pass


def classify_entry(entry: dict, history: list[dict]) -> str:
    """H8: Classify a new entry as gap, regression, fixed, or passed.

    gap        -- file has no prior history (never tested before this session)
    regression -- file was passing in the most recent prior session, now failing
    fixed      -- failed but was resolved within the session (attempts > 0, not deferred)
    passed     -- passed with no fix attempts
    """
    file_path = entry.get("file", "")
    status = entry.get("status", "passed")

    prior = [e for e in history if e.get("file") == file_path]

    if not prior:
        return "gap"

    if status == "passed":
        return "passed"

    if status in ("fixed",):
        return "fixed"

    if status in ("unresolved", "deferred"):
        last_prior = prior[-1]
        if last_prior.get("status") == "passed":
            return "regression"
        return status

    return status


def detect_recurring_failures(history: list[dict]) -> list[str]:
    """H6: Return files that have failed in 3+ different sessions.

    Only counts distinct session_ids to avoid counting retries within a session.
    """
    from collections import defaultdict
    failure_sessions: dict[str, set] = defaultdict(set)

    for entry in history:
        if entry.get("status") in ("unresolved", "deferred", "regression"):
            file_path = entry.get("file", "")
            session_id = entry.get("session_id", "")
            if file_path and session_id:
                failure_sessions[file_path].add(session_id)

    return [
        f for f, sessions in failure_sessions.items()
        if len(sessions) >= _RECURRENCE_THRESHOLD
    ]


def append_session_to_history(
    project_root: str,
    new_entries: list[dict],
) -> list[dict]:
    """A1: Append new session entries to history.json with classification.

    Returns the updated history list.
    """
    history = load_history(project_root)

    classified = []
    for entry in new_entries:
        e = dict(entry)
        e["classification"] = classify_entry(e, history)
        classified.append(e)

    history = history + classified
    if len(history) > _MAX_ENTRIES:
        history = history[-_MAX_ENTRIES:]

    save_history(project_root, history)
    return history


def get_recent_failures(history: list[dict], max_entries: int = 5) -> list[dict]:
    """A3: Return the most recent failure entries for startup context injection."""
    failures = [
        e for e in history
        if e.get("status") in ("unresolved", "deferred", "regression", "recurring")
        or e.get("classification") in ("regression", "recurring")
    ]
    return failures[-max_entries:]


def format_history_context(project_root: str) -> str:
    """A3/H6: Build a startup context line from history.

    Returns empty string if nothing notable to report.
    """
    history = load_history(project_root)
    if not history:
        return ""

    lines = []

    # H6: recurring failures
    recurring = detect_recurring_failures(history)
    if recurring:
        names = ", ".join(os.path.basename(f) for f in recurring[:3])
        overflow = len(recurring) - 3
        suffix = f" (+{overflow} more)" if overflow > 0 else ""
        lines.append(
            f"Recurring failures across sessions: {names}{suffix}. "
            f"These files have failed in multiple sessions -- consider adding validation."
        )

    # A3: recent regressions
    recent = get_recent_failures(history, max_entries=3)
    if recent:
        reg = [e for e in recent if e.get("classification") == "regression"]
        if reg:
            names = ", ".join(os.path.basename(e["file"]) for e in reg[:2])
            lines.append(f"Recent regressions: {names} (was passing, now failing).")

    return " ".join(lines)


def entry_count(project_root: str) -> int:
    """Return current number of entries in history.json."""
    return len(load_history(project_root))
