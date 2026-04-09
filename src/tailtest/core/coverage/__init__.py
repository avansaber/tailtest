"""tailtest.core.coverage, delta coverage tracking (Phase 1 Task 1.8a).

Delta coverage is what tailtest reports to answer the question
"is your NEW code tested?" without the noise of "your legacy code
is 40% covered". Google's FSE 2019 paper showed this is the
practically useful signal: absolute coverage penalizes historical
debt, delta coverage focuses review on new work.

The module has three layers:

1. Diff parsing (pure functions). Convert a Claude Code tool input
   or a unified diff into a map of file -> set of added / modified
   line numbers.
2. Coverage parsing (pure functions). Read a `coverage.py` JSON
   report and return a map of file -> set of covered line numbers.
3. Delta computation (pure function). Intersect added lines with
   covered lines to produce a DeltaCoverageReport.

Runner integration lives in `tailtest.core.runner.python.PythonRunner`
via a `collect_coverage` parameter that wraps pytest with `coverage
run` when the target project has coverage.py installed. Hook
integration lives in `tailtest.hook.post_tool_use.run` which reads
the tool input, extracts added lines, and passes them through.
"""

from tailtest.core.coverage.delta import (
    DeltaCoverageReport,
    compute_delta_coverage,
    edit_added_lines,
    parse_coverage_json,
    parse_unified_diff,
    resolve_coverage_bin,
    write_added_lines,
)

__all__ = [
    "DeltaCoverageReport",
    "compute_delta_coverage",
    "edit_added_lines",
    "parse_coverage_json",
    "parse_unified_diff",
    "resolve_coverage_bin",
    "write_added_lines",
]
