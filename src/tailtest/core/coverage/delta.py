"""Delta coverage, pure functions (Phase 1 Task 1.8a).

Pure helpers for computing "coverage on the lines that this edit
actually changed" rather than "coverage on the whole file". Every
function in this module is side-effect free and synchronous so the
caller (hook, MCP tool, CLI) can compose them freely.

Design rules:

- Inputs are ``pathlib.Path`` keys mapped to sets of line numbers.
  Line numbers are 1-indexed to match editor conventions and the
  way ``coverage.py`` reports them.
- Outputs are frozen dataclasses with plain types so they serialize
  cleanly into ``FindingBatch`` metadata and JSON reports.
- The module accepts a variety of diff input formats: a raw unified
  diff string, an Edit tool payload (old_string + new_string), or a
  Write tool payload (content only, treated as all-new).

The delta computation runs in milliseconds even on large diffs;
there is no caching or memoization here.
"""

from __future__ import annotations

import difflib
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Default threshold below which a coverage_gap finding gets surfaced.
# Chosen to match the Google FSE 2019 recommendation for delta
# coverage on new code.
DEFAULT_DELTA_COVERAGE_THRESHOLD = 80.0


@dataclass(frozen=True)
class DeltaCoverageReport:
    """Result of computing delta coverage for a set of changed files.

    Attributes
    ----------
    delta_coverage_pct:
        The percentage (0.0 to 100.0) of added or modified lines
        that were covered by the test run. ``None`` when there are
        no added lines to measure against.
    total_new_lines:
        Count of unique added or modified lines across all files.
    covered_new_lines:
        Count of those lines that were hit by the test run.
    uncovered_new_lines:
        List of ``{"file": str, "line": int}`` entries for the
        added lines that were not covered. Callers serialize this
        into ``FindingBatch.uncovered_new_lines``.
    """

    delta_coverage_pct: float | None
    total_new_lines: int
    covered_new_lines: int
    uncovered_new_lines: list[dict[str, Any]] = field(default_factory=list)

    def to_finding_batch_fields(self) -> dict[str, Any]:
        """Return the subset of fields that FindingBatch consumes.

        Kept on the dataclass so callers do not need to know the
        exact FindingBatch field names to construct the update
        payload for ``FindingBatch.model_copy(update=...)``.
        """
        return {
            "delta_coverage_pct": self.delta_coverage_pct,
            "uncovered_new_lines": list(self.uncovered_new_lines),
        }


# --- Diff parsing ------------------------------------------------------


def parse_unified_diff(diff_text: str) -> dict[Path, set[int]]:
    """Parse a unified diff string into a map of file -> added line numbers.

    Only the ``+++ b/<path>`` headers and the ``@@`` hunk ranges are
    consulted; the actual ``+`` prefixed lines are counted within
    each hunk. Removed lines and context lines are skipped.

    Handles multi-file diffs, missing file headers (returns empty),
    and the ``/dev/null`` placeholder used for new files.
    """
    result: dict[Path, set[int]] = {}
    current_file: Path | None = None
    current_line: int = 0

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("+++ "):
            # `+++ b/path/to/file.py` or `+++ /dev/null`
            header = raw_line[4:].strip()
            if header == "/dev/null" or not header:
                current_file = None
                continue
            # Strip the conventional `a/` or `b/` prefix that git diff
            # emits for the "new side" of a hunk.
            if header.startswith(("b/", "a/")):
                header = header[2:]
            current_file = Path(header)
            if current_file not in result:
                result[current_file] = set()
            continue

        if current_file is None:
            continue

        if raw_line.startswith("@@"):
            # Parse `@@ -old_start,old_count +new_start,new_count @@`
            match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw_line)
            if not match:
                current_line = 0
                continue
            current_line = int(match.group(1))
            continue

        if current_line <= 0:
            continue

        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            result[current_file].add(current_line)
            current_line += 1
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            # Removed line; does not advance the new-file line counter.
            continue
        else:
            # Context line (starts with a space) or EOL marker.
            current_line += 1

    return result


def edit_added_lines(file_path: Path, old_string: str, new_string: str) -> set[int]:
    """Return the set of 1-indexed line numbers that differ in ``new_string``.

    Used when processing a Claude Code ``Edit`` tool payload: we know
    the old substring and the new substring but not the full file
    layout. We compute a line-level diff between the two and return
    the set of lines in ``new_string`` that were added or modified.
    The result is relative to the new_string block, NOT the full
    destination file. Callers that want destination-file line numbers
    must offset the result by the position of ``new_string`` inside
    the file.

    For Phase 1 this is conservative: we do not attempt to match
    ``new_string`` against the file content on disk. Callers that
    know the file layout can pass the whole new file contents as
    ``new_string`` and an empty ``old_string`` to get line numbers
    matching the file.
    """
    if old_string == new_string:
        return set()

    old_lines = old_string.splitlines()
    new_lines = new_string.splitlines()

    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    added: set[int] = set()
    for op, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if op in ("insert", "replace"):
            for j in range(j1, j2):
                added.add(j + 1)  # 1-indexed to match editor conventions
    return added


