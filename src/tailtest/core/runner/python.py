"""PythonRunner — pytest adapter (Phase 1 Task 1.2a).

Shells out to `pytest --junitxml=<path>` and parses the JUnit XML output
into `Finding` objects. JUnit XML is used instead of the pytest-json-report
plugin because it's a pytest builtin (no extra install required) and its
format is stable and well-documented.

Test impact analysis (TIA): if `pytest-testmon` is installed in the target
project's environment, `impacted()` uses it to get the minimal set of
affected tests. Otherwise, a simple heuristic fallback scans test files
for references to the changed source file's stem — brittle but better
than running the whole suite.

The runner is registered into the default `RunnerRegistry` on import.
"""

from __future__ import annotations

import logging
import re
import tempfile
import xml.etree.ElementTree as ET
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


@register_runner
class PythonRunner(BaseRunner):
    """Pytest-based runner for Python projects.

    Discovery signals (any one is sufficient):
    - ``pyproject.toml`` with a ``[tool.pytest.ini_options]`` section
    - ``pytest.ini`` present at the project root
    - A ``tests/`` directory with at least one ``test_*.py`` file

    Discovery raises ``RunnerNotAvailable`` if ``pytest`` isn't on PATH at all.
    """

    name: ClassVar[str] = "pytest"
    language: ClassVar[str] = "python"

    # --- Discovery ---

    def discover(self) -> bool:
        if not self._has_pytest_config() and not self._has_tests_dir():
            return False
        # We need pytest available — either inside the target project's
        # own venv (preferred — has the target's deps installed) or on PATH.
        if self._resolve_pytest_path() is None:
            raise RunnerNotAvailable("pytest not found in project venv or on PATH")
        return True

    def _resolve_pytest_path(self) -> str | None:
        """Find a pytest binary, preferring the target project's own venv.

        When tailtest is invoked from its own venv targeting a different
        project, using a PATH-resolved pytest runs in tailtest's venv,
        which doesn't have the target project's dependencies installed —
        every test then collection-fails with ``ModuleNotFoundError``.
        Caught by the Checkpoint E dogfood against CoreCoder where every
        test surfaced as a generic ``<unknown>:88 collection failure``
        because the underlying error was ``ModuleNotFoundError: openai``.

        Resolution order:
        1. ``<project>/.venv/bin/pytest`` (or ``Scripts/pytest.exe`` on Windows)
        2. ``<project>/venv/bin/pytest``
        3. ``pytest`` from PATH
        4. ``None`` if nothing found
        """
        venv_candidates = [
            self.project_root / ".venv" / "bin" / "pytest",
            self.project_root / "venv" / "bin" / "pytest",
            self.project_root / ".venv" / "Scripts" / "pytest.exe",
            self.project_root / "venv" / "Scripts" / "pytest.exe",
        ]
        for candidate in venv_candidates:
            if candidate.exists():
                return str(candidate)

        import shutil as _shutil

        return _shutil.which("pytest")

    def _has_pytest_config(self) -> bool:
        pyproject = self.project_root / "pyproject.toml"
        if pyproject.exists():
            try:
                text = pyproject.read_text(encoding="utf-8")
                if "[tool.pytest.ini_options]" in text:
                    return True
            except OSError:
                pass
        if (self.project_root / "pytest.ini").exists():
            return True
        if (self.project_root / "setup.cfg").exists():
            try:
                text = (self.project_root / "setup.cfg").read_text(encoding="utf-8")
                if "[tool:pytest]" in text:
                    return True
            except OSError:
                pass
        return False

    def _has_tests_dir(self) -> bool:
        tests_dir = self.project_root / "tests"
        if not tests_dir.is_dir():
            return False
        return any(tests_dir.rglob("test_*.py"))

    async def _testmon_available(self) -> bool:
        """Check if pytest-testmon is installed in the target environment."""
        pytest_path = self._resolve_pytest_path()
        if pytest_path is None:
            return False
        try:
            result = await self.shell_run(
                [pytest_path, "--help"],
                timeout_seconds=10.0,
            )
        except (RunnerNotAvailable, TimeoutError):
            return False
        return "--testmon" in result.stdout

    # --- Impacted-test detection ---

    async def impacted(
        self,
        files: list[Path],
        diff: str | None = None,
    ) -> list[TestID]:
        """Return the list of test IDs affected by changes to ``files``.

        Prefers ``pytest --testmon --collect-only`` when testmon is
        available. Falls back to a heuristic: test files whose content
        references any changed source file's stem.
        """
        if not files:
            return []

        if await self._testmon_available():
            return await self._impacted_via_testmon()
        return self._impacted_via_heuristic(files)

    async def _impacted_via_testmon(self) -> list[TestID]:
        """Use pytest-testmon to get the exact affected test IDs."""
        pytest_path = self._resolve_pytest_path()
        if pytest_path is None:
            return []
        try:
            result = await self.shell_run(
                [
                    pytest_path,
                    "--testmon",
                    "--collect-only",
                    "-q",
                    "--no-header",
                ],
                timeout_seconds=30.0,
            )
        except (RunnerNotAvailable, TimeoutError):
            logger.warning("testmon collect-only failed; falling back")
            return []

        # testmon's output includes lines like:
        #   tests/test_foo.py::test_bar
        # plus a summary. We parse lines that contain ::.
        ids: list[TestID] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if "::" in line and line.startswith("tests"):
                ids.append(line)
        return ids

    def _impacted_via_heuristic(self, changed_files: list[Path]) -> list[TestID]:
        """Fallback: return tests whose contents reference any changed file stem.

        This is O(files × tests) naive, correct-but-slow. Missing pytest-testmon
        means we accept slower runs in exchange for not running the whole suite.
        """
        stems = {Path(f).stem for f in changed_files if Path(f).suffix == ".py"}
        # Also include the module path (with dots) for import-style matches
        module_names = set()
        for f in changed_files:
            fp = Path(f)
            if fp.suffix != ".py":
                continue
            # Best-effort: tailtest.core.findings.schema, etc.
            parts = list(fp.with_suffix("").parts)
            # Drop common top-level dirs
            while parts and parts[0] in {"src", "lib", "app", "pkg"}:
                parts = parts[1:]
            if parts:
                module_names.add(".".join(parts))
                module_names.add(parts[-1])

        search_terms = stems | module_names
        if not search_terms:
            return []

        result_ids: list[TestID] = []
        tests_dir = self.project_root / "tests"
        if not tests_dir.exists():
            return []
        for test_file in tests_dir.rglob("test_*.py"):
            # Skip files inside fixture project subdirectories. Fixtures are
            # self-contained pytest rootdirs (per the 2026-04-09 layout
            # refactor) and outer pytest cannot collect them without their
            # own pyproject.toml's pythonpath. The heuristic was matching
            # them on docstring content alone, producing collection failures
            # when the runner tried to execute them. Caught by Checkpoint E
            # dogfood: a tailtest run --changed scanner.py was matching
            # tests/fixtures/scanner_python_ai/tests/test_placeholder.py
            # because its docstring mentioned "scanner".
            if "fixtures" in test_file.parts:
                continue
            try:
                content = test_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if any(term in content for term in search_terms):
                # Run all tests in this file — we don't know the function level.
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
        collect_coverage: bool = False,
        added_lines: dict[str, set[int]] | None = None,
    ) -> FindingBatch:
        """Execute the given tests and return a structured FindingBatch.

        If ``test_ids`` is empty, pytest will run every test. Callers that
        want a no-op should not call this method.

        When ``collect_coverage`` is True and the target project has
        ``coverage`` in its venv (or on PATH), tests run under
        ``coverage run -m pytest ...`` and the coverage data gets
        parsed into a JSON report. If ``added_lines`` is also provided
        (a map of ``{file_str: set[int]}`` with 1-indexed line numbers
        that the most recent edit introduced or modified), the runner
        computes delta coverage and populates
        ``FindingBatch.delta_coverage_pct`` and
        ``FindingBatch.uncovered_new_lines``. Any coverage-collection
        failure degrades silently to a run without coverage rather
        than blocking the hot loop.
        """
        pytest_path = self._resolve_pytest_path()
        if pytest_path is None:
            return self._build_crash_batch(
                run_id=run_id,
                stderr="pytest not found in project venv or on PATH",
                stdout="",
                duration_ms=0.0,
            )

        # Resolve coverage binary if coverage collection was requested.
        # Missing coverage downgrades the run to "no coverage" rather
        # than erroring out, matching the testmon fallback pattern.
        coverage_bin: str | None = None
        if collect_coverage:
            from tailtest.core.coverage import resolve_coverage_bin

            coverage_bin = resolve_coverage_bin(self.project_root)
            if coverage_bin is None:
                logger.info(
                    "coverage.py not found in project venv or on PATH; "
                    "delta coverage unavailable for this run"
                )

        with tempfile.TemporaryDirectory(prefix="tailtest-pytest-") as tmp_dir:
            junit_path = Path(tmp_dir) / "junit.xml"
            coverage_data_file: Path | None = None
            coverage_json_file: Path | None = None

            if coverage_bin:
                coverage_data_file = Path(tmp_dir) / ".coverage"
                coverage_json_file = Path(tmp_dir) / "coverage.json"
                cmd = [
                    coverage_bin,
                    "run",
                    f"--data-file={coverage_data_file}",
                    "-m",
                    "pytest",
                    "--tb=short",
                    "-q",
                    "--no-header",
                    f"--junitxml={junit_path}",
                ]
            else:
                cmd = [
                    pytest_path,
                    "--tb=short",
                    "-q",
                    "--no-header",
                    f"--junitxml={junit_path}",
                ]
            if test_ids:
                cmd.extend(test_ids)

            start_time = self._monotonic_ms()
            try:
                result = await self.shell_run(cmd, timeout_seconds=timeout_seconds)
            except TimeoutError:
                return FindingBatch(
                    run_id=run_id,
                    depth="standard",
                    summary_line=f"tailtest: pytest timed out at {timeout_seconds}s",
                    duration_ms=(self._monotonic_ms() - start_time),
                )

            duration_ms = self._monotonic_ms() - start_time

            if not junit_path.exists():
                # pytest crashed before writing the JUnit file, capture stderr.
                return self._build_crash_batch(
                    run_id=run_id,
                    stderr=result.stderr,
                    stdout=result.stdout,
                    duration_ms=duration_ms,
                )

            try:
                batch = self._parse_junit(
                    junit_xml=junit_path.read_text(encoding="utf-8"),
                    run_id=run_id,
                    duration_ms=duration_ms,
                )
            except ET.ParseError as exc:
                logger.error("Failed to parse pytest JUnit XML: %s", exc)
                return self._build_crash_batch(
                    run_id=run_id,
                    stderr=f"invalid JUnit XML: {exc}",
                    stdout=result.stdout,
                    duration_ms=duration_ms,
                )

            # Delta coverage pass (Phase 1 Task 1.8a). Only runs when
            # the caller requested coverage, coverage.py was available,
            # and the caller passed a map of added lines. Any failure
            # here is swallowed so the hot loop never breaks because
            # delta coverage could not be computed.
            if (
                coverage_bin
                and coverage_data_file
                and coverage_json_file
                and coverage_data_file.exists()
                and added_lines
            ):
                try:
                    await self.shell_run(
                        [
                            coverage_bin,
                            "json",
                            f"--data-file={coverage_data_file}",
                            "-o",
                            str(coverage_json_file),
                        ],
                        timeout_seconds=30.0,
                    )
                except (RunnerNotAvailable, TimeoutError) as exc:
                    logger.info("coverage json failed: %s", exc)
                else:
                    if coverage_json_file.exists():
                        from tailtest.core.coverage import (
                            compute_delta_coverage,
                            parse_coverage_json,
                        )

                        covered = parse_coverage_json(coverage_json_file)
                        added_paths = {Path(k): set(v) for k, v in added_lines.items()}
                        report = compute_delta_coverage(added_paths, covered)
                        batch = batch.model_copy(update=report.to_finding_batch_fields())

            return batch

    # --- Parsing helpers ---

    def _parse_junit(
        self,
        *,
        junit_xml: str,
        run_id: str,
        duration_ms: float,
    ) -> FindingBatch:
        """Parse pytest's JUnit XML output into a FindingBatch."""
        root = ET.fromstring(junit_xml)
        # root may be either <testsuites> or a single <testsuite>
        if root.tag == "testsuites":
            suites = list(root.findall("testsuite"))
        elif root.tag == "testsuite":
            suites = [root]
        else:
            suites = []

        passed = 0
        failed = 0
        skipped = 0
        findings: list[Finding] = []

        for suite in suites:
            for testcase in suite.findall("testcase"):
                classname = testcase.get("classname", "")
                name = testcase.get("name", "")
                file_attr = testcase.get("file", "")

                failure_el = testcase.find("failure")
                error_el = testcase.find("error")
                skipped_el = testcase.find("skipped")

                if skipped_el is not None:
                    skipped += 1
                    continue

                if failure_el is None and error_el is None:
                    passed += 1
                    continue

                failed += 1
                issue_el = failure_el if failure_el is not None else error_el
                assert issue_el is not None  # for type checker
                message_attr = issue_el.get("message", "")
                text_content = (issue_el.text or "").strip()
                # For collection errors pytest sets message="collection failure"
                # and puts the actual ImportError / SyntaxError into the text
                # body. The earlier `message_attr or text_content` short-circuited
                # on the generic message and lost the underlying error — caught
                # by the Checkpoint E CoreCoder dogfood where every test
                # surfaced as `<unknown>:88 collection failure` with no hint
                # that the real cause was `ModuleNotFoundError: openai`.
                # Always combine when both are present so the user sees the
                # underlying error.
                if message_attr and text_content:
                    message = f"{message_attr}: {text_content}"
                else:
                    message = message_attr or text_content

                file_path = self._resolve_file(file_attr, classname)
                line_no = self._extract_line_number(message, issue_el.text or "")
                test_label = f"{classname}::{name}" if classname else name

                finding = Finding.create(
                    kind=FindingKind.TEST_FAILURE,
                    severity=Severity.HIGH,
                    file=file_path,
                    line=line_no,
                    message=self._summarize(message),
                    run_id=run_id,
                    rule_id=f"pytest::{test_label}",
                    claude_hint=self._claude_hint(message, issue_el.text or ""),
                )
                findings.append(finding)

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
        """Build a FindingBatch representing a pytest crash (no JUnit produced)."""
        message = (stderr or stdout or "pytest crashed with no output").strip()
        finding = Finding.create(
            kind=FindingKind.TEST_FAILURE,
            severity=Severity.CRITICAL,
            file="<pytest>",
            line=0,
            message=self._summarize(message),
            run_id=run_id,
            rule_id="pytest::crash",
            claude_hint="pytest failed to produce a JUnit report — check the full stderr",
        )
        return FindingBatch(
            run_id=run_id,
            depth="standard",
            findings=[finding],
            duration_ms=duration_ms,
            summary_line="tailtest: pytest crashed",
            tests_failed=1,
        )

    def _resolve_file(self, file_attr: str, classname: str) -> Path:
        """Figure out the file path for a failing test from JUnit attrs."""
        if file_attr:
            return Path(file_attr)
        if classname:
            # classname is dotted like "tests.test_foo"
            return Path(*classname.split(".")).with_suffix(".py")
        return Path("<unknown>")

    def _extract_line_number(self, message: str, body: str) -> int:
        """Pull a line number out of a pytest failure message."""
        combined = f"{message}\n{body}"
        match = re.search(r":(\d+):", combined)
        if match:
            return int(match.group(1))
        match = re.search(r"line (\d+)", combined, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return 0

    def _summarize(self, text: str) -> str:
        """Trim a pytest message to one compact line."""
        stripped = text.strip().replace("\n", " ")
        if len(stripped) <= 200:
            return stripped
        return stripped[:197] + "..."

    def _claude_hint(self, message: str, body: str) -> str | None:
        """Extract a short actionable hint for Claude's next turn."""
        combined = f"{message}\n{body}"
        # Prefer a one-line assertion message
        match = re.search(r"AssertionError:\s*(.+)", combined)
        if match:
            hint = match.group(1).strip().split("\n", 1)[0]
            return hint[:200]
        if "assert " in combined:
            match2 = re.search(r"assert\s+.+?$", combined, re.MULTILINE)
            if match2:
                return match2.group(0)[:200]
        return None

    def _monotonic_ms(self) -> float:
        import time

        return time.monotonic() * 1000.0
