"""TapeRunner -- tape test framework adapter (Phase 4.5.9).

Tape is a minimal TAP-producing test library for Node.js. Unlike mocha
or jest, tape doesn't have a test runner CLI -- test files are executed
directly with ``node`` and emit TAP to stdout.

Discovery signals:
  - ``tape`` in ``package.json`` devDependencies or dependencies

Discovery requires ``node`` on PATH.

TIA: tape has no native related-tests feature. Uses stem-based heuristic.

The runner is registered into the default RunnerRegistry on import.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path
from typing import ClassVar

from tailtest.core.findings.schema import (
    Finding,
    FindingBatch,
    FindingKind,
    Severity,
)
from tailtest.core.runner._tap import parse_tap
from tailtest.core.runner.base import (
    BaseRunner,
    RunnerNotAvailable,
    TestID,
    register_runner,
)

logger = logging.getLogger(__name__)

# Tape test file patterns (tape tests are plain .js/.ts files, typically
# in test/ or tests/ with names like test-*.js or *.test.js).
_TAPE_TEST_GLOBS = (
    "test-*.js",
    "test-*.ts",
    "test-*.mjs",
    "*.test.js",
    "*.test.ts",
    "*.test.mjs",
)


@register_runner
class TapeRunner(BaseRunner):
    """Tape test runner adapter.

    Discovers tape test files, runs each via ``node <file>`` (or
    ``npx tsx <file>`` for TypeScript), and parses the TAP output.
    """

    name: ClassVar[str] = "tape"
    language: ClassVar[str] = "tape"

    def discover(self) -> bool:
        if shutil.which("node") is None:
            return False
        return self._pkg_has_dep("tape")

    def _pkg_has_dep(self, dep: str) -> bool:
        pkg_path = self.project_root / "package.json"
        if not pkg_path.exists():
            return False
        try:
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        for section in ("devDependencies", "dependencies"):
            if isinstance(pkg.get(section), dict) and dep in pkg[section]:
                return True
        return False

    def _find_test_files(self) -> list[Path]:
        """Return all tape test files under test/ or tests/."""
        found: list[Path] = []
        for dirname in ("test", "tests"):
            test_dir = self.project_root / dirname
            if not test_dir.is_dir():
                continue
            for pattern in _TAPE_TEST_GLOBS:
                for f in test_dir.rglob(pattern):
                    if "node_modules" not in f.parts:
                        found.append(f)
        return sorted(set(found))

    # --- TIA ---

    async def impacted(
        self,
        files: list[Path],
        diff: str | None = None,
    ) -> list[TestID]:
        """Stem-based heuristic."""
        if not files:
            return []
        stems: set[str] = set()
        for f in files:
            fp = Path(f)
            if fp.suffix in (".ts", ".js", ".mjs", ".cjs"):
                stems.add(fp.stem)
        if not stems:
            return []
        result: list[TestID] = []
        for test_file in self._find_test_files():
            try:
                content = test_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if any(stem in content for stem in stems):
                result.append(str(test_file.relative_to(self.project_root)))
        return result

    # --- Execution ---

    async def run(
        self,
        test_ids: list[TestID],
        *,
        run_id: str,
        timeout_seconds: float = 60.0,
    ) -> FindingBatch:
        """Run tape test files and parse TAP output.

        If ``test_ids`` is empty, discovers all tape test files. Each file
        is run in a separate ``node`` invocation; output is concatenated
        and parsed as a single TAP stream.
        """
        import time

        start_ms = time.monotonic() * 1000.0
        files_to_run = [Path(t) for t in test_ids] if test_ids else self._find_test_files()

        if not files_to_run:
            duration_ms = time.monotonic() * 1000.0 - start_ms
            return FindingBatch(
                run_id=run_id,
                depth="standard",
                summary_line="tailtest: 0/0 tests passed (no tape test files found)",
                duration_ms=duration_ms,
            )

        # Use tsx for TypeScript files if available.
        tsx_available = shutil.which("tsx") is not None or self._pkg_has_dep("tsx")

        all_tap_lines: list[str] = []
        for test_file in files_to_run:
            file_path = (
                test_file
                if test_file.is_absolute()
                else self.project_root / test_file
            )
            suffix = file_path.suffix
            if suffix in (".ts", ".mts") and tsx_available:
                cmd = ["npx", "tsx", str(file_path)]
            else:
                cmd = ["node", str(file_path)]

            try:
                result = await self.shell_run(cmd, timeout_seconds=timeout_seconds)
                all_tap_lines.extend(result.stdout.splitlines())
            except (TimeoutError, RunnerNotAvailable) as exc:
                logger.warning("TapeRunner: failed to run %s: %s", file_path, exc)
                all_tap_lines.append(f"not ok 1 - {file_path.name} (runner error: {exc})")

        duration_ms = time.monotonic() * 1000.0 - start_ms
        combined = "\n".join(all_tap_lines)

        if not combined.strip():
            return self._crash_batch(run_id=run_id, stderr="", duration_ms=duration_ms)

        return self._parse_tap(combined, run_id=run_id, duration_ms=duration_ms)

    def _parse_tap(
        self, output: str, *, run_id: str, duration_ms: float
    ) -> FindingBatch:
        entries = parse_tap(output)
        passed = sum(1 for e in entries if e.passed and not e.skipped)
        skipped = sum(1 for e in entries if e.skipped)
        failed = sum(1 for e in entries if not e.passed)

        findings: list[Finding] = []
        for entry in entries:
            if not entry.passed:
                findings.append(
                    Finding.create(
                        kind=FindingKind.TEST_FAILURE,
                        severity=Severity.HIGH,
                        file=Path("<tape>"),
                        line=0,
                        message=_trim(entry.message or entry.name),
                        run_id=run_id,
                        rule_id=f"tape::{entry.name}",
                        claude_hint=_first_line(entry.message or entry.name),
                    )
                )

        total = passed + failed + skipped
        summary = f"tailtest: {passed}/{total} tests passed"
        if failed:
            summary += f" · {failed} failed"
        if skipped:
            summary += f" · {skipped} skipped"
        return FindingBatch(
            run_id=run_id,
            depth="standard",
            findings=findings,
            duration_ms=duration_ms,
            summary_line=summary,
            tests_passed=passed,
            tests_failed=failed,
            tests_skipped=skipped,
        )

    def _crash_batch(
        self, *, run_id: str, stderr: str, duration_ms: float
    ) -> FindingBatch:
        msg = (stderr or "tape produced no output").strip()
        return FindingBatch(
            run_id=run_id,
            depth="standard",
            findings=[
                Finding.create(
                    kind=FindingKind.TEST_FAILURE,
                    severity=Severity.CRITICAL,
                    file=Path("<tape>"),
                    line=0,
                    message=_trim(msg),
                    run_id=run_id,
                    rule_id="tape::crash",
                    claude_hint="tape produced no output; check stderr",
                )
            ],
            duration_ms=duration_ms,
            summary_line="tailtest: tape crashed",
            tests_failed=1,
        )


def _trim(text: str) -> str:
    s = re.sub(r"\s+", " ", text.strip())
    return s[:200] if len(s) > 200 else s


def _first_line(text: str) -> str | None:
    for raw in text.splitlines():
        line = raw.strip()
        if line:
            return line[:200]
    return None
