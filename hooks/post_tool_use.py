#!/usr/bin/env python3
# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0
"""PostToolUse hook entry point (thin shim, Phase 1 Task 1.5).

Claude Code's plugin runtime launches this script as a subprocess on
every Edit/Write/MultiEdit. The real logic lives in
``tailtest.hook.post_tool_use.run``, which is unit-tested independently.

This file is intentionally tiny. It:

1. Bootstraps the python interpreter via ``_bootstrap`` so that
   ``import tailtest.hook`` is guaranteed to succeed (Phase 7
   Task 7.4a — fixes the case where the system ``python3`` on
   PATH is not the python that has tailtest installed).
2. Reads stdin as text.
3. Calls the async ``run()`` from the package.
4. Prints the returned JSON string to stdout (if any).
5. Exits 0.

SIGINT handling (audit gap #16) is installed here rather than in the
library code because the library runs in-process from the test suite
where we do not want to hijack signal handlers.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

# Phase 7 Task 7.4a: ensure the script's directory is on sys.path
# so we can import the sibling _bootstrap.py module. Python normally
# adds the script directory to sys.path[0] automatically, but some
# launchers (and re-execs that pass an absolute script path) skip
# that step. Make it explicit so the import below cannot fail for
# the wrong reason.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


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

    # Phase 7 Task 7.4a: bootstrap the python interpreter so the
    # `import tailtest.hook` below cannot fail because Claude Code
    # invoked this shim with the wrong python3. On unrecoverable
    # failure, bootstrap_or_die writes a clear stderr message and
    # raises SystemExit(0) so the hot loop doesn't block.
    from _bootstrap import bootstrap_or_die

    bootstrap_or_die(__file__)

    try:
        stdin_text = sys.stdin.read()
    except Exception:  # noqa: BLE001
        stdin_text = ""

    try:
        # Import deferred so unit tests of the library do not pay the
        # import cost for every shim invocation and so a broken library
        # does not crash the shim at import time.
        # Import is INSIDE the try/except so any ImportError (e.g. stale
        # plugin cache missing _register_all_runners) exits 0 rather than
        # producing a traceback and a non-zero exit that Claude Code displays
        # as a "hook error". The broken state surfaces in tailtest doctor.
        from tailtest.hook.post_tool_use import run

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
