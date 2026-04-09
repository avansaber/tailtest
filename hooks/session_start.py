#!/usr/bin/env python3
"""SessionStart hook entry point (thin shim, Phase 1 Task 1.6).

Claude Code's plugin runtime launches this script once at the start
of every session with ``matcher: startup`` per
``hooks/hooks.json``. The real logic lives in
``tailtest.hook.session_start.run``, which is unit-tested
independently.

This file is intentionally tiny:
1. Install the SIGINT handler (audit gap #16, same as PostToolUse).
2. Read stdin as text.
3. Call the async ``run()`` from the package.
4. Print the returned JSON string to stdout (if any).
5. Exit 0.

Any unhandled library exception returns 0 silently. Session start
must never block or fail; a broken state surfaces in
``tailtest doctor`` or the next PostToolUse hook, not here.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path


def _install_sigint_handler() -> None:
    """Handle Ctrl+C cleanly, exit 130 without a Python traceback."""

    def _on_sigint(signum: int, frame) -> None:  # noqa: ARG001
        sys.exit(130)

    try:
        signal.signal(signal.SIGINT, _on_sigint)
    except (OSError, ValueError):
        pass


def main() -> int:
    _install_sigint_handler()

    try:
        stdin_text = sys.stdin.read()
    except Exception:  # noqa: BLE001
        stdin_text = ""

    from tailtest.hook.session_start import run

    try:
        result = asyncio.run(run(stdin_text, project_root=Path.cwd()))
    except Exception:  # noqa: BLE001
        return 0

    if result.stdout_json:
        print(result.stdout_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
