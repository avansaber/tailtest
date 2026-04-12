"""Tests for the BaseRunner interface + RunnerRegistry (Phase 1 Task 1.2)."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from tailtest.core.findings.schema import Finding, FindingBatch, FindingKind, Severity
from tailtest.core.runner import (
    BaseRunner,
    RunnerNotAvailable,
    RunnerRegistry,
    register_runner,
)
from tailtest.core.runner import TestID as _TestID  # aliased so pytest doesn't try to collect it

# --- Fake runner subclasses for testing ---------------------------------


class FakePythonRunner(BaseRunner):
    name: ClassVar[str] = "fake-pytest"
    language: ClassVar[str] = "python-fake"

    discover_return: ClassVar[bool | type[Exception]] = True

    def discover(self) -> bool:
        if isinstance(self.discover_return, type):
            raise self.discover_return("fake")
        return self.discover_return

    async def impacted(self, files: list[Path], diff: str | None = None) -> list[_TestID]:
        return [f"test_{f.stem}" for f in files]

    async def run(
        self,
        test_ids: list[_TestID],
        *,
        run_id: str,
        timeout_seconds: float = 30.0,
    ) -> FindingBatch:
        findings = [
            Finding.create(
                kind=FindingKind.TEST_FAILURE,
                severity=Severity.HIGH,
                file="tests/test_example.py",
                line=10,
                message=f"fake failure for {tid}",
                run_id=run_id,
                rule_id="fake.assertion",
            )
            for tid in test_ids
        ]
        return FindingBatch(
            run_id=run_id,
            depth="standard",
            findings=findings,
            tests_passed=0,
            tests_failed=len(test_ids),
        )


class FakeJSRunner(BaseRunner):
    name: ClassVar[str] = "fake-vitest"
    language: ClassVar[str] = "javascript-fake"

    def discover(self) -> bool:
        return (self.project_root / "package.json").exists()

    async def impacted(self, files: list[Path], diff: str | None = None) -> list[_TestID]:
        return []

    async def run(
        self,
        test_ids: list[_TestID],
        *,
        run_id: str,
        timeout_seconds: float = 30.0,
    ) -> FindingBatch:
        return FindingBatch(run_id=run_id, depth="standard", tests_passed=1)


# --- Basic contract ------------------------------------------------------


def test_base_runner_is_abstract() -> None:
    """BaseRunner cannot be instantiated directly — it's abstract."""
    with pytest.raises(TypeError):
        BaseRunner(Path("."))  # type: ignore[abstract]


def test_subclass_must_set_language() -> None:
    """A runner without `language` cannot be registered."""

    class BadRunner(BaseRunner):
        name = "bad"
        language = ""  # empty

        def discover(self) -> bool:
            return True

        async def impacted(self, files: list[Path], diff: str | None = None) -> list[_TestID]:
            return []

        async def run(
            self,
            test_ids: list[_TestID],
            *,
            run_id: str,
            timeout_seconds: float = 30.0,
        ) -> FindingBatch:
            return FindingBatch(run_id=run_id, depth="quick")

    registry = RunnerRegistry()
    with pytest.raises(ValueError, match="no `language`"):
        registry.register(BadRunner)


# --- Registry behavior ---------------------------------------------------


def test_registry_register_and_lookup() -> None:
    registry = RunnerRegistry()
    registry.register(FakePythonRunner)

    cls = registry.get_for_language("python-fake")
    assert cls is FakePythonRunner

    assert registry.get_for_language("rust") is None
    assert "python-fake" in registry.registered_languages()


def test_registry_all_for_project(tmp_path: Path) -> None:
    """all_for_project returns runners that successfully discover()."""
    registry = RunnerRegistry()
    registry.register(FakePythonRunner)
    registry.register(FakeJSRunner)

    # No package.json → only Python runner discovers
    runners = registry.all_for_project(tmp_path)
    assert len(runners) == 1
    assert isinstance(runners[0], FakePythonRunner)

    # Add package.json → both discover
    (tmp_path / "package.json").write_text("{}")
    runners = registry.all_for_project(tmp_path)
    assert len(runners) == 2
    langs = {r.language for r in runners}
    assert langs == {"python-fake", "javascript-fake"}


