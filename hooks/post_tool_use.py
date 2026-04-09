#!/usr/bin/env python3
"""PostToolUse hook — Phase 0 pass-through stub.

Phase 0: reads stdin JSON (if any), exits 0, emits nothing. The hook is
wired into the plugin manifest so subsequent phases can replace this
stub without re-registering the hook.

Phase 1 replaces this with the real implementation: parse tool_name +
tool_input.file_path, invoke the local MCP server's run_tests +
impacted_tests tools, format findings as additionalContext, return in
hookSpecificOutput so Claude's next turn sees them.
"""

from __future__ import annotations

import sys


def main() -> int:
    # Read whatever stdin has — don't fail on missing or malformed input.
    try:
        sys.stdin.read()
    except Exception:
        pass
    # Phase 0: emit nothing. Phase 1 emits additionalContext via stdout.
    return 0


if __name__ == "__main__":
    sys.exit(main())
