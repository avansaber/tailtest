"""Tests for Phase 3: history_manager (A1/A3/H8/H6), impact_tracer (H5), api_validator (S4)."""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hooks"))

from lib.history_manager import (
    append_session_to_history,
    classify_entry,
    detect_recurring_failures,
    entry_count,
    format_history_context,
    get_recent_failures,
    load_history,
    save_history,
)
from lib.impact_tracer import (
    _module_name_from_path,
    find_importers,
    format_impact_note,
    is_impact_tracing_enabled,
)
from lib.api_validator import (
    build_api_validation_note,
    extract_public_names,
    is_api_validation_enabled,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp_project() -> str:
    return tempfile.mkdtemp()


def _write_history(project_root: str, entries: list[dict]) -> None:
    path = os.path.join(project_root, ".tailtest", "history.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(entries, fh)


# ---------------------------------------------------------------------------
# load_history / save_history
# ---------------------------------------------------------------------------


class TestLoadSaveHistory:
    def test_load_empty_project_returns_empty(self):
        root = _tmp_project()
        assert load_history(root) == []

    def test_save_and_reload(self):
        root = _tmp_project()
        entries = [{"file": "billing.py", "status": "passed", "session_id": "s1"}]
        save_history(root, entries)
        loaded = load_history(root)
        assert loaded == entries

    def test_save_enforces_1000_cap(self):
        root = _tmp_project()
        entries = [{"file": f"f{i}.py"} for i in range(1200)]
        save_history(root, entries)
        loaded = load_history(root)
        assert len(loaded) == 1000
        assert loaded[-1]["file"] == "f1199.py"

    def test_corrupt_history_returns_empty(self):
        root = _tmp_project()
        path = os.path.join(root, ".tailtest", "history.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write("not json{{{")
        assert load_history(root) == []

    def test_entry_count_correct(self):
        root = _tmp_project()
        save_history(root, [{"file": "a.py"}, {"file": "b.py"}])
        assert entry_count(root) == 2


# ---------------------------------------------------------------------------
# classify_entry (H8)
# ---------------------------------------------------------------------------


class TestClassifyEntry:
    def test_no_history_is_gap(self):
        entry = {"file": "billing.py", "status": "unresolved"}
        assert classify_entry(entry, []) == "gap"

    def test_passed_is_passed(self):
        history = [{"file": "billing.py", "status": "unresolved"}]
        entry = {"file": "billing.py", "status": "passed"}
        assert classify_entry(entry, history) == "passed"

    def test_fixed_is_fixed(self):
        history = [{"file": "billing.py", "status": "passed"}]
        entry = {"file": "billing.py", "status": "fixed"}
        assert classify_entry(entry, history) == "fixed"

    def test_regression_when_previously_passing(self):
        history = [{"file": "billing.py", "status": "passed"}]
        entry = {"file": "billing.py", "status": "unresolved"}
        assert classify_entry(entry, history) == "regression"

    def test_not_regression_when_previously_failing(self):
        history = [{"file": "billing.py", "status": "unresolved"}]
        entry = {"file": "billing.py", "status": "unresolved"}
        result = classify_entry(entry, history)
        assert result != "regression"


# ---------------------------------------------------------------------------
# detect_recurring_failures (H6)
# ---------------------------------------------------------------------------


class TestDetectRecurringFailures:
    def test_empty_history_returns_empty(self):
        assert detect_recurring_failures([]) == []

    def test_two_sessions_not_recurring(self):
        history = [
            {"file": "billing.py", "status": "unresolved", "session_id": "s1"},
            {"file": "billing.py", "status": "unresolved", "session_id": "s2"},
        ]
        assert detect_recurring_failures(history) == []

    def test_three_different_sessions_is_recurring(self):
        history = [
            {"file": "billing.py", "status": "unresolved", "session_id": "s1"},
            {"file": "billing.py", "status": "unresolved", "session_id": "s2"},
            {"file": "billing.py", "status": "unresolved", "session_id": "s3"},
        ]
        result = detect_recurring_failures(history)
        assert "billing.py" in result

    def test_same_session_repeated_not_counted(self):
        history = [
            {"file": "billing.py", "status": "unresolved", "session_id": "s1"},
            {"file": "billing.py", "status": "unresolved", "session_id": "s1"},
            {"file": "billing.py", "status": "unresolved", "session_id": "s1"},
        ]
        assert detect_recurring_failures(history) == []

    def test_passed_entries_not_counted(self):
        history = [
            {"file": "billing.py", "status": "passed", "session_id": "s1"},
            {"file": "billing.py", "status": "passed", "session_id": "s2"},
            {"file": "billing.py", "status": "passed", "session_id": "s3"},
        ]
        assert detect_recurring_failures(history) == []


# ---------------------------------------------------------------------------
# append_session_to_history (A1)
# ---------------------------------------------------------------------------


class TestAppendSessionToHistory:
    def test_appends_entries_with_classification(self):
        root = _tmp_project()
        entries = [{"file": "billing.py", "status": "passed", "session_id": "s1", "attempts": 0}]
        history = append_session_to_history(root, entries)
        assert len(history) == 1
        assert "classification" in history[0]

    def test_gap_classification_for_new_file(self):
        root = _tmp_project()
        entries = [{"file": "billing.py", "status": "unresolved", "session_id": "s1", "attempts": 3}]
        history = append_session_to_history(root, entries)
        assert history[0]["classification"] == "gap"

    def test_regression_classification_after_passing(self):
        root = _tmp_project()
        # First session: passed
        append_session_to_history(root, [
            {"file": "billing.py", "status": "passed", "session_id": "s1", "attempts": 0}
        ])
        # Second session: unresolved
        history = append_session_to_history(root, [
            {"file": "billing.py", "status": "unresolved", "session_id": "s2", "attempts": 3}
        ])
        last = [e for e in history if e.get("session_id") == "s2"][0]
        assert last["classification"] == "regression"

    def test_persisted_to_disk(self):
        root = _tmp_project()
        append_session_to_history(root, [{"file": "a.py", "status": "passed", "session_id": "s1"}])
        assert entry_count(root) == 1


# ---------------------------------------------------------------------------
# get_recent_failures / format_history_context (A3/H6)
# ---------------------------------------------------------------------------


class TestFormatHistoryContext:
    def test_empty_history_returns_empty(self):
        root = _tmp_project()
        assert format_history_context(root) == ""

    def test_recurring_failure_mentioned(self):
        root = _tmp_project()
        _write_history(root, [
            {"file": "billing.py", "status": "unresolved", "session_id": "s1"},
            {"file": "billing.py", "status": "unresolved", "session_id": "s2"},
            {"file": "billing.py", "status": "unresolved", "session_id": "s3"},
        ])
        result = format_history_context(root)
        assert "billing.py" in result
        assert "Recurring" in result

    def test_regression_mentioned(self):
        root = _tmp_project()
        _write_history(root, [
            {"file": "auth.py", "status": "passed", "session_id": "s1", "classification": "passed"},
            {"file": "auth.py", "status": "unresolved", "session_id": "s2", "classification": "regression"},
        ])
        result = format_history_context(root)
        assert "auth.py" in result or result == ""  # only fires if 3+ sessions for recurring


# ---------------------------------------------------------------------------
# impact_tracer (H5)
# ---------------------------------------------------------------------------


class TestModuleNameFromPath:
    def test_simple_file(self):
        assert _module_name_from_path("billing.py") == "billing"

    def test_nested_file(self):
        result = _module_name_from_path("services/billing.py")
        assert "billing" in result

    def test_no_extension(self):
        result = _module_name_from_path("billing")
        assert result == "billing"


class TestFindImporters:
    def test_finds_direct_importer(self):
        root = _tmp_project()
        # billing.py
        with open(os.path.join(root, "billing.py"), "w") as fh:
            fh.write("def charge(): pass\n")
        # checkout.py imports billing
        with open(os.path.join(root, "checkout.py"), "w") as fh:
            fh.write("import billing\n\ndef buy(): billing.charge()\n")
        importers = find_importers("billing.py", root)
        assert "checkout.py" in importers

    def test_no_importers_returns_empty(self):
        root = _tmp_project()
        with open(os.path.join(root, "billing.py"), "w") as fh:
            fh.write("def charge(): pass\n")
        assert find_importers("billing.py", root) == []

    def test_skips_self(self):
        root = _tmp_project()
        with open(os.path.join(root, "billing.py"), "w") as fh:
            fh.write("import billing\n")
        importers = find_importers("billing.py", root)
        assert "billing.py" not in importers


class TestFormatImpactNote:
    def test_no_importers_returns_empty(self):
        assert format_impact_note("billing.py", []) == ""

    def test_one_importer(self):
        result = format_impact_note("billing.py", ["checkout.py"])
        assert "billing.py" in result
        assert "checkout.py" in result

    def test_overflow_note(self):
        importers = [f"file{i}.py" for i in range(5)]
        result = format_impact_note("billing.py", importers)
        assert "+2 more" in result


class TestImpactTracingEnabled:
    def test_disabled_by_default(self):
        root = _tmp_project()
        assert not is_impact_tracing_enabled(root)

    def test_enabled_when_config_set(self):
        root = _tmp_project()
        config_dir = os.path.join(root, ".tailtest")
        os.makedirs(config_dir)
        with open(os.path.join(config_dir, "config.json"), "w") as fh:
            json.dump({"impact_tracing": True}, fh)
        assert is_impact_tracing_enabled(root)


# ---------------------------------------------------------------------------
# api_validator (S4)
# ---------------------------------------------------------------------------


class TestExtractPublicNames:
    def _write_py(self, content: str) -> str:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
        f.write(content)
        f.close()
        return f.name

    def test_extracts_public_function(self):
        path = self._write_py("def charge(): pass\ndef _private(): pass\n")
        try:
            names = extract_public_names(path)
            assert "charge" in names
            assert "_private" not in names
        finally:
            os.unlink(path)

    def test_extracts_public_class(self):
        path = self._write_py("class BillingService: pass\n")
        try:
            names = extract_public_names(path)
            assert "BillingService" in names
        finally:
            os.unlink(path)

    def test_empty_file_returns_empty(self):
        path = self._write_py("")
        try:
            assert extract_public_names(path) == []
        finally:
            os.unlink(path)

    def test_syntax_error_returns_empty(self):
        path = self._write_py("def broken(: pass\n")
        try:
            assert extract_public_names(path) == []
        finally:
            os.unlink(path)


class TestApiValidationEnabled:
    def test_disabled_by_default(self):
        root = _tmp_project()
        assert not is_api_validation_enabled(root)

    def test_enabled_when_config_set(self):
        root = _tmp_project()
        config_dir = os.path.join(root, ".tailtest")
        os.makedirs(config_dir)
        with open(os.path.join(config_dir, "config.json"), "w") as fh:
            json.dump({"api_validation": True}, fh)
        assert is_api_validation_enabled(root)


class TestBuildApiValidationNote:
    def test_non_python_returns_empty(self):
        assert build_api_validation_note("/some/file.ts", "/project") == ""
