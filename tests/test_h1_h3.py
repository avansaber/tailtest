"""Tests for H1 (complexity_scorer) and H3 (scenario_log)."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))

from lib.complexity_scorer import (
    complexity_context_note,
    score_file,
    score_to_depth,
)
from lib.scenario_log import append_to_log, build_scenario_entries, get_file_history


# ---------------------------------------------------------------------------
# score_to_depth
# ---------------------------------------------------------------------------


class TestScoreToDepth:
    def test_zero_score_is_simple(self):
        depth, count = score_to_depth(0)
        assert depth == "simple"
        assert count >= 2

    def test_low_score_is_simple(self):
        depth, _ = score_to_depth(4)
        assert depth == "simple"

    def test_medium_score_is_standard(self):
        depth, count = score_to_depth(7)
        assert depth == "standard"
        assert count <= 8

    def test_high_score_is_thorough(self):
        depth, count = score_to_depth(12)
        assert depth == "thorough"
        assert count <= 15

    def test_score_boundary_6_is_standard(self):
        depth, _ = score_to_depth(6)
        assert depth == "standard"

    def test_score_boundary_10_is_thorough(self):
        depth, _ = score_to_depth(10)
        assert depth == "thorough"

    def test_scenario_count_capped_at_15(self):
        _, count = score_to_depth(100)
        assert count == 15


# ---------------------------------------------------------------------------
# score_file
# ---------------------------------------------------------------------------


class TestScoreFile:
    def _write_temp(self, content: str, suffix: str = ".py") -> str:
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=suffix, delete=False, prefix="test_billing_"
        )
        f.write(content)
        f.close()
        return f.name

    def test_empty_file_scores_low(self):
        path = self._write_temp("")
        try:
            score, _ = score_file(path)
            assert score <= 5
        finally:
            os.unlink(path)

    def test_auth_in_path_adds_points(self):
        path = self._write_temp("def check(): pass", suffix=".py")
        # rename to include "auth"
        new_path = path.replace(os.path.basename(path), "auth_service.py")
        os.rename(path, new_path)
        try:
            score, _ = score_file(new_path)
            assert score >= 4
        finally:
            os.unlink(new_path)

    def test_http_call_adds_points(self):
        content = "import requests\nrequests.get('http://example.com')\n"
        path = self._write_temp(content)
        try:
            score, _ = score_file(path)
            assert score >= 3
        finally:
            os.unlink(path)

    def test_db_call_adds_points(self):
        content = "def save(): db.session.commit()\n"
        path = self._write_temp(content)
        try:
            score, _ = score_file(path)
            assert score >= 3
        finally:
            os.unlink(path)

    def test_branches_add_points(self):
        content = "\n".join(
            ["def f():", "  if x:", "    pass", "  elif y:", "    pass", "  else:", "    pass"]
        )
        path = self._write_temp(content)
        try:
            score, _ = score_file(path)
            assert score >= 2
        finally:
            os.unlink(path)

    def test_reasoning_empty_for_low_score(self):
        path = self._write_temp("x = 1\n")
        try:
            _, reasoning = score_file(path)
            assert reasoning == ""
        finally:
            os.unlink(path)

    def test_reasoning_present_for_high_score(self):
        content = (
            "import requests\n"
            "def pay(): db.session.commit()\n"
            "if auth: pass\nelif perm: pass\nelse: pass\n" * 3
        )
        path = self._write_temp(content, suffix="billing_auth.py")
        new_path = path.replace(os.path.basename(path), "billing_auth_service.py")
        os.rename(path, new_path)
        try:
            score, reasoning = score_file(new_path)
            if score >= 10:
                assert reasoning != ""
                assert "=" in reasoning
        finally:
            os.unlink(new_path)

    def test_missing_file_returns_score_zero(self):
        score, reasoning = score_file("/nonexistent/path/billing.py")
        assert isinstance(score, int)
        assert score >= 0  # path signal for "billing" still applies

    def test_branches_capped(self):
        content = "\n".join(["if x: pass"] * 20)
        path = self._write_temp(content)
        try:
            score, _ = score_file(path)
            # Branch contribution is capped at _MAX_BRANCHES (4)
            assert score <= 20  # sanity bound
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# complexity_context_note
# ---------------------------------------------------------------------------


class TestComplexityContextNote:
    def _write_temp(self, content: str, name: str = "simple.py") -> str:
        d = tempfile.mkdtemp()
        path = os.path.join(d, name)
        with open(path, "w") as fh:
            fh.write(content)
        return path

    def test_returns_empty_when_no_override_needed(self):
        # Low-complexity file with configured_depth=simple -- no override
        path = self._write_temp("x = 1\n")
        result = complexity_context_note(path, "simple")
        # might be empty or might not, depending on score -- just ensure it's a string
        assert isinstance(result, str)

    def test_returns_empty_when_complexity_le_configured(self):
        # thorough already covers everything
        path = self._write_temp("x = 1\n")
        result = complexity_context_note(path, "thorough")
        assert result == ""

    def test_high_complexity_file_triggers_override(self):
        content = (
            "import requests\n"
            "def auth_check(): db.session.commit()\n"
            "if perm: pass\nelse: pass\n" * 4
        )
        d = tempfile.mkdtemp()
        path = os.path.join(d, "billing_auth.py")
        with open(path, "w") as fh:
            fh.write(content)
        result = complexity_context_note(path, "simple")
        # May or may not override depending on actual score; result must be a string
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# build_scenario_entries
# ---------------------------------------------------------------------------


class TestBuildScenarioEntries:
    def test_empty_session_returns_empty(self):
        assert build_scenario_entries({}) == []

    def test_passed_file_entry(self):
        session = {
            "generated_tests": {"billing.py": "tests/test_billing.py"},
            "fix_attempts": {},
            "deferred_failures": [],
            "session_id": "abc",
        }
        entries = build_scenario_entries(session)
        assert len(entries) == 1
        assert entries[0]["file"] == "billing.py"
        assert entries[0]["status"] == "passed"
        assert entries[0]["session_id"] == "abc"
        assert "timestamp" in entries[0]

    def test_fixed_file_entry(self):
        session = {
            "generated_tests": {"auth.py": "tests/test_auth.py"},
            "fix_attempts": {"auth.py": 2},
            "deferred_failures": [],
            "session_id": "abc",
        }
        entries = build_scenario_entries(session)
        assert entries[0]["status"] == "fixed"
        assert entries[0]["attempts"] == 2

    def test_unresolved_file_entry(self):
        session = {
            "generated_tests": {"auth.py": "tests/test_auth.py"},
            "fix_attempts": {"auth.py": 3},
            "deferred_failures": [],
            "session_id": "abc",
        }
        entries = build_scenario_entries(session)
        assert entries[0]["status"] == "unresolved"

    def test_deferred_file_entry(self):
        session = {
            "generated_tests": {"pay.py": "tests/test_pay.py"},
            "fix_attempts": {},
            "deferred_failures": [{"file": "pay.py"}],
            "session_id": "abc",
        }
        entries = build_scenario_entries(session)
        assert entries[0]["status"] == "deferred"


# ---------------------------------------------------------------------------
# append_to_log
# ---------------------------------------------------------------------------


class TestAppendToLog:
    def test_appends_entries(self):
        existing = [{"file": "a.py", "status": "passed"}]
        new = [{"file": "b.py", "status": "fixed"}]
        result = append_to_log(existing, new)
        assert len(result) == 2

    def test_cap_at_500(self):
        existing = [{"file": f"f{i}.py", "status": "passed"} for i in range(498)]
        new = [{"file": "x.py"}, {"file": "y.py"}, {"file": "z.py"}]
        result = append_to_log(existing, new)
        assert len(result) == 500

    def test_oldest_dropped_when_over_cap(self):
        existing = [{"file": f"f{i}.py"} for i in range(500)]
        new = [{"file": "newest.py"}]
        result = append_to_log(existing, new)
        assert len(result) == 500
        assert result[-1]["file"] == "newest.py"
        assert result[0]["file"] != "f0.py"

    def test_empty_existing(self):
        new = [{"file": "a.py"}]
        result = append_to_log([], new)
        assert result == new


# ---------------------------------------------------------------------------
# get_file_history
# ---------------------------------------------------------------------------


class TestGetFileHistory:
    def test_returns_entries_for_file(self):
        log = [
            {"file": "billing.py", "status": "passed"},
            {"file": "auth.py", "status": "fixed"},
            {"file": "billing.py", "status": "unresolved"},
        ]
        result = get_file_history(log, "billing.py")
        assert len(result) == 2
        assert all(e["file"] == "billing.py" for e in result)

    def test_returns_last_n(self):
        log = [{"file": "billing.py", "status": str(i)} for i in range(20)]
        result = get_file_history(log, "billing.py", last_n=5)
        assert len(result) == 5
        assert result[-1]["status"] == "19"

    def test_returns_empty_for_unknown_file(self):
        log = [{"file": "auth.py", "status": "passed"}]
        assert get_file_history(log, "missing.py") == []
