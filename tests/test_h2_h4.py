"""Tests for H2 (last_failures_formatter) and H4 (output_compressor)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))

from lib.last_failures_formatter import compute_last_failures, format_last_failures
from lib.output_compressor import compress_output


# ---------------------------------------------------------------------------
# compute_last_failures
# ---------------------------------------------------------------------------


class TestComputeLastFailures:
    def test_empty_session_returns_empty(self):
        session = {"generated_tests": {}, "fix_attempts": {}, "deferred_failures": []}
        assert compute_last_failures(session) == []

    def test_passed_file_not_included(self):
        session = {
            "generated_tests": {"billing.py": "tests/test_billing.py"},
            "fix_attempts": {},
            "deferred_failures": [],
        }
        assert compute_last_failures(session) == []

    def test_fixed_file_included(self):
        session = {
            "generated_tests": {"billing.py": "tests/test_billing.py"},
            "fix_attempts": {"billing.py": 2},
            "deferred_failures": [],
        }
        result = compute_last_failures(session)
        assert len(result) == 1
        assert result[0]["file"] == "billing.py"
        assert result[0]["status"] == "fixed"
        assert result[0]["attempts"] == 2

    def test_deferred_file_included_as_unresolved(self):
        session = {
            "generated_tests": {"payment.py": "tests/test_payment.py"},
            "fix_attempts": {"payment.py": 3},
            "deferred_failures": [{"file": "payment.py"}],
        }
        result = compute_last_failures(session)
        assert len(result) == 1
        assert result[0]["status"] == "unresolved"

    def test_mixed_session(self):
        session = {
            "generated_tests": {
                "billing.py": "tests/test_billing.py",
                "auth.py": "tests/test_auth.py",
                "utils.py": "tests/test_utils.py",
            },
            "fix_attempts": {"billing.py": 1, "auth.py": 3},
            "deferred_failures": [{"file": "auth.py"}],
        }
        result = compute_last_failures(session)
        files = {r["file"]: r for r in result}
        assert "utils.py" not in files
        assert files["billing.py"]["status"] == "fixed"
        assert files["auth.py"]["status"] == "unresolved"

    def test_missing_keys_dont_crash(self):
        assert compute_last_failures({}) == []

    def test_malformed_deferred_failures_skipped(self):
        session = {
            "generated_tests": {"billing.py": "tests/test_billing.py"},
            "fix_attempts": {"billing.py": 2},
            "deferred_failures": ["not-a-dict", None, {"file": "billing.py"}],
        }
        result = compute_last_failures(session)
        assert result[0]["status"] == "unresolved"


# ---------------------------------------------------------------------------
# format_last_failures
# ---------------------------------------------------------------------------


class TestFormatLastFailures:
    def test_empty_returns_empty_string(self):
        assert format_last_failures([]) == ""

    def test_fixed_entry_formatted(self):
        entries = [{"file": "billing.py", "status": "fixed", "attempts": 2}]
        result = format_last_failures(entries)
        assert "billing.py" in result
        assert "fixed" in result
        assert "2" in result

    def test_unresolved_entry_formatted(self):
        entries = [{"file": "payment.py", "status": "unresolved", "attempts": 3}]
        result = format_last_failures(entries)
        assert "unresolved" in result

    def test_max_entries_cap(self):
        entries = [
            {"file": f"file{i}.py", "status": "fixed", "attempts": 1}
            for i in range(10)
        ]
        result = format_last_failures(entries, max_entries=3)
        assert "(+7 more)" in result
        assert "file0.py" in result
        assert "file3.py" not in result

    def test_exactly_max_entries_no_overflow_note(self):
        entries = [
            {"file": f"file{i}.py", "status": "fixed", "attempts": 1}
            for i in range(3)
        ]
        result = format_last_failures(entries, max_entries=3)
        assert "more" not in result

    def test_uses_basename_not_full_path(self):
        entries = [{"file": "src/billing/billing.py", "status": "fixed", "attempts": 1}]
        result = format_last_failures(entries)
        assert "billing.py" in result
        assert "src/billing/" not in result

    def test_result_ends_with_period(self):
        entries = [{"file": "x.py", "status": "fixed", "attempts": 1}]
        assert format_last_failures(entries).endswith(".")


# ---------------------------------------------------------------------------
# compress_output
# ---------------------------------------------------------------------------


class TestCompressOutput:
    def test_short_output_unchanged(self):
        text = "\n".join(f"line {i}" for i in range(10))
        assert compress_output(text) == text

    def test_long_output_compressed(self):
        lines = [f"line {i}" for i in range(200)]
        lines[5] = "FAILED test_billing.py::test_null_items"
        lines[6] = "AssertionError: expected 0 got 3"
        text = "\n".join(lines)
        result = compress_output(text, max_lines=50)
        assert "FAILED" in result
        assert "AssertionError" in result
        assert len(result.splitlines()) < 200

    def test_truncation_note_added(self):
        lines = [f"line {i}" for i in range(200)]
        lines[0] = "FAILED something"
        text = "\n".join(lines)
        result = compress_output(text, max_lines=50)
        assert "omitted" in result or "truncated" in result

    def test_exactly_max_lines_unchanged(self):
        text = "\n".join(f"line {i}" for i in range(50))
        assert compress_output(text, max_lines=50) == text

    def test_keeps_assertion_error_lines(self):
        lines = ["verbose line"] * 100
        lines[10] = "AssertionError: x != y"
        lines[20] = "Expected: 1"
        lines[21] = "Received: 0"
        text = "\n".join(lines)
        result = compress_output(text, max_lines=50)
        assert "AssertionError" in result
        assert "Expected" in result

    def test_no_keep_patterns_falls_back_to_head(self):
        lines = ["verbose line"] * 200
        text = "\n".join(lines)
        result = compress_output(text, max_lines=50)
        assert "truncated" in result
        assert len(result.splitlines()) <= 51  # 50 lines + truncation note
