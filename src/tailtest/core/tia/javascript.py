"""JSTIA, delegator to JSRunner's native related-tests features (Phase 1 Task 1.3 JS half).

Thin wrapper around ``JSRunner.impacted()``. Exists for the same reason
``python.py`` exists: so callers that want a pure TIA provider (no
runner ownership) can ask for one without importing the runner class.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from tailtest.core.runner.base import TestID
from tailtest.core.runner.javascript import JSRunner
from tailtest.core.tia.base import TIAProvider


class JSTIA(TIAProvider):
    """TIA provider for JavaScript/TypeScript projects.

    Wraps a `JSRunner` and exposes only its `impacted()` method. Call
    sites that merely need to know "which tests are affected by these
    changed files" can depend on `TIAProvider` and avoid the runner's
    execution surface.
    """

    def __init__(self, project_root: Path) -> None:
        self._runner = JSRunner(project_root)
        # Populate the framework selection before the first impacted()
        # call by running discover() once. If discovery fails (no npx on
        # PATH, no package.json, etc.) the runner's impacted() path will
        # surface the error naturally on first call, so swallow here.
        with contextlib.suppress(Exception):
            self._runner.discover()

    async def impacted(
        self,
        changed_files: list[Path],
        diff: str | None = None,
    ) -> list[TestID]:
        return await self._runner.impacted(changed_files, diff=diff)
