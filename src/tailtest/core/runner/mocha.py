"""MochaRunner -- Mocha test framework adapter (Phase 4.5.9).

Mocha is a widely-used JS/TS test framework. This adapter uses Mocha's
built-in ``--reporter=json`` flag which emits a single well-documented
JSON object to stdout. No external plugin required.

Discovery signals (any one is sufficient):
  - ``mocha`` in ``package.json`` devDependencies or dependencies
  - ``.mocharc.{js,ts,mjs,cjs,yaml,yml,json,jsonc}`` at the project root
  - ``mocha.opts`` at ``test/mocha.opts`` (legacy)

Discovery requires ``npx`` on PATH.

TIA: Mocha has no native related-tests feature. Uses stem-based heuristic.

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
from tailtest.core.runner.base import (
    BaseRunner,
    RunnerNotAvailable,
    TestID,
    register_runner,
)

logger = logging.getLogger(__name__)

_MOCHARC_NAMES = (
    ".mocharc.js",
    ".mocharc.ts",
    ".mocharc.mjs",
    ".mocharc.cjs",
    ".mocharc.yaml",
    ".mocharc.yml",
    ".mocharc.json",
    ".mocharc.jsonc",
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
class MochaRunner(BaseRunner):
    """Mocha test runner adapter.

    Uses ``npx mocha --reporter=json`` to obtain structured output.
    """

    name: ClassVar[str] = "mocha"
    language: ClassVar[str] = "mocha"

    def discover(self) -> bool:
        if not self._has_mocha():
            return False
        if shutil.which("npx") is None:
            raise RunnerNotAvailable("npx binary not on PATH")
        return True

    def _has_mocha(self) -> bool:
        # On-disk config files.
        for name in _MOCHARC_NAMES:
            if (self.project_root / name).exists():
                return True
        # Legacy opts file.
        if (self.project_root / "test" / "mocha.opts").exists():
            return True
        # package.json deps.
        return self._pkg_has_dep("mocha")

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
        for dirname in ("test", "tests", "__tests__", "src"):
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
        """Execute mocha with ``--reporter=json`` and parse the result."""
        import time

        start_ms = time.monotonic() * 1000.0
        cmd = ["npx", "mocha", "--reporter=json"]
        if test_ids:
            cmd.extend(test_ids)

        try:
            result = await self.shell_run(cmd, timeout_seconds=timeout_seconds)
        except TimeoutError:
            duration_ms = time.monotonic() * 1000.0 - start_ms
            return FindingBatch(
                run_id=run_id,
                depth="standard",
                summary_line=f"tailtest: mocha timed out at {timeout_seconds}s",
                duration_ms=duration_ms,
            )

        duration_ms = time.monotonic() * 1000.0 - start_ms

        if not result.stdout.strip():
            return self._crash_batch(run_id=run_id, stderr=result.stderr, duration_ms=duration_ms)

        try:
            return self._parse_mocha_json(result.stdout, run_id=run_id, duration_ms=duration_ms)
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.error("MochaRunner: failed to parse JSON output: %s", exc)
            return self._crash_batch(
                run_id=run_id,
                stderr=f"invalid mocha JSON: {exc}",
                duration_ms=duration_ms,
            )

    # --- Parser ---

    def _parse_mocha_json(self, stdout: str, *, run_id: str, duration_ms: float) -> FindingBatch:
        """Parse mocha ``--reporter=json`` output.

        Mocha JSON shape::

            {
              "stats": {"passes": N, "failures": N, "pending": N, ...},
              "passes": [...],
              "failures": [{"title": "...", "fullTitle": "...", "file": "...",
                             "err": {"message": "...", "stack": "..."}}],
              "pending": [...],
            }
        """
        json_start = stdout.find("{")
        if json_start < 0:
            raise json.JSONDecodeError("no JSON object in mocha output", stdout, 0)
        data = json.loads(stdout[json_start:])

        stats = data.get("stats") or {}
        passed = int(stats.get("passes") or 0)
        failed = int(stats.get("failures") or 0)
        skipped = int(stats.get("pending") or 0)

        findings: list[Finding] = []
        for failure in data.get("failures") or []:
            title = failure.get("fullTitle") or failure.get("title") or "unknown"
            err = failure.get("err") or {}
            message = err.get("message") or err.get("stack") or f"{title} failed"
            file_str = failure.get("file") or "<mocha>"
            findings.append(
                Finding.create(
                    kind=FindingKind.TEST_FAILURE,
                    severity=Severity.HIGH,
                    file=Path(file_str),
                    line=0,
                    message=_trim(message),
                    run_id=run_id,
                    rule_id=f"mocha::{title}",
                    claude_hint=_first_line(message),
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

    def _crash_batch(self, *, run_id: str, stderr: str, duration_ms: float) -> FindingBatch:
        msg = (stderr or "mocha produced no output").strip()
        return FindingBatch(
            run_id=run_id,
            depth="standard",
            findings=[
                Finding.create(
                    kind=FindingKind.TEST_FAILURE,
                    severity=Severity.CRITICAL,
                    file=Path("<mocha>"),
                    line=0,
                    message=_trim(msg),
                    run_id=run_id,
                    rule_id="mocha::crash",
                    claude_hint="mocha produced no JSON output; check stderr",
                )
            ],
            duration_ms=duration_ms,
            summary_line="tailtest: mocha crashed",
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