def test_registry_skips_runner_that_raises_not_available(tmp_path: Path) -> None:
    """If a runner's discover() raises RunnerNotAvailable, it's skipped silently."""
    try:
        FakePythonRunner.discover_return = RunnerNotAvailable
        registry = RunnerRegistry()
        registry.register(FakePythonRunner)
        runners = registry.all_for_project(tmp_path)
        assert runners == []
    finally:
        FakePythonRunner.discover_return = True


def test_registry_unavailable_reasons(tmp_path: Path) -> None:
    """unavailable_reasons() collects RunnerNotAvailable messages without hiding them.

    Regression test for Ubuntu smoke test Finding 3: when a runner binary is
    missing the registry swallowed RunnerNotAvailable silently, causing run.py
    to print 'no runners detected' with no hint about what was actually wrong.
    """
    class ConfiguredButMissingBinary(BaseRunner):
        name: ClassVar[str] = "fake-missing"
        language: ClassVar[str] = "python-missing-fake"

        def discover(self) -> bool:
            # Simulates a runner that sees pytest.ini but can't find the binary.
            raise RunnerNotAvailable("pytest not found in project venv or on PATH")

        async def impacted(self, files: list[Path], diff: str | None = None) -> list[_TestID]:
            return []

        async def run(
            self,
            test_ids: list[_TestID],
            *,
            run_id: str,
            timeout_seconds: float = 30.0,
        ) -> FindingBatch:
            return FindingBatch(run_id=run_id, depth="quick")

    registry = RunnerRegistry()
    registry.register(ConfiguredButMissingBinary)

    # all_for_project returns empty (runner is unavailable)
    assert registry.all_for_project(tmp_path) == []

    # but unavailable_reasons reports WHY
    reasons = registry.unavailable_reasons(tmp_path)
    assert "fake-missing" in reasons
    assert "pytest not found" in reasons["fake-missing"]


def test_register_runner_decorator_uses_default_registry() -> None:
    """The @register_runner decorator adds to the process-wide registry."""

    @register_runner
    class DecoratedRunner(BaseRunner):
        name: ClassVar[str] = "decorated"
        language: ClassVar[str] = "python-decorated-test"

        def discover(self) -> bool:
            return False

        async def impacted(self, files: list[Path], diff: str | None = None) -> list[_TestID]:
            return []

        async def run(
            self,
            test_ids: list[_TestID],
            *,
            run_id: str,
            timeout_seconds: float = 30.0,
        ) -> FindingBatch:
            return FindingBatch(run_id=run_id, depth="quick")

    from tailtest.core.runner import get_default_registry

    registry = get_default_registry()
    assert registry.get_for_language("python-decorated-test") is DecoratedRunner


# --- shell_run helper ----------------------------------------------------


@pytest.mark.asyncio
async def test_shell_run_returns_stdout(tmp_path: Path) -> None:
    """shell_run captures stdout and returncode."""
    runner = FakePythonRunner(tmp_path)
    result = await runner.shell_run(
        ["python3", "-c", "print('hello'); print('world')"],
        timeout_seconds=10.0,
    )
    assert result.returncode == 0
    assert "hello" in result.stdout
    assert "world" in result.stdout
    assert result.duration_ms > 0


@pytest.mark.asyncio
async def test_shell_run_captures_stderr(tmp_path: Path) -> None:
    """shell_run captures stderr separately."""
    runner = FakePythonRunner(tmp_path)
    result = await runner.shell_run(
        ["python3", "-c", "import sys; sys.stderr.write('err!'); sys.exit(2)"],
        timeout_seconds=10.0,
    )
    assert result.returncode == 2
    assert "err!" in result.stderr


@pytest.mark.asyncio
async def test_shell_run_raises_not_available_on_missing_binary(tmp_path: Path) -> None:
    """Missing binary → RunnerNotAvailable, not a raw FileNotFoundError."""
    runner = FakePythonRunner(tmp_path)
    with pytest.raises(RunnerNotAvailable, match="Binary not found"):
        await runner.shell_run(
            ["nonexistent-binary-xyz123"],
            timeout_seconds=1.0,
        )


@pytest.mark.asyncio
async def test_shell_run_kills_process_on_timeout(tmp_path: Path) -> None:
    """Timeout kills the process cleanly — no zombies."""
    runner = FakePythonRunner(tmp_path)
    with pytest.raises(TimeoutError):
        await runner.shell_run(
            ["python3", "-c", "import time; time.sleep(30)"],
            timeout_seconds=0.3,
        )
