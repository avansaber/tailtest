"""tailtest.core.baseline — per-project baseline file management.

Phase 1 Task 1.7. The baseline file at `.tailtest/baseline.yaml` holds the
set of findings that existed when tailtest was first enabled on a project.
Subsequent runs filter against it so only *new* findings surface.

Two important policies from the audit:

1. **Lazy baseline generation** — the baseline is NOT generated on
   SessionStart; it's generated on the first successful green run. This
   avoids capturing "dependency missing" errors as permanent baseline
   entries when the user hasn't run `npm install` / `pip install` yet.

2. **Kind-aware baseline policy** — security findings (secrets, SAST, SCA)
   baseline immediately on first detection because they're content-level
   and stable. Test failures only baseline after 3+ consecutive failed
   runs, which distinguishes broken tests from flaky tests.
"""

from tailtest.core.baseline.manager import (
    BaselineEntry,
    BaselineFile,
    BaselineManager,
)

__all__ = ["BaselineEntry", "BaselineFile", "BaselineManager"]
