# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""RustRunner -- cargo test adapter (Phase 4.5 Task 4.5.1).

Shells out to `cargo test` and parses the plain-text output into
`Finding` objects.  No nextest required -- uses the stable `cargo test`
output format present in every Rust toolchain.

TIA: maps each changed file to the nearest `Cargo.toml` (walking up the
directory tree) and extracts the crate name via `[package].name`.  If any
file cannot be mapped to a crate, `__all__` is returned and the whole
workspace is re-tested.

The runner is registered into the default `RunnerRegistry` on import.
"""

from __future__ import annotations

import logging
import re
import shutil
import tomllib
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

# Sentinel value meaning "run every crate in the workspace / project".
_ALL_SENTINEL = "__all__"


def _cargo_path() -> str | None:
    """Return the absolute path to the `cargo` binary, or None."""
    return shutil.which("cargo")


def _read_package_name(cargo_toml: Path) -> str | None:
    """Read ``[package].name`` from a ``Cargo.toml``.  Returns None on error."""
    try:
        data = tomllib.loads(cargo_toml.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.debug("Failed to parse %s: %s", cargo_toml, exc)
        return None
    pkg = data.get("package")
    if isinstance(pkg, dict):
        name = pkg.get("name")
        if isinstance(name, str):
            return name
    return None


def _is_workspace_toml(cargo_toml: Path) -> bool:
    """Return True if the Cargo.toml has a ``[workspace]`` section."""
    try:
        data = tomllib.loads(cargo_toml.read_text(encoding="utf-8"))
        return "workspace" in data
    except (OSError, tomllib.TOMLDecodeError):
        return False


@register_runner
class RustRunner(BaseRunner):
    """Cargo-based runner for Rust projects.

    Discovery signals (any one is sufficient):
    - ``Cargo.toml`` at project root (single-crate or workspace)

    Discovery raises ``RunnerNotAvailable`` if ``cargo`` is not on PATH
    but a ``Cargo.toml`` exists (cargo missing, not just wrong project).
    """

    name: ClassVar[str] = "cargo"
    language: ClassVar[str] = "rust"

    # --- Discovery ---

    def discover(self) -> bool:
        cargo_toml = self.project_root / "Cargo.toml"
        if not cargo_toml.exists():
            return False
        # Cargo.toml exists -- this is a Rust project.  cargo must be present.
        if _cargo_path() is None:
            raise RunnerNotAvailable(
                "Cargo.toml found but `cargo` is not on PATH. Install Rust from https://rustup.rs/"
            )
        return True

    # --- Crate-root resolution ---

    def _find_crate_root(self, file: Path) -> Path | None:
        """Walk up from ``file`` to find the directory containing ``Cargo.toml``.

        Returns the directory that contains the nearest ``Cargo.toml``, or
        None if none is found before the filesystem root.
        """
        # Make the file absolute so parent traversal works regardless of cwd.
        try:
            candidate = file.resolve()
        except OSError:
            candidate = file
        # Start from the file's parent; the file itself is not a directory.
        current = candidate.parent if candidate.is_file() else candidate
        while True:
            if (current / "Cargo.toml").exists():
                return current
            parent = current.parent
            if parent == current:
                # Reached the filesystem root.
                return None
            current = parent

    # --- Test impact analysis ---

    async def impacted(
        self,
        files: list[Path],
        diff: str | None = None,
    ) -> list[TestID]:
        """Map changed files to the crate names that contain them.

        Returns a list of crate name strings (e.g. ``["my_crate"]``) that
        should be re-tested, or an empty list when none of the files map to
        a known crate (which the caller interprets as "run all").

        The special sentinel ``"__all__"`` is returned as a single-element
        list when any file cannot be mapped to a Cargo.toml.
        """
        if not files:
            return []

        crate_names: list[str] = []
        seen: set[str] = set()

        for f in files:
            crate_root = self._find_crate_root(f)
            if crate_root is None:
                logger.debug("No Cargo.toml found for %s; falling back to __all__", f)
                return [_ALL_SENTINEL]

            cargo_toml = crate_root / "Cargo.toml"
            # Workspace root Cargo.toml has no [package].name; skip it.
            name = _read_package_name(cargo_toml)
            if name is None:
                logger.debug(
                    "Could not read package name from %s; falling back to __all__", cargo_toml
                )
                return [_ALL_SENTINEL]

            if name not in seen:
                seen.add(name)
                crate_names.append(name)

        return crate_names

    # --- Test execution ---

    async def run(
        self,
        test_ids: list[TestID],
        *,
        run_id: str,
        timeout_seconds: float = 120.0,
    ) -> FindingBatch:
        """Execute cargo tests and return a FindingBatch.

        When ``test_ids`` is empty or contains ``"__all__"``, all tests in
        the workspace / project are run.  Otherwise each element is treated
        as a crate name and run with ``--package <name>``.
        """
        cargo = _cargo_path()
        if cargo is None:
            return FindingBatch(
                run_id=run_id,
                depth="standard",
                summary_line="cargo not found on PATH",
                tests_failed=1,
                findings=[
                    Finding.create(
                        kind=FindingKind.TEST_FAILURE,
                        severity=Severity.CRITICAL,
                        file="<cargo>",
                        line=0,
                        message="cargo binary not found on PATH",
                        run_id=run_id,
                        rule_id="rust-cargo-missing",
                    )
                ],
            )

        run_all = not test_ids or _ALL_SENTINEL in test_ids

        import time

        start = time.monotonic()

        if run_all:
            cmd = [cargo, "test", "--no-fail-fast"]
            try:
                result = await self.shell_run(cmd, timeout_seconds=timeout_seconds)
            except TimeoutError:
                return FindingBatch(
                    run_id=run_id,
                    depth="standard",
                    summary_line=f"cargo test timed out after {timeout_seconds:.0f}s",
                    duration_ms=(time.monotonic() - start) * 1000.0,
                    tests_failed=1,
                )
            output = result.stdout + "\n" + result.stderr
            batch = _parse_cargo_output(output, run_id=run_id, package="")
            return batch.model_copy(update={"duration_ms": (time.monotonic() - start) * 1000.0})

        # Per-crate runs -- aggregate results.
        all_findings: list[Finding] = []
        total_passed = 0
        total_failed = 0
        total_skipped = 0
        duration_ms = 0.0

        for crate_name in test_ids:
            cmd = [cargo, "test", "--package", crate_name, "--no-fail-fast"]
            crate_start = time.monotonic()
            try:
                result = await self.shell_run(cmd, timeout_seconds=timeout_seconds)
            except TimeoutError:
                total_failed += 1
                continue
            crate_duration = (time.monotonic() - crate_start) * 1000.0
            duration_ms += crate_duration
            output = result.stdout + "\n" + result.stderr
            crate_batch = _parse_cargo_output(output, run_id=run_id, package=crate_name)
            total_passed += crate_batch.tests_passed
            total_failed += crate_batch.tests_failed
            total_skipped += crate_batch.tests_skipped
            all_findings.extend(crate_batch.findings)

        summary = f"cargo: {total_passed} passed, {total_failed} failed" + (
            f", {total_skipped} skipped" if total_skipped else ""
        )
        return FindingBatch(
            run_id=run_id,
            depth="standard",
            findings=all_findings,
            duration_ms=duration_ms,
            summary_line=summary,
            tests_passed=total_passed,
            tests_failed=total_failed,
            tests_skipped=total_skipped,
        )


# --- Output parser -------------------------------------------------------

# Matches a test result line:  "test <path> ... ok|FAILED|ignored"
_TEST_LINE_RE = re.compile(
    r"^test\s+([\w:]+(?:::[\w:]+)*)\s+\.\.\.\s+(ok|FAILED|ignored)",
    re.MULTILINE,
)

# The summary line: "test result: FAILED. 1 passed; 2 failed; 0 ignored; ..."
_SUMMARY_RE = re.compile(
    r"^test result:\s+(?:ok|FAILED)\.\s+"
    r"(\d+)\s+passed;\s+(\d+)\s+failed;\s+(\d+)\s+ignored.*?finished in ([\d.]+)s",
    re.MULTILINE,
)

# Inside a failure block: "---- <name> stdout ----"
_FAILURE_BLOCK_RE = re.compile(
    r"----\s+([\w:]+(?:::[\w:]+)*)\s+stdout\s+----\s*\n(.*?)(?=\n----|\nfailures:|\ntest result:|$)",
    re.DOTALL,
)

# Panic location:  "panicked at 'msg', src/lib.rs:42:9"  (old format)
# or newer:  "panicked at src/lib.rs:42:9:\n  msg"
_PANIC_LOCATION_RE = re.compile(r"panicked at (?:'[^']*',\s*)?([^\n:]+\.rs):(\d+):\d+")


def _parse_cargo_output(output: str, *, run_id: str, package: str) -> FindingBatch:
    """Parse plain-text `cargo test` output into a FindingBatch.

    Handles both stable output lines (``test <name> ... FAILED``) and the
    ``failures:`` detail block.  Does not require JSON or nextest.
    """
    # Collect names of failed tests from the per-test result lines.
    failed_names: list[str] = []
    passed = 0
    skipped = 0

    for match in _TEST_LINE_RE.finditer(output):
        test_name = match.group(1)
        status = match.group(2)
        if status == "ok":
            passed += 1
        elif status == "FAILED":
            failed_names.append(test_name)
        elif status == "ignored":
            skipped += 1

    # Extract failure detail blocks for fix hints.
    failure_details: dict[str, str] = {}
    for block_match in _FAILURE_BLOCK_RE.finditer(output):
        name = block_match.group(1)
        body = block_match.group(2)
        failure_details[name] = body

    # Build findings for each failed test.
    findings: list[Finding] = []
    for test_name in failed_names:
        detail = failure_details.get(test_name, "")

        # Extract file + line from panic location.
        file_path: Path = Path("<unknown>")
        line_no = 0
        fix_hint: str | None = None
        loc_match = _PANIC_LOCATION_RE.search(detail)
        if loc_match:
            file_path = Path(loc_match.group(1))
            line_no = int(loc_match.group(2))
            # Use the line after "panicked at ..." as a hint (new format) or
            # the text inside quotes (old format).
            old_style = re.search(r"panicked at '([^']+)'", detail)
            if old_style:
                fix_hint = old_style.group(1).strip()
            else:
                # New format: "panicked at path:line:col:\n  message"
                new_style = re.search(r"panicked at [^\n]+:\n\s*(.+)", detail)
                if new_style:
                    fix_hint = new_style.group(1).strip()
        elif detail.strip():
            # No panic location but we have some detail -- use first line.
            first_line = detail.strip().split("\n")[0].strip()
            if first_line:
                fix_hint = first_line[:200]

        prefix = f"{package}::" if package else ""
        short_name = test_name.split("::")[-1]
        rule_id = f"rust-test-failure::{prefix}{short_name}"

        finding = Finding.create(
            kind=FindingKind.TEST_FAILURE,
            severity=Severity.HIGH,
            file=file_path,
            line=line_no,
            message=f"Test failed: {test_name}",
            run_id=run_id,
            rule_id=rule_id,
            fix_suggestion=fix_hint,
        )
        findings.append(finding)

    # Parse all summary lines and sum counts.
    # cargo emits one summary per test binary (crates + doc-tests).  We sum
    # them all; doc-test sections that have 0 tests contribute 0 to each
    # counter so including them is harmless.
    duration_ms = 0.0
    passed = 0
    skipped = 0
    for summary_match in _SUMMARY_RE.finditer(output):
        passed += int(summary_match.group(1))
        skipped += int(summary_match.group(3))
        duration_ms += float(summary_match.group(4)) * 1000.0

    actual_failed = len(findings)

    summary_line = f"cargo: {passed} passed, {actual_failed} failed"
    if skipped:
        summary_line += f", {skipped} skipped"
    if duration_ms:
        summary_line += f" in {duration_ms:.0f}ms"

    return FindingBatch(
        run_id=run_id,
        depth="standard",
        findings=findings,
        duration_ms=duration_ms,
        summary_line=summary_line,
        tests_passed=passed,
        tests_failed=actual_failed,
        tests_skipped=skipped,
    )
