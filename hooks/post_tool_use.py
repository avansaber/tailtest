#!/usr/bin/env python3
"""PostToolUse hook entry point (thin shim, Phase 1 Task 1.5).

Claude Code's plugin runtime launches this script as a subprocess on
every Edit/Write/MultiEdit. The real logic lives in
``tailtest.hook.post_tool_use.run``, which is unit-tested independently.

This file is intentionally tiny. It:

1. Reads stdin as text.
2. Calls the async ``run()`` from the package.
3. Prints the returned JSON string to stdout (if any).
4. Exits 0.

SIGINT handling (audit gap #16) is installed here rather than in the
library code because the library runs in-process from the test suite
where we do not want to hijack signal handlers.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path


def _install_sigint_handler() -> None:
    """Handle Ctrl+C cleanly, exit 130 without a Python traceback.

    tailtest must not leave zombie pytest/vitest children. Asyncio's
    default behavior already cancels pending tasks on SIGINT via the
    event loop; this handler ensures the process exits with the
    conventional 130 code (128 + SIGINT) rather than the default 1.
    """

    def _on_sigint(signum: int, frame) -> None:  # noqa: ARG001
        sys.exit(130)

    try:
        signal.signal(signal.SIGINT, _on_sigint)
    except (OSError, ValueError):
        # Not running in the main thread, or on a platform that does
        # not allow installing handlers. Either way, fall back to
        # default behavior.
        pass


def main() -> int:
    _install_sigint_handler()

    try:
        stdin_text = sys.stdin.read()
    except Exception:  # noqa: BLE001
        stdin_text = ""

    # Import deferred so unit tests of the library do not pay the
    # import cost for every shim invocation and so a broken library
    # does not crash the shim at import time.
    from tailtest.hook.post_tool_use import run

    try:
        result = asyncio.run(run(stdin_text, project_root=Path.cwd()))
    except Exception:  # noqa: BLE001
        # The hot loop must never block Claude's next turn. On any
        # unhandled engine exception, log nothing (stdout must stay
        # clean), exit 0. The broken state will show up in the next
        # SessionStart or doctor run.
        return 0

    if result.stdout_json:
        print(result.stdout_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
