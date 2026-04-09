"""Tests for BaselineManager (Phase 1 Task 1.7)."""

from __future__ import annotations

from pathlib import Path

from tailtest.core.baseline import BaselineEntry, BaselineFile, BaselineManager
from tailtest.core.findings.schema import Finding, FindingBatch, FindingKind, Severity


def _make_finding(
    kind: FindingKind,
    *,
    file: str = "src/foo.py",
    line: int = 10,
    message: str = "example",
    severity: Severity = Severity.MEDIUM,
) -> Finding:
    return Finding.create(
        kind=kind,
        severity=severity,
        file=file,
        line=line,
        message=message,
        run_id="run-test",
        rule_id=f"{kind.value}.rule",
    )


# --- File roundtrip ------------------------------------------------------


def test_baseline_file_roundtrip() -> None:
    """BaselineFile → YAML → BaselineFile preserves entries."""
    entry = BaselineEntry.from_finding(
        _make_finding(FindingKind.SAST, message="eval of user input")
    )
    original = BaselineFile(entries={entry.id: entry})

    yaml_text = original.to_yaml()
    restored = BaselineFile.from_yaml(yaml_text)

    assert restored.schema_version == 1
    assert restored.ids == original.ids
    assert restored.entries[entry.id].kind == entry.kind
    assert restored.entries[entry.id].file == entry.file


def test_baseline_file_from_empty_yaml() -> None:
    """Empty YAML produces an empty baseline."""
    empty = BaselineFile.from_yaml("")
    assert empty.ids == set()
    assert empty.schema_version == 1


def test_baseline_file_ids() -> None:
    entry1 = BaselineEntry.from_finding(_make_finding(FindingKind.SAST, line=1))
    entry2 = BaselineEntry.from_finding(_make_finding(FindingKind.SECRET, line=2))
    bf = BaselineFile(entries={entry1.id: entry1, entry2.id: entry2})
    assert bf.ids == {entry1.id, entry2.id}


# --- Manager: load/save/apply --------------------------------------------


def test_manager_load_missing_returns_empty(tmp_path: Path) -> None:
    """Loading a non-existent baseline returns an empty BaselineFile."""
    mgr = BaselineManager(tmp_path / ".tailtest")
    assert not mgr.exists()
    bf = mgr.load()
    assert bf.ids == set()


def test_manager_save_creates_file(tmp_path: Path) -> None:
    mgr = BaselineManager(tmp_path / ".tailtest")
    entry = BaselineEntry.from_finding(_make_finding(FindingKind.SAST))
    bf = BaselineFile(entries={entry.id: entry})

    mgr.save(bf)

    assert mgr.exists()
    content = mgr.baseline_path.read_text()
    assert "schema_version" in content
    assert entry.id in content


def test_manager_apply_to_flips_in_baseline(tmp_path: Path) -> None:
    """apply_to marks findings that are in the baseline as in_baseline=True."""
    mgr = BaselineManager(tmp_path / ".tailtest")

    # Pre-populate the baseline with a SAST finding
    f1 = _make_finding(FindingKind.SAST, line=10)
    f2 = _make_finding(FindingKind.SAST, line=20)
    entry = BaselineEntry.from_finding(f1)
    mgr.save(BaselineFile(entries={entry.id: entry}))

    # Apply to a batch containing f1 (baselined) and f2 (new)
    batch = FindingBatch(run_id="r", depth="standard", findings=[f1, f2])
    applied = mgr.apply_to(batch)

    # f1 should be in_baseline; f2 should not
    id_to_finding = {f.id: f for f in applied.findings}
    assert id_to_finding[f1.id].in_baseline is True
    assert id_to_finding[f2.id].in_baseline is False

    # new_findings should only return f2
    new = applied.new_findings
    assert len(new) == 1
    assert new[0].id == f2.id


# --- Kind-aware policy ---------------------------------------------------


def test_update_from_immediately_baselines_security_findings(tmp_path: Path) -> None:
    """Security findings (SAST, SECRET, SCA) baseline on first detection."""
    mgr = BaselineManager(tmp_path / ".tailtest")
    f1 = _make_finding(FindingKind.SAST, line=10, message="eval unsafe")
    f2 = _make_finding(FindingKind.SECRET, line=20, message="hardcoded key")
    f3 = _make_finding(FindingKind.SCA, line=30, message="CVE-2025-1234")

    batch = FindingBatch(run_id="r1", depth="standard", findings=[f1, f2, f3])
    result = mgr.update_from(batch)

    assert result.ids == {f1.id, f2.id, f3.id}


def test_update_from_immediately_baselines_lint_and_coverage(tmp_path: Path) -> None:
    mgr = BaselineManager(tmp_path / ".tailtest")
    f1 = _make_finding(FindingKind.LINT, line=10)
    f2 = _make_finding(FindingKind.COVERAGE_GAP, line=20)
    batch = FindingBatch(run_id="r1", depth="standard", findings=[f1, f2])
    result = mgr.update_from(batch)
    assert f1.id in result.ids
    assert f2.id in result.ids


def test_update_from_does_not_immediately_baseline_test_failures(tmp_path: Path) -> None:
    """Test failures need 3 consecutive runs before being baselined."""
    mgr = BaselineManager(tmp_path / ".tailtest")
    f = _make_finding(FindingKind.TEST_FAILURE, message="flaky")

    batch = FindingBatch(run_id="r1", depth="standard", findings=[f])
    result = mgr.update_from(batch)

    # First failure: not yet baselined
    assert f.id not in result.ids


def test_update_from_never_baselines_validator_or_redteam(tmp_path: Path) -> None:
    """Validator and red-team findings are managed by their own phases."""
    mgr = BaselineManager(tmp_path / ".tailtest")
    f1 = _make_finding(FindingKind.VALIDATOR, line=10)
    f2 = _make_finding(FindingKind.REDTEAM, line=20)
    batch = FindingBatch(run_id="r1", depth="paranoid", findings=[f1, f2])
    result = mgr.update_from(batch)
    assert f1.id not in result.ids
    assert f2.id not in result.ids


def test_update_from_decrements_streak_for_recovered_tests(tmp_path: Path) -> None:
    """A baselined flaky test that passes decrements its streak."""
    mgr = BaselineManager(tmp_path / ".tailtest")

    # Seed the baseline with a test failure entry at streak=2
    f = _make_finding(FindingKind.TEST_FAILURE, message="intermittent")
    entry = BaselineEntry.from_finding(f)
    entry.failure_streak = 2
    mgr.save(BaselineFile(entries={entry.id: entry}))

    # Run with no test failure in the batch (the test recovered)
    clean_batch = FindingBatch(run_id="r2", depth="standard", findings=[])
    result = mgr.update_from(clean_batch)

    # Streak decremented
    assert result.entries[entry.id].failure_streak == 1
