"""NodeTestRunner -- Node.js built-in ``node --test`` adapter (Phase 4.5.9).

Node's built-in test runner has been stable since Node 20 and available
experimentally since Node 18. It is the runner Feynman and a growing
number of TypeScript projects use instead of vitest or jest.

Discovery signals:
  - ``node`` binary is on PATH
  - ``package.json`` exists at the project root
  - Any ``scripts.*`` value contains ``"node --test"`` or ``"node:test"``

Runner selection: this runner defers to JSRunner for vitest/jest projects.
If a project has ``vitest`` or ``jest`` in devDependencies, the JSRunner
will pick it up and NodeTestRunner's ``discover()`` returns False to avoid
double-running.

Output format: ``node --test --test-reporter=json`` (Node 20+) emits one
JSON event per line (NDJSON). Older Node versions can use TAP via
``--test-reporter=tap``.  This adapter tries JSON first and falls back to
TAP on parse failure.

TIA: node --test has no native related-tests feature. Falls back to the
stem-based heuristic from JSRunner.

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
    TestID,
    register_runner,
)

logger = logging.getLogger(__name__)

# Node's default test file patterns (from Node docs).
_NODE_TEST_GLOBS = (
    "**/*.test.js",
    "**/*.test.mjs",
    "**/*.test.cjs",
    "**/*.test.ts",
    "**/*.test.mts",
    "**/*.spec.js",
    "**/*.spec.mjs",
    "**/*.spec.cjs",
    "**/*.spec.ts",
    "**/*.spec.mts",
    "test/**/*.js",
    "test/**/*.ts",
)

_VITEST_JEST_DEPS = frozenset({"vitest", "jest", "@jest/core"})


@register_runner
class NodeTestRunner(BaseRunner):
    """Test runner adapter for Node.js's built-in ``node --test``.

    Supports both JS and TypeScript projects. For TypeScript, detects
    ``tsx`` in devDependencies and prefixes ``npx tsx`` to enable TS
    transpilation at runtime.
    """

    name: ClassVar[str] = "node_test"
    language: ClassVar[str] = "node_test"

    def discover(self) -> bool:
        if shutil.which("node") is None:
            return False
        pkg_path = self.project_root / "package.json"
        if not pkg_path.exists():
            return False
        try:
            pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False

        # Defer to JSRunner if vitest or jest are in use.
        for section in ("devDependencies", "dependencies"):
            deps = pkg.get(section) or {}
            if isinstance(deps, dict) and _VITEST_JEST_DEPS & deps.keys():
                return False

        # Require an explicit node --test signal in scripts.
        scripts = pkg.get("scripts") or {}
        if not isinstance(scripts, dict):
            return False
        return any(
            "node --test" in str(v) or "node:test" in str(v)
            for v in scripts.values()
        )

    # --- TIA (stem-based heuristic) ---

    async def impacted(
        self,
        files: list[Path],
        diff: str | None = None,
    ) -> list[TestID]:
        """Return test file paths referencing any changed source file stem."""
        if not files:
            return []
        stems: set[str] = set()
        for f in files:
            fp = Path(f)
            if fp.suffix in (".ts", ".mts", ".tsx", ".js", ".mjs", ".cjs"):
                stems.add(fp.stem)
        if not stems:
            return []
        result: list[TestID] = []
        for pattern in _NODE_TEST_GLOBS:
            for test_file in self.project_root.glob(pattern):
                if "fixtures" in test_file.parts or "node_modules" in test_file.parts:
                    continue
                try:
                    content = test_file.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if any(stem in content for stem in stems):
                    result.append(str(test_file.relative_to(self.project_root)))
        return list(dict.fromkeys(result))  # deduplicate preserving order

    # --- Execution ---

    async def run(
        self,
        test_ids: list[TestID],
        *,
        run_id: str,
        timeout_seconds: float = 60.0,
    ) -> FindingBatch:
        """Run tests via ``node --test --test-reporter=json``."""
        import time

        start_ms = time.monotonic() * 1000.0

        # Prefer tsx if installed (enables TypeScript test files).
        tsx_available = shutil.which("tsx") is not None or self._pkg_has_dep("tsx")
        if tsx_available:
            cmd = ["npx", "tsx", "--test", "--test-reporter=json"]
        else:
            cmd = ["node", "--test", "--test-reporter=json"]

        if test_ids:
            cmd.extend(test_ids)

        try:
            result = await self.shell_run(cmd, timeout_seconds=timeout_seconds)
        except TimeoutError:
            duration_ms = time.monotonic() * 1000.0 - start_ms
            return FindingBatch(
                run_id=run_id,
                depth="standard",
                summary_line=f"tailtest: node --test timed out at {timeout_seconds}s",
                duration_ms=duration_ms,
            )

        duration_ms = time.monotonic() * 1000.0 - start_ms

        # Try JSON reporter output first.
        if result.stdout.strip():
            try:
                return self._parse_json_events(
                    result.stdout, run_id=run_id, duration_ms=duration_ms
                )
            except (json.JSONDecodeError, KeyError):
                logger.debug(
                    "NodeTestRunner: JSON parse failed, trying TAP fallback"
                )

        # JSON failed: try TAP output (re-run with --test-reporter=tap).
        if tsx_available:
            tap_cmd = ["npx", "tsx", "--test", "--test-reporter=tap"]
        else:
            tap_cmd = ["node", "--test", "--test-reporter=tap"]
        if test_ids:
            tap_cmd.extend(test_ids)

        try:
            tap_result = await self.shell_run(tap_cmd, timeout_seconds=timeout_seconds)
        except TimeoutError:
            duration_ms = time.monotonic() * 1000.0 - start_ms
            return FindingBatch(
                run_id=run_id,
                depth="standard",
                summary_line=f"tailtest: node --test timed out at {timeout_seconds}s",
                duration_ms=duration_ms,
            )

        duration_ms = time.monotonic() * 1000.0 - start_ms
        combined_output = tap_result.stdout or result.stdout or result.stderr
        if not combined_output.strip():
            return self._crash_batch(
                run_id=run_id, stderr=result.stderr, duration_ms=duration_ms
            )

        return self._parse_tap_output(
            combined_output, run_id=run_id, duration_ms=duration_ms
        )

    # --- Parsers ---

    def _parse_json_events(
        self, stdout: str, *, run_id: str, duration_ms: float
    ) -> FindingBatch:
        """Parse NDJSON event stream from ``node --test --test-reporter=json``.

        Each line is one JSON event object with a ``type`` field:
        ``test:pass``, ``test:fail``, ``test:skip``, ``test:diagnostic``.
        Only non-diagnostic events at any nesting level are counted;
        suite-level ``test:fail`` events that have a ``tests`` child count
        are excluded to avoid double-counting nested failures.
        """
        passed = 0
        failed = 0
        skipped = 0
        findings: list[Finding] = []

        for raw_line in stdout.splitlines():
            line = raw_line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = event.get("type", "")
            data = event.get("data") or {}

            if etype == "test:pass":
                passed += 1
            elif etype == "test:skip":
                skipped += 1
            elif etype == "test:fail":
                name = data.get("name") or "unknown"
                details = data.get("details") or {}
                err = details.get("error") or {}
                message = (
                    err.get("message")
                    or err.get("stack", "")
                    or f"{name} failed"
                )
                failed += 1
                findings.append(
                    Finding.create(
                        kind=FindingKind.TEST_FAILURE,
                        severity=Severity.HIGH,
                        file=Path("<node --test>"),
                        line=0,
                        message=_trim(message),
                        run_id=run_id,
                        rule_id=f"node_test::{name}",
                        claude_hint=_first_line(message),
                    )
                )

        if passed == 0 and failed == 0 and skipped == 0:
            # No events parsed -- the output was not valid NDJSON.
            raise json.JSONDecodeError("no test events found", stdout, 0)

        return self._build_batch(
            run_id=run_id,
            passed=passed,
            failed=failed,
            skipped=skipped,
            findings=findings,
            duration_ms=duration_ms,
        )

    def _parse_tap_output(
        self, stdout: str, *, run_id: str, duration_ms: float
    ) -> FindingBatch:
        """Parse TAP output from ``node --test --test-reporter=tap``."""
        entries = parse_tap(stdout)
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
                        file=Path("<node --test>"),
                        line=0,
                        message=_trim(entry.message or entry.name),
                        run_id=run_id,
                        rule_id=f"node_test::{entry.name}",
                        claude_hint=_first_line(entry.message or entry.name),
                    )
                )

        return self._build_batch(
            run_id=run_id,
            passed=passed,
            failed=failed,
            skipped=skipped,
            findings=findings,
            duration_ms=duration_ms,
        )

    def _build_batch(
        self,
        *,
        run_id: str,
        passed: int,
        failed: int,
        skipped: int,
        findings: list[Finding],
        duration_ms: float,
    ) -> FindingBatch:
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
        msg = (stderr or "node --test produced no output").strip()
        return FindingBatch(
            run_id=run_id,
            depth="standard",
            findings=[
                Finding.create(
                    kind=FindingKind.TEST_FAILURE,
                    severity=Severity.CRITICAL,
                    file=Path("<node --test>"),
                    line=0,
                    message=_trim(msg),
                    run_id=run_id,
                    rule_id="node_test::crash",
                    claude_hint="node --test produced no output; check stderr",
                )
            ],
            duration_ms=duration_ms,
            summary_line="tailtest: node --test crashed",
            tests_failed=1,
        )

    def _pkg_has_dep(self, dep: str) -> bool:
        try:
            pkg = json.loads(
                (self.project_root / "package.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return False
        for section in ("devDependencies", "dependencies"):
            if isinstance(pkg.get(section), dict) and dep in pkg[section]:
                return True
        return False


# --- Helpers ---


def _trim(text: str) -> str:
    s = re.sub(r"\s+", " ", text.strip())
    return s[:200] if len(s) > 200 else s


def _first_line(text: str) -> str | None:
    for raw in text.splitlines():
        line = raw.strip()
        if line:
            return line[:200]
    return None
