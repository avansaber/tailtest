"""Tests for delta coverage (Phase 1 Task 1.8a).

Covers the pure functions in ``tailtest.core.coverage.delta``:
parse_unified_diff, edit_added_lines, write_added_lines,
parse_coverage_json, compute_delta_coverage, plus the
DeltaCoverageReport dataclass surface.
"""

from __future__ import annotations

import json
from pathlib import Path

from tailtest.core.coverage.delta import (
    DEFAULT_DELTA_COVERAGE_THRESHOLD,
    DeltaCoverageReport,
    compute_delta_coverage,
    edit_added_lines,
    parse_coverage_json,
    parse_unified_diff,
    resolve_coverage_bin,
    write_added_lines,
)

# --- parse_unified_diff ------------------------------------------------


def test_parse_unified_diff_single_file_single_hunk() -> None:
    diff = """\
diff --git a/src/foo.py b/src/foo.py
--- a/src/foo.py
+++ b/src/foo.py
@@ -1,3 +1,4 @@
 def add(a, b):
     return a + b
+
+print("new")
"""
    out = parse_unified_diff(diff)
    assert Path("src/foo.py") in out
    # Lines 3 and 4 are the newly added blank line and the print().
    assert 3 in out[Path("src/foo.py")]
    assert 4 in out[Path("src/foo.py")]


def test_parse_unified_diff_multi_file() -> None:
    diff = """\
--- a/a.py
+++ b/a.py
@@ -1 +1,2 @@
 x = 1
+y = 2
--- a/b.py
+++ b/b.py
@@ -1 +1,2 @@
 z = 3
+w = 4
"""
    out = parse_unified_diff(diff)
    assert Path("a.py") in out
    assert Path("b.py") in out
    assert 2 in out[Path("a.py")]
    assert 2 in out[Path("b.py")]


def test_parse_unified_diff_ignores_removed_lines() -> None:
    diff = """\
--- a/c.py
+++ b/c.py
@@ -1,3 +1,2 @@
 kept
-removed
 still_here
"""
    out = parse_unified_diff(diff)
    # Only the context lines are present; no lines were added.
    assert out.get(Path("c.py"), set()) == set()


def test_parse_unified_diff_handles_dev_null_destination() -> None:
    diff = """\
--- a/gone.py
+++ /dev/null
@@ -1 +0,0 @@
-removed line
"""
    out = parse_unified_diff(diff)
    # /dev/null means the file was deleted; no added lines attributed.
    assert out == {}


def test_parse_unified_diff_strips_git_prefixes() -> None:
    diff = """\
--- a/dir/x.py
+++ b/dir/x.py
@@ -1 +1,2 @@
 kept
+added
"""
    out = parse_unified_diff(diff)
    # The Path key is stripped of the leading `b/`.
    assert Path("dir/x.py") in out
    assert 2 in out[Path("dir/x.py")]


def test_parse_unified_diff_tolerates_malformed_hunk_headers() -> None:
    diff = """\
--- a/bad.py
+++ b/bad.py
@@ not-a-valid-hunk-header @@
+this-line-is-lost
"""
    out = parse_unified_diff(diff)
    # Malformed hunk header resets current_line to 0, so the +line is ignored.
    assert out.get(Path("bad.py"), set()) == set()


# --- edit_added_lines --------------------------------------------------


def test_edit_added_lines_identical_returns_empty() -> None:
    assert edit_added_lines(Path("x.py"), "a\nb\n", "a\nb\n") == set()


def test_edit_added_lines_pure_insertion() -> None:
    result = edit_added_lines(Path("x.py"), "a\nb", "a\nNEW\nb")
    assert 2 in result  # "NEW" is line 2 of new_string (1-indexed)


def test_edit_added_lines_pure_replace() -> None:
    result = edit_added_lines(Path("x.py"), "a\nb\nc", "a\nMODIFIED\nc")
    assert 2 in result  # "MODIFIED" replaces "b" at position 2


def test_edit_added_lines_append() -> None:
    result = edit_added_lines(Path("x.py"), "a", "a\nb\nc")
    assert 2 in result
    assert 3 in result


# --- write_added_lines -------------------------------------------------


def test_write_added_lines_empty_content() -> None:
    assert write_added_lines("") == set()


def test_write_added_lines_counts_every_line() -> None:
    assert write_added_lines("a\nb\nc\n") == {1, 2, 3}


def test_write_added_lines_without_trailing_newline() -> None:
    assert write_added_lines("a\nb\nc") == {1, 2, 3}


def test_write_added_lines_single_line_no_newline() -> None:
    assert write_added_lines("just one line") == {1}


# --- parse_coverage_json -----------------------------------------------


