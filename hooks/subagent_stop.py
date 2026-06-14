#!/usr/bin/env python3
"""tailtest SubagentStop hook -- drain pending tests after a subagent finishes.

Fires when a subagent completes its work.  If files were queued in
pending_files during the subagent's edits (via PostToolUse), surfaces a
drain note so the parent turn processes them before responding to the user.

Deduplication against touched_files is handled upstream by PostToolUse;
this hook only needs to surface whatever pending_files already contains.

Target: < 1 second.  No LLM calls.  No file writes.
"""

from __future__ import annotations

import json
import os
import sys

from lib.session import load_session


def main() -> None:
    try:
        raw = sys.stdin.read()
        event: dict = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        event = {}

    project_root: str = event.get("cwd", os.getcwd())

    session = load_session(project_root)

    if session.get("paused", False):
        sys.exit(0)

    pending_files: list[dict] = session.get("pending_files", [])
    if not pending_files:
        sys.exit(0)

    paths = [p["path"] for p in pending_files]
    count = len(paths)
    path_list = ", ".join(paths)

    note = (
        f"tailtest: subagent finished -- {count} file(s) pending ({path_list}). "
        "Read .tailtest/session.json, write test file(s) to disk, run them, "
        "report results -- then respond to the user."
    )
    print(json.dumps({"hookSpecificOutput": {"additionalContext": note}}))


if __name__ == "__main__":
    main()
