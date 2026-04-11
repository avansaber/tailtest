# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""tailtest.core.tia — test impact analysis.

Given a changed set of files, a TIA provider returns the list of test IDs
likely affected. Native tools (pytest-testmon, jest --findRelatedTests,
cargo metadata) are preferred; a heuristic fallback runs when they're
unavailable.

Phase 1 ships Python TIA (delegates to PythonRunner.impacted()) and the
heuristic fallback. Phase 1 also ships JS TIA via JSRunner. Phase 4.5 adds
Rust TIA via cargo metadata.
"""

from tailtest.core.tia.base import TIAProvider

__all__ = ["TIAProvider"]
