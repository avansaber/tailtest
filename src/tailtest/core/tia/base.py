"""TIAProvider abstract base (Phase 1 Task 1.3)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from tailtest.core.runner.base import TestID


class TIAProvider(ABC):
    """Abstract base class for test-impact-analysis providers.

    The engine's default TIA strategy is to use the runner's own
    `impacted()` method — which IS a TIA provider in disguise. This base
    class exists for cases where the engine wants to separate "what tests
    to run" from "how to run them" (e.g. an LLM-based TIA that doesn't
    own a runner).
    """

    @abstractmethod
    async def impacted(
        self,
        changed_files: list[Path],
        diff: str | None = None,
    ) -> list[TestID]:
        """Return the test IDs affected by the changed files."""
        raise NotImplementedError
