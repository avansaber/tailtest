# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""JSRunner, vitest-first / jest-fallback adapter (Phase 1 Task 1.2b).

Shells out to `npx vitest run --reporter=json` or `npx jest --json` and
parses the JSON output into `Finding` objects. Both runners produce
structured JSON reports that are stable and well-documented.

Runner selection (auto):

1. If `vitest.config.*` exists OR `vitest` is in `package.json`
   devDependencies, use vitest.
2. Else if `jest.config.*` exists OR `jest` is in `package.json`
   devDependencies, use jest.
3. Else `discover()` returns False (project does not use a recognized JS
   test runner).

When both are present, vitest wins. It's the modern default for new
TypeScript projects and matches the in-tree `scanner_typescript_ai`
fixture.

TIA (impacted tests): delegates to the runner's native feature:
- vitest: `npx vitest related <files> --reporter=json`
- jest: `npx jest --findRelatedTests <files> --listTests`

Heuristic fallback (same shape as PythonRunner's): scan test files for
references to the changed source file's stem. Catches everything native
TIA can't reach without a runtime graph.

The runner is registered into the default `RunnerRegistry` on import.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path
from typing import ClassVar, Literal

from tailtest.core.findings.schema import (
    Finding,
    FindingBatch,
    FindingKind,
    Severity,
)
from tailtest.core.runner.base import (
    BaseRunner,
    RunnerNotAvailable,
    TestID,
    register_runner,
)

logger = logging.getLogger(__name__)

# Test framework variants. Exposed as a type for callers that want to
# query or override which runner a JSRunner instance is using.
JSFramework = Literal["vitest", "jest"]

_VITEST_CONFIG_NAMES = (
    "vitest.config.ts",
    "vitest.config.js",
    "vitest.config.mjs",
    "vitest.config.cjs",
    "vite.config.ts",  # vitest can piggyback on vite.config
    "vite.config.js",
)

_JEST_CONFIG_NAMES = (
    "jest.config.ts",
    "jest.config.js",
    "jest.config.mjs",
    "jest.config.cjs",
    "jest.config.json",
)

_JS_TEST_FILE_GLOBS = (
    "*.test.ts",
    "*.test.tsx",
    "*.test.js",
    "*.test.jsx",
    "*.spec.ts",
    "*.spec.tsx",
    "*.spec.js",
    "*.spec.jsx",
)


@register_runner
class JSRunner(BaseRunner):
    """JavaScript/TypeScript test runner adapter.

    Supports vitest (preferred) and jest. Auto-selects based on
    ``package.json`` devDependencies and on-disk config files. When both
    are declared, vitest wins.

    Discovery signals (any is sufficient):
    - a ``package.json`` with ``vitest`` or ``jest`` in devDependencies
    - a ``vitest.config.*`` or ``jest.config.*`` file at the project root
    - a ``tests/``, ``test/``, or ``__tests__/`` directory with at least
      one ``*.test.ts`` / ``*.test.js`` / ``*.spec.ts`` / ``*.spec.js``
      file

    Discovery raises ``RunnerNotAvailable`` if ``npx`` is not on PATH.
    """

    name: ClassVar[str] = "jsrunner"
    language: ClassVar[str] = "javascript"

    # Populated by `discover()`, consumed by `run()` / `impacted()`.
    # Default is vitest; if discovery picks jest, this flips.
    _framework: JSFramework = "vitest"

    def __init__(self, project_root: Path) -> None:
        super().__init__(project_root)
        self._framework = "vitest"

    # --- Discovery ---

    def discover(self) -> bool:
        has_vitest = self._has_vitest()
        has_jest = self._has_jest()

        # Phase 1 Checkpoint G dogfood finding: the earlier version
        # ALSO returned True when only a `tests/` directory with
        # test files existed, falling back to vitest as the default.
        # That produced a false positive on Feynman, which has
        # `tests/*.test.ts` files but uses Node's built-in
        # `node --test` runner (not vitest or jest). JSRunner would
        # then shell out to `npx vitest run` and crash.
        #
        # The fix: require an explicit framework signal, either a
        # config file on disk or a package.json devDependencies
        # entry. A bare tests dir is not enough. Projects using
        # node --test, ava, mocha, or tape will need their own
        # runner in a follow-up (tracked in the Phase 1 retro).
        if not (has_vitest or has_jest):
            return False

        # When both are present, prefer vitest (modern default for new
        # TypeScript projects). When only jest is present, use jest.
        if has_jest and not has_vitest:
            self._framework = "jest"
        else:
            self._framework = "vitest"

        # We need `npx` on PATH to invoke either runner. `npx` is part
        # of Node / npm; missing it means the target project has no
        # Node toolchain available at all.
        if shutil.which("npx") is None:
            raise RunnerNotAvailable("npx binary not on PATH")
        return True

    @property
    def framework(self) -> JSFramework:
        """Return the test framework this runner selected during discovery.

        Useful for doctor output and for callers that want to surface
        which runner is in use without re-running discovery.
        """
        return self._framework

    def _has_vitest(self) -> bool:
        # Check on-disk config files first (cheap).
        for name in _VITEST_CONFIG_NAMES:
            if (self.project_root / name).exists():
                return True
        # Fall back to package.json inspection.
        return self._package_json_has("vitest")

    def _has_jest(self) -> bool:
        for name in _JEST_CONFIG_NAMES:
            if (self.project_root / name).exists():
                return True
        return self._package_json_has("jest")

    def _package_json_has(self, dep_name: str) -> bool:
        """Return True if ``dep_name`` appears in package.json devDeps or deps."""
        pkg = self.project_root / "package.json"
        if not pkg.exists():
            return False
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        for section in ("devDependencies", "dependencies"):
            if isinstance(data.get(section), dict) and dep_name in data[section]:
                return True
        return False

    def _has_test_dir(self) -> bool:
        for dirname in ("tests", "test", "__tests__"):
            test_dir = self.project_root / dirname
            if not test_dir.is_dir():
                continue
            for pattern in _JS_TEST_FILE_GLOBS:
                if any(test_dir.rglob(pattern)):
                    return True
        return False

    # --- Impacted-test detection ---

    async def impacted(
        self,
        files: list[Path],
        diff: str | None = None,
    ) -> list[TestID]:
        """Return the list of test IDs affected by changes to ``files``.

        Uses the runner's native related-tests feature (vitest or jest).
        Falls back to a heuristic that scans JS/TS test files for the
        changed source file's stem when the native path fails.
        """
        if not files:
            return []

        try:
            if self._framework == "vitest":
                native = await self._impacted_via_vitest(files)
            else:
                native = await self._impacted_via_jest(files)
        except (RunnerNotAvailable, TimeoutError) as exc:
            logger.warning("native TIA failed: %s; falling back to heuristic", exc)
            return self._impacted_via_heuristic(files)

        if native:
            return native

        # The native call succeeded but returned nothing. That's a
        # legitimate "no impacted tests" answer for a file that isn't
        # referenced by any test. Don't fall back; trust the runner.
        return []

    async def _impacted_via_vitest(self, files: list[Path]) -> list[TestID]:
        """Use ``vitest related`` to get impacted test files."""
        file_args = [str(f) for f in files]
        try:
            result = await self.shell_run(
                [
                    "npx",
                    "vitest",
                    "related",
                    "--run",
                    "--reporter=json",
                    *file_args,
                ],
                timeout_seconds=30.0,
            )
        except (RunnerNotAvailable, TimeoutError):
            raise

        ids: list[TestID] = []
        # vitest --reporter=json emits a single JSON object. Parse it
        # and extract `testResults[*].name` which is the test file path.
        try:
            data = json.loads(result.stdout)
            for tr in data.get("testResults", []):
                name = tr.get("name")
                if isinstance(name, str) and name:
                    ids.append(name)
        except json.JSONDecodeError:
            # vitest sometimes emits non-JSON banners even with
            # --reporter=json. Parse line by line as a fallback.
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.startswith("{") and "testResults" in line:
                    try:
                        data = json.loads(line)
                        for tr in data.get("testResults", []):
                            name = tr.get("name")
                            if isinstance(name, str) and name:
                                ids.append(name)
                    except json.JSONDecodeError:
                        continue
        return ids

    async def _impacted_via_jest(self, files: list[Path]) -> list[TestID]:
        """Use ``jest --findRelatedTests --listTests`` to get impacted file paths."""
        file_args = [str(f) for f in files]
        try:
            result = await self.shell_run(
                [
                    "npx",
                    "jest",
                    "--findRelatedTests",
                    "--listTests",
                    *file_args,
                ],
                timeout_seconds=30.0,
            )
        except (RunnerNotAvailable, TimeoutError):
            raise

        # `jest --listTests` prints one file path per line.
        ids: list[TestID] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if line and ("/" in line or "\\" in line):
                ids.append(line)
        return ids

    def _impacted_via_heuristic(self, changed_files: list[Path]) -> list[TestID]:
        """Fallback: return test files whose contents reference any changed file stem.

        Mirrors PythonRunner's heuristic. O(files * tests) naive scan.
        Missing native TIA means slower hook runs in exchange for not
        running the whole suite.
        """
        stems: set[str] = set()
        for f in changed_files:
            fp = Path(f)
            if fp.suffix in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
                stems.add(fp.stem)
        if not stems:
            return []

        result_ids: list[TestID] = []
        for dirname in ("tests", "test", "__tests__", "src"):
            test_dir = self.project_root / dirname
            if not test_dir.is_dir():
                continue
            for pattern in _JS_TEST_FILE_GLOBS:
                for test_file in test_dir.rglob(pattern):
                    # Skip fixtures by the same convention as PythonRunner.
                    if "fixtures" in test_file.parts:
                        continue
                    try:
                        content = test_file.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    if any(stem in content for stem in stems):
                        rel = test_file.relative_to(self.project_root)
                        result_ids.append(str(rel))
        return result_ids

    # --- Test execution ---

    async def run(
        self,
        test_ids: list[TestID],
        *,
        run_id: str,
        timeout_seconds: float = 30.0,
    ) -> FindingBatch:
        """Execute the given tests and return a structured FindingBatch.

        Empty ``test_ids`` means "run everything" (both jest and vitest
        default to running the full suite with no file arguments).
        """
        start_time = self._monotonic_ms()

        if self._framework == "vitest":
            cmd = ["npx", "vitest", "run", "--reporter=json"]
        else:
            cmd = ["npx", "jest", "--json"]

        if test_ids:
            cmd.extend(test_ids)

        try:
            result = await self.shell_run(cmd, timeout_seconds=timeout_seconds)
        except TimeoutError:
            return FindingBatch(
                run_id=run_id,
                depth="standard",
                summary_line=f"tailtest: {self._framework} timed out at {timeout_seconds}s",
                duration_ms=(self._monotonic_ms() - start_time),
            )

        duration_ms = self._monotonic_ms() - start_time

        if not result.stdout.strip():
            return self._build_crash_batch(
                run_id=run_id,
                stderr=result.stderr,
                stdout=result.stdout,
                duration_ms=duration_ms,
            )

        try:
            if self._framework == "vitest":
                return self._parse_vitest_json(
                    stdout=result.stdout,
                    run_id=run_id,
                    duration_ms=duration_ms,
                )
            return self._parse_jest_json(
                stdout=result.stdout,
                run_id=run_id,
                duration_ms=duration_ms,
            )
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.error("Failed to parse %s JSON output: %s", self._framework, exc)
            return self._build_crash_batch(
                run_id=run_id,
                stderr=f"invalid {self._framework} JSON: {exc}",
                stdout=result.stdout,
                duration_ms=duration_ms,
            )

    # --- Parsing helpers ---

    def _parse_vitest_json(
        self,
        *,
        stdout: str,
        run_id: str,
        duration_ms: float,
    ) -> FindingBatch:
        """Parse vitest's JSON reporter output into a FindingBatch.

        vitest's JSON shape (v1.x, stable): top-level object with
        ``testResults: list[FileResult]``, each with ``assertionResults:
        list[AssertionResult]``, each with ``status``, ``title``,
        ``fullName``, ``failureMessages``, ``location`` (file/line/col).
        """
        # vitest sometimes emits a banner before the JSON object. Find
        # the first `{` and parse from there.
        json_start = stdout.find("{")
        if json_start < 0:
            raise json.JSONDecodeError("no JSON object found", stdout, 0)
        data = json.loads(stdout[json_start:])

        passed = 0
        failed = 0
        skipped = 0
        findings: list[Finding] = []

        for file_result in data.get("testResults", []):
            file_name = file_result.get("name", "")
            for assertion in file_result.get("assertionResults", []):
                status = assertion.get("status")
                title = assertion.get("fullName") or assertion.get("title", "")
                if status == "passed":
                    passed += 1
                    continue
                if status in ("pending", "skipped", "todo"):
                    skipped += 1
                    continue

                # Anything else is a failure.
                failed += 1
                failure_messages = assertion.get("failureMessages") or []
                message = " ".join(failure_messages).strip() or f"{title} failed"
                location = assertion.get("location") or {}
                line = 0
                if isinstance(location, dict):
                    try:
                        line = int(location.get("line") or 0)
                    except (TypeError, ValueError):
                        line = 0
                file_path = Path(file_name) if file_name else Path("<unknown>")

                finding = Finding.create(
                    kind=FindingKind.TEST_FAILURE,
                    severity=Severity.HIGH,
                    file=file_path,
                    line=line,
                    message=self._summarize(message),
                    run_id=run_id,
                    rule_id=f"vitest::{title}",
                    claude_hint=self._first_line(message),
                )
                findings.append(finding)

        return self._build_batch(
            run_id=run_id,
            passed=passed,
            failed=failed,
            skipped=skipped,
            findings=findings,
            duration_ms=duration_ms,
        )

    def _parse_jest_json(
        self,
        *,
        stdout: str,
        run_id: str,
        duration_ms: float,
    ) -> FindingBatch:
        """Parse jest's JSON reporter output into a FindingBatch.

        jest's JSON shape: top-level object with ``testResults: list``.
        Each file result has ``testResults: list`` (same field name,
        different scope) with ``status``, ``title``, ``fullName``,
        ``failureMessages``, ``location``.
        """
        json_start = stdout.find("{")
        if json_start < 0:
            raise json.JSONDecodeError("no JSON object found", stdout, 0)
        data = json.loads(stdout[json_start:])

        passed = 0
        failed = 0
        skipped = 0
        findings: list[Finding] = []

        for file_result in data.get("testResults", []):
            file_name = file_result.get("name", "") or file_result.get("testFilePath", "")
            for testcase in file_result.get("testResults", []):
                status = testcase.get("status")
                title = testcase.get("fullName") or testcase.get("title", "")
                if status == "passed":
                    passed += 1
                    continue
                if status in ("pending", "skipped", "todo"):
                    skipped += 1
                    continue

                failed += 1
                failure_messages = testcase.get("failureMessages") or []
                message = " ".join(failure_messages).strip() or f"{title} failed"
                line = 0
                location = testcase.get("location") or {}
                if isinstance(location, dict):
                    try:
                        line = int(location.get("line") or 0)
                    except (TypeError, ValueError):
                        line = 0
                file_path = Path(file_name) if file_name else Path("<unknown>")

                finding = Finding.create(
                    kind=FindingKind.TEST_FAILURE,
                    severity=Severity.HIGH,
                    file=file_path,
                    line=line,
                    message=self._summarize(message),
                    run_id=run_id,
                    rule_id=f"jest::{title}",
                    claude_hint=self._first_line(message),
                )
                findings.append(finding)

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

    def _build_crash_batch(
        self,
        *,
        run_id: str,
        stderr: str,
        stdout: str,
        duration_ms: float,
    ) -> FindingBatch:
        """Build a FindingBatch representing a runner crash (no usable output)."""
        combined = (stderr or stdout or f"{self._framework} crashed with no output").strip()
        finding = Finding.create(
            kind=FindingKind.TEST_FAILURE,
            severity=Severity.CRITICAL,
            file=Path(f"<{self._framework}>"),
            line=0,
            message=self._summarize(combined),
            run_id=run_id,
            rule_id=f"{self._framework}::crash",
            claude_hint=f"{self._framework} failed to produce JSON output, check the full stderr",
        )
        return FindingBatch(
            run_id=run_id,
            depth="standard",
            findings=[finding],
            duration_ms=duration_ms,
            summary_line=f"tailtest: {self._framework} crashed",
            tests_failed=1,
        )

    @staticmethod
    def _summarize(text: str) -> str:
        """Trim a JS runner failure message to one compact line."""
        stripped = re.sub(r"\s+", " ", text.strip())
        if len(stripped) <= 200:
            return stripped
        return stripped[:197] + "..."

    @staticmethod
    def _first_line(text: str) -> str | None:
        """Return the first non-empty line of a failure message, or None."""
        for raw in text.splitlines():
            line = raw.strip()
            if line:
                return line[:200]
        return None

    @staticmethod
    def _monotonic_ms() -> float:
        import time

        return time.monotonic() * 1000.0
