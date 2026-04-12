#!/usr/bin/env python3
# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0
"""Stop hook — Phase 0 pass-through stub.

Phase 0: reads stdin JSON (if any), exits 0, emits nothing.

Phase 4+ uses this hook for turn-end speculative test runs — kicking off
"related but not directly impacted" test sweeps while the user is reading
Claude's response. Not yet implemented.
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
