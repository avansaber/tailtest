"""Tests for Recommendation schema (Phase 3 Task 3.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tailtest.core.recommendations import (
    Recommendation,
    RecommendationKind,
    RecommendationPriority,
)

# --- Helpers -----------------------------------------------------------------


def _make_rec(**kwargs) -> Recommendation:
    defaults: dict = dict(
        kind=RecommendationKind.add_test,
        priority=RecommendationPriority.medium,
        title="Add unit tests",
        why="Coverage is below 60%.",
        next_step="Run pytest and add tests for uncovered paths.",
    )
    defaults.update(kwargs)
    return Recommendation(**defaults)


# --- ID auto-generation ------------------------------------------------------


def test_id_is_auto_generated_when_empty() -> None:
    """If id is not provided, model_validator sets it from (kind, title, applies_to)."""
    rec = _make_rec()
    assert rec.id != ""
    assert len(rec.id) == 16
    assert all(c in "0123456789abcdef" for c in rec.id)


def test_id_is_stable_for_same_inputs() -> None:
    """Same kind, title, and applies_to always produce the same id."""
    rec_a = _make_rec()
    rec_b = _make_rec()
    assert rec_a.id == rec_b.id


def test_id_differs_for_different_kind() -> None:
    """Changing kind must change the id."""
    rec_test = _make_rec(kind=RecommendationKind.add_test)
    rec_tool = _make_rec(kind=RecommendationKind.install_tool)
    assert rec_test.id != rec_tool.id


def test_id_differs_for_different_title() -> None:
    """Changing title must change the id."""
    rec_a = _make_rec(title="Add unit tests")
    rec_b = _make_rec(title="Enable coverage reporting")
    assert rec_a.id != rec_b.id


def test_id_differs_for_different_applies_to() -> None:
    """Changing applies_to must change the id."""
    rec_a = _make_rec(applies_to="src/agent.py")
    rec_b = _make_rec(applies_to="src/tools.py")
    assert rec_a.id != rec_b.id


def test_explicit_id_is_preserved() -> None:
    """An explicitly provided id must not be overwritten by the validator."""
    rec = _make_rec(id="abcd1234abcd1234")
    assert rec.id == "abcd1234abcd1234"


# --- is_dismissed property ---------------------------------------------------


def test_is_dismissed_false_when_dismissed_until_is_none() -> None:
    """A recommendation with no dismissed_until is never dismissed."""
    rec = _make_rec()
    assert rec.dismissed_until is None
    assert rec.is_dismissed is False


def test_is_dismissed_false_when_dismissed_until_is_in_the_past() -> None:
    """dismissed_until in the past means the dismissal has expired."""
    past = datetime.now(tz=UTC) - timedelta(days=1)
    rec = _make_rec(dismissed_until=past)
    assert rec.is_dismissed is False


def test_is_dismissed_true_when_dismissed_until_is_in_the_future() -> None:
    """dismissed_until in the future means the rec is still suppressed."""
    future = datetime.now(tz=UTC) + timedelta(days=7)
    rec = _make_rec(dismissed_until=future)
    assert rec.is_dismissed is True


# --- dismiss() method --------------------------------------------------------


def test_dismiss_returns_new_recommendation_with_dismissed_until_set() -> None:
    """dismiss() must return a new object; the original is unchanged (immutable semantics)."""
    rec = _make_rec()
    future = datetime.now(tz=UTC) + timedelta(days=30)
    dismissed = rec.dismiss(future)

    assert dismissed.dismissed_until == future
    assert dismissed.is_dismissed is True
    # Original is untouched.
    assert rec.dismissed_until is None


def test_dismiss_preserves_all_other_fields() -> None:
    """dismiss() must not mutate kind, title, why, next_step, or source."""
    rec = _make_rec(source="llm", applies_to="src/agent.py")
    future = datetime.now(tz=UTC) + timedelta(days=1)
    dismissed = rec.dismiss(future)

    assert dismissed.kind == rec.kind
    assert dismissed.priority == rec.priority
    assert dismissed.title == rec.title
    assert dismissed.why == rec.why
    assert dismissed.next_step == rec.next_step
    assert dismissed.source == rec.source
    assert dismissed.applies_to == rec.applies_to
    assert dismissed.id == rec.id


# --- Schema round-trip -------------------------------------------------------


def test_schema_roundtrip_model_dump_and_validate() -> None:
    """model_dump() -> model_validate() produces an identical Recommendation."""
    future = datetime.now(tz=UTC) + timedelta(days=5)
    rec = _make_rec(
        applies_to="src/agent.py",
        source="llm",
        dismissed_until=future,
    )
    dumped = rec.model_dump()
    restored = Recommendation.model_validate(dumped)
    assert restored == rec


def test_schema_roundtrip_json() -> None:
    """model_dump_json() -> model_validate_json() round-trip preserves all fields."""
    rec = _make_rec(source="rules", applies_to="")
    json_str = rec.model_dump_json()
    restored = Recommendation.model_validate_json(json_str)
    assert restored.id == rec.id
    assert restored.kind == rec.kind
    assert restored.priority == rec.priority
    assert restored.title == rec.title
    assert restored.why == rec.why
    assert restored.next_step == rec.next_step
    assert restored.source == rec.source


# --- Enum string-ness --------------------------------------------------------


def test_recommendation_kind_is_string_enum() -> None:
    """RecommendationKind members compare equal to their string values."""
    assert RecommendationKind.install_tool == "install_tool"
    assert RecommendationKind.enable_depth == "enable_depth"
    assert RecommendationKind.add_test == "add_test"
    assert RecommendationKind.configure_runner == "configure_runner"
    assert RecommendationKind.enable_ai_checks == "enable_ai_checks"
    # .value is the plain string.
    assert RecommendationKind.add_test.value == "add_test"


def test_recommendation_priority_is_string_enum() -> None:
    """RecommendationPriority members compare equal to their string values."""
    assert RecommendationPriority.high == "high"
    assert RecommendationPriority.medium == "medium"
    assert RecommendationPriority.low == "low"
    assert RecommendationPriority.high.value == "high"


def test_enum_values_serialize_as_strings_in_json() -> None:
    """When dumped to JSON with mode='json', enum fields appear as plain strings."""
    rec = _make_rec(kind=RecommendationKind.enable_ai_checks, priority=RecommendationPriority.high)
    dumped = rec.model_dump(mode="json")
    assert dumped["kind"] == "enable_ai_checks"
    assert dumped["priority"] == "high"