def test_parse_coverage_json_happy_path(tmp_path: Path) -> None:
    payload = {
        "files": {
            "/tmp/project/src/foo.py": {
                "executed_lines": [1, 2, 5, 7],
                "missing_lines": [3, 4],
                "summary": {},
            },
            "/tmp/project/src/bar.py": {
                "executed_lines": [10, 11],
                "summary": {},
            },
        }
    }
    json_path = tmp_path / "coverage.json"
    json_path.write_text(json.dumps(payload), encoding="utf-8")

    out = parse_coverage_json(json_path)
    assert Path("/tmp/project/src/foo.py") in out
    assert out[Path("/tmp/project/src/foo.py")] == {1, 2, 5, 7}
    assert out[Path("/tmp/project/src/bar.py")] == {10, 11}


def test_parse_coverage_json_returns_empty_for_missing_file(tmp_path: Path) -> None:
    assert parse_coverage_json(tmp_path / "nowhere.json") == {}


def test_parse_coverage_json_returns_empty_for_malformed_json(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json {{", encoding="utf-8")
    assert parse_coverage_json(bad) == {}


def test_parse_coverage_json_returns_empty_when_files_field_missing(tmp_path: Path) -> None:
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"summary": {}}), encoding="utf-8")
    assert parse_coverage_json(path) == {}


# --- compute_delta_coverage -------------------------------------------


def test_compute_delta_coverage_full_coverage() -> None:
    added = {Path("foo.py"): {1, 2, 3}}
    covered = {Path("foo.py"): {1, 2, 3, 4, 5}}
    report = compute_delta_coverage(added, covered)
    assert report.delta_coverage_pct == 100.0
    assert report.total_new_lines == 3
    assert report.covered_new_lines == 3
    assert report.uncovered_new_lines == []


def test_compute_delta_coverage_partial() -> None:
    added = {Path("foo.py"): {1, 2, 3, 4, 5}}
    covered = {Path("foo.py"): {1, 3, 5}}
    report = compute_delta_coverage(added, covered)
    assert report.delta_coverage_pct == 60.0
    assert report.total_new_lines == 5
    assert report.covered_new_lines == 3
    assert len(report.uncovered_new_lines) == 2
    uncovered_lines = {entry["line"] for entry in report.uncovered_new_lines}
    assert uncovered_lines == {2, 4}


def test_compute_delta_coverage_zero_when_no_coverage() -> None:
    added = {Path("foo.py"): {1, 2, 3}}
    covered: dict[Path, set[int]] = {}
    report = compute_delta_coverage(added, covered)
    assert report.delta_coverage_pct == 0.0
    assert report.uncovered_new_lines == [
        {"file": "foo.py", "line": 1},
        {"file": "foo.py", "line": 2},
        {"file": "foo.py", "line": 3},
    ]


def test_compute_delta_coverage_none_when_no_new_lines() -> None:
    """No added lines means the percentage is None, not 100."""
    report = compute_delta_coverage({}, {})
    assert report.delta_coverage_pct is None
    assert report.total_new_lines == 0


def test_compute_delta_coverage_matches_by_filename_when_paths_differ() -> None:
    """If the diff uses a relative path but coverage uses absolute, fall back to basename."""
    added = {Path("src/foo.py"): {1, 2}}
    covered = {Path("/tmp/project/src/foo.py"): {1, 2}}
    report = compute_delta_coverage(added, covered)
    # Basename fallback matches both files as foo.py.
    assert report.delta_coverage_pct == 100.0


def test_compute_delta_coverage_to_finding_batch_fields() -> None:
    report = DeltaCoverageReport(
        delta_coverage_pct=72.5,
        total_new_lines=10,
        covered_new_lines=7,
        uncovered_new_lines=[{"file": "a.py", "line": 5}],
    )
    out = report.to_finding_batch_fields()
    assert out["delta_coverage_pct"] == 72.5
    assert out["uncovered_new_lines"] == [{"file": "a.py", "line": 5}]


def test_default_threshold_is_80() -> None:
    """Phase 1 wires the threshold from audit gap #12 at 80%."""
    assert DEFAULT_DELTA_COVERAGE_THRESHOLD == 80.0


# --- resolve_coverage_bin ----------------------------------------------


def test_resolve_coverage_bin_prefers_project_venv(tmp_path: Path) -> None:
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    fake_bin = bin_dir / "coverage"
    fake_bin.write_text("#!/bin/sh\necho fake\n")
    fake_bin.chmod(0o755)
    assert resolve_coverage_bin(tmp_path) == str(fake_bin)


def test_resolve_coverage_bin_falls_back_to_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/local/bin/coverage")
    assert resolve_coverage_bin(tmp_path) == "/usr/local/bin/coverage"


def test_resolve_coverage_bin_returns_none_when_nothing_found(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    assert resolve_coverage_bin(tmp_path) is None
