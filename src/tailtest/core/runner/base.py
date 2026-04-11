# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""BaseRunner interface — the shared abstraction every test runner implements.

Per Phase 1 Task 1.2. Every language-specific runner (Python, TS/JS, Rust,
and the Phase 2 custom runner) implements this interface. The engine
dispatches to runners by language via the `RunnerRegistry`.

Design notes:

- `discover()` inspects the project and returns whether this runner can
  handle it. Cheap check — read a manifest file, look for a test dir.
- `impacted(files, diff)` returns the list of test IDs affected by a set
  of changed files. Implementations delegate to native tools (pytest-testmon,
  jest --findRelatedTests, cargo metadata) where available, and fall back
  to "run all tests in related files" otherwise.
- `run(test_ids, timeout)` executes tests and returns a FindingBatch.
- `shell_run` is a shared helper that handles subprocess execution with
  timeout, stderr capture, and graceful failure. Runners should use it
  instead of calling `asyncio.create_subprocess_exec` directly.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, TypeVar

from tailtest.core.findings.schema import FindingBatch

logger = logging.getLogger(__name__)

# A test ID is an opaque string the runner understands — e.g. pytest uses
# "tests/test_foo.py::test_bar", vitest uses a similar path-style format.
TestID = str


class RunnerNotAvailable(Exception):
    """Raised by `discover()` when the runner's tools aren't present.

    For example, the Python runner raises this if `pytest` isn't on PATH
    or if there's no pytest configuration in the project.
    """


@dataclass(frozen=True)
class ShellResult:
    """The result of a subprocess shell invocation."""

    returncode: int
    stdout: str
    stderr: str
    duration_ms: float


class BaseRunner(ABC):
    """Abstract base class every runner implements.

    Subclasses MUST set class attributes `name` and `language`, and
    implement `discover`, `impacted`, and `run`. The `shell_run` helper
    is provided for subprocess execution.
    """

    # Set by subclasses.
    name: ClassVar[str] = ""
    language: ClassVar[str] = ""

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root)

    # --- Required abstract methods ---

    @abstractmethod
    def discover(self) -> bool:
        """Return True if this runner can handle the project at self.project_root.

        Should be cheap — read manifest files, check for a test directory,
        look for runner binaries on PATH. No test execution.

        Raise `RunnerNotAvailable` with a clear reason if the runner is
        definitely not usable (e.g. pytest not installed). Return False
        if the project simply doesn't use this runner (no tests dir, no
        manifest).
        """
        raise NotImplementedError

    @abstractmethod
    async def impacted(
        self,
        files: list[Path],
        diff: str | None = None,
    ) -> list[TestID]:
        """Return the list of test IDs affected by a change to `files`.

        Implementations should delegate to native TIA tools where available
        (pytest-testmon, jest --findRelatedTests) and fall back to "all
        tests in related files" otherwise. Must NOT execute tests.
        """
        raise NotImplementedError

    @abstractmethod
    async def run(
        self,
        test_ids: list[TestID],
        *,
        run_id: str,
        timeout_seconds: float = 30.0,
    ) -> FindingBatch:
        """Execute the given tests and return a structured FindingBatch.

        `test_ids` is the output of `impacted()` (or an empty list to run
        everything). `timeout_seconds` is the overall budget for the run.
        """
        raise NotImplementedError

    # --- Shared subprocess helper ---

    async def shell_run(
        self,
        command: list[str],
        *,
        timeout_seconds: float,
        cwd: Path | None = None,
    ) -> ShellResult:
        """Run a subprocess command with timeout and captured output.

        This helper is the blessed way for runners to invoke external
        tools. It handles: timeout via asyncio.wait_for, stderr capture,
        process cleanup on timeout, and returns a typed ShellResult.

        Never raises on the subprocess's non-zero exit — the caller
        inspects `returncode` to decide what happened. Does raise on
        timeout (with the process killed) and on failure to start the
        subprocess (e.g., binary not found).
        """
        cwd_path = cwd or self.project_root
        logger.debug("shell_run: %s (cwd=%s)", " ".join(command), cwd_path)

        start = asyncio.get_event_loop().time()
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd_path,
            )
        except FileNotFoundError as exc:
            raise RunnerNotAvailable(f"Binary not found: {command[0]}") from exc

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            # Kill the process tree so we don't leave zombies.
            try:
                process.kill()
                await process.wait()
            except ProcessLookupError:
                pass
            raise

        duration_ms = (asyncio.get_event_loop().time() - start) * 1000.0
        return ShellResult(
            returncode=process.returncode or 0,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            duration_ms=duration_ms,
        )


# --- Registry -------------------------------------------------------------


class RunnerRegistry:
    """A registry of runner classes keyed by language.

    The engine looks up runners via `get_for_language("python")` or
    iterates over `all_for_project(project_root)` to find every runner
    that successfully `discover()`s the project.
    """

    def __init__(self) -> None:
        self._runners: dict[str, type[BaseRunner]] = {}

    def register(self, runner_cls: type[BaseRunner]) -> None:
        if not runner_cls.language:
            raise ValueError(f"{runner_cls.__name__} has no `language` class attribute")
        self._runners[runner_cls.language] = runner_cls

    def get_for_language(self, language: str) -> type[BaseRunner] | None:
        return self._runners.get(language)

    def all_for_project(self, project_root: Path) -> list[BaseRunner]:
        """Instantiate every runner that successfully discovers the project.

        Returns a list — a single project can have multiple runners (e.g.
        a monorepo with a Python backend and a TypeScript frontend).
        """
        result: list[BaseRunner] = []
        for runner_cls in self._runners.values():
            runner = runner_cls(project_root)
            try:
                if runner.discover():
                    result.append(runner)
            except RunnerNotAvailable as exc:
                logger.debug("%s skipped: %s", runner_cls.__name__, exc)
        return result

    def registered_languages(self) -> list[str]:
        return sorted(self._runners.keys())


_DEFAULT_REGISTRY = RunnerRegistry()


def get_default_registry() -> RunnerRegistry:
    """Return the process-wide default registry.

    Runner modules register themselves into this registry at import time
    via the `register_runner` decorator.
    """
    return _DEFAULT_REGISTRY


_RunnerT = TypeVar("_RunnerT", bound=BaseRunner)


def register_runner(runner_cls: type[_RunnerT]) -> type[_RunnerT]:
    """Class decorator that registers a runner into the default registry.

    Generic in the runner type so the decorator preserves the concrete
    subclass: ``@register_runner`` on ``PythonRunner`` returns
    ``type[PythonRunner]``, not ``type[BaseRunner]``, so the decorated
    class's own methods remain type-visible.

    Usage::

        @register_runner
        class PythonRunner(BaseRunner):
            name = "pytest"
            language = "python"
            ...
    """
    _DEFAULT_REGISTRY.register(runner_cls)
    return runner_cls
