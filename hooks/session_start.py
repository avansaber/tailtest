#!/usr/bin/env python3
"""SessionStart hook — Phase 0 pass-through stub.

Phase 0: reads stdin JSON (if any), exits 0, emits nothing.

Phase 1 replaces this with the real implementation: run the shallow
project scan, bootstrap .tailtest/config.yaml if missing, handle the
empty-project case, warm the TIA cache, emit a single-line
initialization message.
"""

from __future__ import annotations

import sys


def main() -> int:
    try:
        sys.stdin.read()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