def write_added_lines(content: str) -> set[int]:
    """Return every line number (1-indexed) in a freshly-written file.

    Used when processing a Claude Code ``Write`` tool payload. All
    lines in a newly-written file are "added" by definition, so the
    result is simply ``{1, 2, ..., N}`` where N is the line count.
    """
    if not content:
        return set()
    line_count = content.count("\n")
    # If the file ends without a trailing newline, the last line
    # still counts.
    if not content.endswith("\n"):
        line_count += 1
    return set(range(1, line_count + 1))


# --- Coverage JSON parsing --------------------------------------------


def parse_coverage_json(json_path: Path) -> dict[Path, set[int]]:
    """Parse a ``coverage.py`` JSON report into a map of file -> covered lines.

    The ``coverage.py`` JSON format (version 7.x) has a top-level
    ``files`` object whose keys are absolute file paths and whose
    values contain ``executed_lines: list[int]`` (the lines that
    actually ran during the test session).

    Returns an empty dict on any read or parse failure, so callers
    can treat "no coverage data" and "broken coverage data" the same.
    """
    if not json_path.exists():
        return {}
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    files_obj = data.get("files")
    if not isinstance(files_obj, dict):
        return {}

    result: dict[Path, set[int]] = {}
    for file_str, file_data in files_obj.items():
        if not isinstance(file_data, dict):
            continue
        executed = file_data.get("executed_lines")
        if not isinstance(executed, list):
            continue
        line_set: set[int] = set()
        for value in executed:
            if isinstance(value, int) and value > 0:
                line_set.add(value)
        if line_set:
            result[Path(file_str)] = line_set
    return result


def resolve_coverage_bin(project_root: Path) -> str | None:
    """Find the ``coverage`` binary, target venv first, then PATH.

    Mirrors ``PythonRunner._resolve_pytest_path``. Returns the
    absolute path to the ``coverage`` executable or ``None`` when
    coverage.py is not available to the project.
    """
    candidates = [
        project_root / ".venv" / "bin" / "coverage",
        project_root / "venv" / "bin" / "coverage",
        project_root / ".venv" / "Scripts" / "coverage.exe",
        project_root / "venv" / "Scripts" / "coverage.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return shutil.which("coverage")


# --- Delta computation -------------------------------------------------


def compute_delta_coverage(
    added_lines_by_file: dict[Path, set[int]],
    covered_lines_by_file: dict[Path, set[int]],
) -> DeltaCoverageReport:
    """Intersect added lines with covered lines to produce a delta report.

    A line is "covered" if its file path matches (by string equality
    after resolving both sides) and its line number is present in
    the coverage set. Unmatched files are treated as entirely
    uncovered, which is the pessimistic choice that surfaces the
    maximum actionable signal to the user.

    Returns a report with ``delta_coverage_pct = None`` when the
    total added line count is zero, so callers can distinguish "no
    new code" (nothing to report) from "new code, nothing covered"
    (report 0%).
    """

    # Normalize file paths on both sides so "foo.py" and "./foo.py"
    # match. Use resolved POSIX strings as the canonical key.
    def _canonical(p: Path) -> str:
        try:
            return str(Path(p).resolve())
        except OSError:
            return str(p)

    # Build coverage lookup keyed by canonical path + by bare filename
    # so we can match either way.
    coverage_by_canonical: dict[str, set[int]] = {}
    coverage_by_name: dict[str, set[int]] = {}
    for cov_path, cov_lines in covered_lines_by_file.items():
        canonical = _canonical(cov_path)
        coverage_by_canonical[canonical] = set(cov_lines)
        coverage_by_name.setdefault(cov_path.name, set()).update(cov_lines)

    total_new = 0
    covered_new = 0
    uncovered: list[dict[str, Any]] = []

    for added_path, added_lines in added_lines_by_file.items():
        if not added_lines:
            continue
        total_new += len(added_lines)
        canonical = _canonical(added_path)
        hit_lines = coverage_by_canonical.get(canonical)
        if hit_lines is None:
            # Fall back to filename-only match. Useful when the diff
            # uses a relative path and coverage reports an absolute
            # one (or vice versa).
            hit_lines = coverage_by_name.get(added_path.name, set())

        for line in sorted(added_lines):
            if line in hit_lines:
                covered_new += 1
            else:
                uncovered.append({"file": str(added_path), "line": line})

    if total_new == 0:
        return DeltaCoverageReport(
            delta_coverage_pct=None,
            total_new_lines=0,
            covered_new_lines=0,
            uncovered_new_lines=[],
        )

    pct = round(100.0 * covered_new / total_new, 1)
    return DeltaCoverageReport(
        delta_coverage_pct=pct,
        total_new_lines=total_new,
        covered_new_lines=covered_new,
        uncovered_new_lines=uncovered,
    )
