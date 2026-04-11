"""AvaRunner -- ava test framework adapter (Phase 4.5.9).

Ava is a popular modern test framework for Node.js and TypeScript projects.
It supports native TypeScript without a compile step (via esbuild), runs
tests concurrently by default, and emits TAP output via ``--tap``.

Discovery signals (any one is sufficient):
  - ``ava`` in ``package.json`` devDependencies or dependencies
  - ``ava.config.{js,ts,mjs,cjs}`` at the project root

Discovery requires ``npx`` on PATH.

TIA: ava has no native related-tests feature. Uses stem-based heuristic.

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

_AVA_CONFIG_NAMES = (
    "ava.config.js",
    "ava.config.ts",
    "ava.config.mjs",
    "ava.config.cjs",
)

_JS_TEST_FILE_GLOBS = (
    "*.test.ts",
    "*.test.js",
    "*.test.mjs",
    "*.spec.ts",
    "*.spec.js",
    "*.spec.mjs",
)


@register_runner
class AvaRunner(BaseRunner):
    """Ava test runner adapter.

    Uses ``npx ava --tap`` to obtain TAP output, parsed by the shared
    TAP parser.
    """

    name: ClassVar[str] = "ava"
    language: ClassVar[str] = "ava"

    def discover(self) -> bool:
        if not self._has_ava():
            return False
        if shutil.which("npx") is None:
            raise RunnerNotAvailable("npx binary not on PATH")
        return True

    def _has_ava(self) -> bool:
        for name in _AVA_CONFIG_NAMES:
            if (self.project_root / name).exists():
                return True
        return self._pkg_has_dep("ava")

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

    # --- TIA ---

    async def impacted(
        self,
        files: list[Path],
        diff: str | None = None,
    ) -> list[TestID]:
        """Stem-based heuristic: return test files referencing changed source stems."""
        if not files:
            return []
        stems: set[str] = set()
        for f in files:
            fp = Path(f)
            if fp.suffix in (".ts", ".tsx", ".js", ".mjs", ".cjs"):
                stems.add(fp.stem)
        if not stems:
            return []
        result: list[TestID] = []
        for dirname in ("test", "tests", "src"):
            test_dir = self.project_root / dirname
            if not test_dir.is_dir():
                continue
            for pattern in _JS_TEST_FILE_GLOBS:
                for test_file in test_dir.rglob(pattern):
                    if "fixtures" in test_file.parts or "node_modules" in test_file.parts:
                        continue
                    try:
                        content = test_file.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    if any(stem in content for stem in stems):
                        result.append(str(test_file.relative_to(self.project_root)))
        return list(dict.fromkeys(result))

    # --- Execution ---

    async def run(
        self,
        test_ids: list[TestID],
        *,
        run_id: str,
        timeout_seconds: float = 60.0,
    ) -> FindingBatch:
        """Execute ava with ``--tap`` and parse the TAP output."""
        import time

        start_ms = time.monotonic() * 1000.0
        cmd = ["npx", "ava", "--tap"]
        if test_ids:
            cmd.extend(test_ids)

        try:
            result = await self.shell_run(cmd, timeout_seconds=timeout_seconds)
        except TimeoutError:
            duration_ms = time.monotonic() * 1000.0 - start_ms
            return FindingBatch(
                run_id=run_id,
                depth="standard",
                summary_line=f"tailtest: ava timed out at {timeout_seconds}s",
                duration_ms=duration_ms,
            )

        duration_ms = time.monotonic() * 1000.0 - start_ms
        output = result.stdout or result.stderr
        if not output.strip():
            return self._crash_batch(
                run_id=run_id, stderr=result.stderr, duration_ms=duration_ms
            )

        return self._parse_tap(output, run_id=run_id, duration_ms=duration_ms)

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
                        file=Path("<ava>"),
                        line=0,
                        message=_trim(entry.message or entry.name),
                        run_id=run_id,
                        rule_id=f"ava::{entry.name}",
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
        msg = (stderr or "ava produced no output").strip()
        return FindingBatch(
            run_id=run_id,
            depth="standard",
            findings=[
                Finding.create(
                    kind=FindingKind.TEST_FAILURE,
                    severity=Severity.CRITICAL,
                    file=Path("<ava>"),
                    line=0,
                    message=_trim(msg),
                    run_id=run_id,
                    rule_id="ava::crash",
                    claude_hint="ava produced no output; check stderr",
                )
            ],
            duration_ms=duration_ms,
            summary_line="tailtest: ava crashed",
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
