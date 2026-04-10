"""Tests for RecommendationEngine (Phase 3 Task 3.3)."""

from __future__ import annotations

import logging
from pathlib import Path

from tailtest.core.recommendations import (
    Recommendation,
    RecommendationKind,
    RecommendationPriority,
)
from tailtest.core.recommender import RecommendationEngine
from tailtest.core.scan.profile import (
    AIConfidence,
    AISurface,
    DetectedFramework,
    DetectedInfrastructure,
    DetectedRunner,
    DirectoryClassification,
    InfrastructureKind,
    ProjectProfile,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_profile(tmp_path: Path, **kwargs) -> ProjectProfile:
    """Return a minimal ProjectProfile with no signals triggered by default."""
    defaults: dict = dict(
        root=tmp_path,
        primary_language="python",
        languages={"python": 5},
        ai_surface=AISurface.NONE,
        ai_confidence=AIConfidence.HIGH,
        likely_vibe_coded=False,
        frameworks_detected=[],
        runners_detected=[],
        infrastructure_detected=[],
        directories=DirectoryClassification(),
        scan_mode="shallow",
        recommendations=[],
    )
    defaults.update(kwargs)
    return ProjectProfile(**defaults)


def _framework(name: str, category: str = "web") -> DetectedFramework:
    return DetectedFramework(name=name, source="package.json", category=category)


def _infra(kind: InfrastructureKind, tmp_path: Path) -> DetectedInfrastructure:
    f = tmp_path / "Dockerfile"
    f.touch()
    return DetectedInfrastructure(kind=kind, file=f)


def _runner(name: str, language: str = "python") -> DetectedRunner:
    return DetectedRunner(name=name, language=language)


def _rec(
    kind: RecommendationKind = RecommendationKind.add_test,
    priority: RecommendationPriority = RecommendationPriority.medium,
    title: str = "A test rec",
    source: str = "rules",
) -> Recommendation:
    return Recommendation(
        kind=kind,
        priority=priority,
        title=title,
        why="Because.",
        next_step="Do something.",
        source=source,
    )


# ---------------------------------------------------------------------------
# _rule_playwright
# ---------------------------------------------------------------------------


def test_playwright_fires_on_nextjs_with_no_e2e(tmp_path: Path) -> None:
    """Rule fires when Next.js is present and no E2E framework is detected."""
    profile = _base_profile(
        tmp_path,
        frameworks_detected=[_framework("nextjs", "web")],
        primary_language="typescript",
    )
    engine = RecommendationEngine()
    recs = engine.compute(profile)
    titles = [r.title for r in recs]
    assert any("Playwright" in t for t in titles)


def test_playwright_fires_on_vue_with_no_e2e(tmp_path: Path) -> None:
    """Rule fires for Vue projects with no E2E tool detected."""
    profile = _base_profile(
        tmp_path,
        frameworks_detected=[_framework("vue", "web")],
    )
    engine = RecommendationEngine()
    recs = engine.compute(profile)
    assert any("Playwright" in r.title for r in recs)


def test_playwright_does_not_fire_when_playwright_is_present(tmp_path: Path) -> None:
    """Rule must not fire when Playwright is already in the detected frameworks."""
    profile = _base_profile(
        tmp_path,
        frameworks_detected=[
            _framework("nextjs", "web"),
            _framework("playwright", "test"),
        ],
    )
    engine = RecommendationEngine()
    recs = engine.compute(profile)
    assert not any("Playwright" in r.title for r in recs)


def test_playwright_does_not_fire_when_cypress_is_present(tmp_path: Path) -> None:
    """Rule must not fire when Cypress is already in the detected frameworks."""
    profile = _base_profile(
        tmp_path,
        frameworks_detected=[
            _framework("svelte", "web"),
            _framework("cypress", "test"),
        ],
    )
    engine = RecommendationEngine()
    recs = engine.compute(profile)
    assert not any("Playwright" in r.title for r in recs)


def test_playwright_does_not_fire_on_python_only_project(tmp_path: Path) -> None:
    """Rule must not fire when the project has no web frameworks at all."""
    profile = _base_profile(
        tmp_path,
        frameworks_detected=[_framework("fastapi", "web")],
        primary_language="python",
    )
    # fastapi is a web framework but not in _WEB_FRAMEWORK_NAMES (JS-focused list)
    engine = RecommendationEngine()
    recs = engine.compute(profile)
    assert not any("Playwright" in r.title for r in recs)


# ---------------------------------------------------------------------------
# _rule_enable_ai_checks
# ---------------------------------------------------------------------------


def test_enable_ai_checks_fires_on_agent_profile(tmp_path: Path) -> None:
    """Rule fires when ai_surface=AGENT and scan_mode is shallow."""
    profile = _base_profile(
        tmp_path,
        ai_surface=AISurface.AGENT,
        ai_confidence=AIConfidence.HIGH,
        scan_mode="shallow",
    )
    engine = RecommendationEngine()
    recs = engine.compute(profile)
    assert any(r.kind == RecommendationKind.enable_ai_checks for r in recs)


def test_enable_ai_checks_fires_on_agent_with_partial_scan(tmp_path: Path) -> None:
    """Rule fires with partial scan mode as well (not deep)."""
    profile = _base_profile(
        tmp_path,
        ai_surface=AISurface.AGENT,
        scan_mode="partial",
    )
    engine = RecommendationEngine()
    recs = engine.compute(profile)
    assert any(r.kind == RecommendationKind.enable_ai_checks for r in recs)


def test_enable_ai_checks_does_not_fire_on_non_agent_profile(tmp_path: Path) -> None:
    """Rule must not fire when ai_surface is not AGENT."""
    profile = _base_profile(
        tmp_path,
        ai_surface=AISurface.UTILITY,
        scan_mode="shallow",
    )
    engine = RecommendationEngine()
    recs = engine.compute(profile)
    assert not any(r.kind == RecommendationKind.enable_ai_checks for r in recs)


def test_enable_ai_checks_does_not_fire_on_deep_scan(tmp_path: Path) -> None:
    """Rule must not fire when scan_mode is 'deep' (implies thorough depth)."""
    profile = _base_profile(
        tmp_path,
        ai_surface=AISurface.AGENT,
        scan_mode="deep",
    )
    engine = RecommendationEngine()
    recs = engine.compute(profile)
    assert not any(r.kind == RecommendationKind.enable_ai_checks for r in recs)


def test_enable_ai_checks_is_high_priority(tmp_path: Path) -> None:
    """The AI checks recommendation must be high priority."""
    profile = _base_profile(
        tmp_path,
        ai_surface=AISurface.AGENT,
        scan_mode="shallow",
    )
    engine = RecommendationEngine()
    recs = engine.compute(profile)
    ai_recs = [r for r in recs if r.kind == RecommendationKind.enable_ai_checks]
    assert ai_recs
    assert ai_recs[0].priority == RecommendationPriority.high


# ---------------------------------------------------------------------------
# _rule_vibe_coder_test_gen
# ---------------------------------------------------------------------------


def test_vibe_coder_test_gen_fires_when_vibe_coded_and_no_tests(tmp_path: Path) -> None:
    """Rule fires when likely_vibe_coded=True and no runners or test dirs exist."""
    profile = _base_profile(
        tmp_path,
        likely_vibe_coded=True,
        runners_detected=[],
        directories=DirectoryClassification(),
    )
    engine = RecommendationEngine()
    recs = engine.compute(profile)
    assert any(r.kind == RecommendationKind.add_test for r in recs)


def test_vibe_coder_test_gen_does_not_fire_when_runners_exist(tmp_path: Path) -> None:
    """Rule must not fire when test runners are detected (implies some tests exist)."""
    profile = _base_profile(
        tmp_path,
        likely_vibe_coded=True,
        runners_detected=[_runner("pytest")],
        directories=DirectoryClassification(),
    )
    engine = RecommendationEngine()
    recs = engine.compute(profile)
    assert not any(r.kind == RecommendationKind.add_test for r in recs)


def test_vibe_coder_test_gen_does_not_fire_when_test_dir_exists(tmp_path: Path) -> None:
    """Rule must not fire when a tests/ directory is detected."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    dirs = DirectoryClassification(tests=[tests_dir])
    profile = _base_profile(
        tmp_path,
        likely_vibe_coded=True,
        runners_detected=[],
        directories=dirs,
    )
    engine = RecommendationEngine()
    recs = engine.compute(profile)
    assert not any(r.kind == RecommendationKind.add_test for r in recs)


def test_vibe_coder_test_gen_does_not_fire_when_not_vibe_coded(tmp_path: Path) -> None:
    """Rule must not fire when likely_vibe_coded is False."""
    profile = _base_profile(
        tmp_path,
        likely_vibe_coded=False,
        runners_detected=[],
        directories=DirectoryClassification(),
    )
    engine = RecommendationEngine()
    recs = engine.compute(profile)
    assert not any(r.kind == RecommendationKind.add_test for r in recs)


def test_vibe_coder_test_gen_is_high_priority(tmp_path: Path) -> None:
    """The vibe-coder recommendation must be high priority."""
    profile = _base_profile(
        tmp_path,
        likely_vibe_coded=True,
        runners_detected=[],
        directories=DirectoryClassification(),
    )
    engine = RecommendationEngine()
    recs = engine.compute(profile)
    test_recs = [r for r in recs if r.kind == RecommendationKind.add_test]
    assert test_recs
    assert test_recs[0].priority == RecommendationPriority.high


# ---------------------------------------------------------------------------
# _rule_sca_upgrade
# ---------------------------------------------------------------------------


def test_sca_upgrade_fires_when_high_severity_sca_rec_present(tmp_path: Path) -> None:
    """Rule fires when the profile recommendations list has a high-priority SCA signal."""
    sca_entry = {
        "kind": "install_tool",
        "priority": "high",
        "title": "Upgrade vulnerable dependency (CVE-2024-1234)",
        "why": "The package has a known CVE.",
        "next_step": "pip install --upgrade package",
        "source": "llm",
    }
    profile = _base_profile(tmp_path, recommendations=[sca_entry])
    engine = RecommendationEngine()
    recs = engine.compute(profile)
    titles = [r.title for r in recs]
    assert any("Upgrade vulnerable" in t for t in titles)


def test_sca_upgrade_fires_on_critical_vulnerability_entry(tmp_path: Path) -> None:
    """Rule fires when priority is 'critical' (treated as high-severity)."""
    sca_entry = {
        "kind": "install_tool",
        "priority": "critical",
        "title": "Upgrade package with vulnerability",
        "why": "Critical CVE.",
        "next_step": "pip install --upgrade x",
        "source": "llm",
    }
    profile = _base_profile(tmp_path, recommendations=[sca_entry])
    engine = RecommendationEngine()
    recs = engine.compute(profile)
    assert any("Upgrade vulnerable" in r.title for r in recs)


def test_sca_upgrade_does_not_fire_when_no_recommendations(tmp_path: Path) -> None:
    """Rule must not fire when the profile has no recommendations at all."""
    profile = _base_profile(tmp_path, recommendations=[])
    engine = RecommendationEngine()
    recs = engine.compute(profile)
    assert not any("Upgrade vulnerable" in r.title for r in recs)


def test_sca_upgrade_does_not_fire_when_only_low_priority_recs(tmp_path: Path) -> None:
    """Rule must not fire when all existing recommendations are low priority."""
    low_entry = {
        "kind": "install_tool",
        "priority": "low",
        "title": "Consider upgrading some package",
        "why": "Minor issue.",
        "next_step": "pip install --upgrade y",
        "source": "llm",
    }
    profile = _base_profile(tmp_path, recommendations=[low_entry])
    engine = RecommendationEngine()
    recs = engine.compute(profile)
    assert not any("Upgrade vulnerable" in r.title for r in recs)


# ---------------------------------------------------------------------------
# _rule_testcontainers
# ---------------------------------------------------------------------------


def test_testcontainers_fires_when_dockerfile_and_no_testcontainers(tmp_path: Path) -> None:
    """Rule fires when a Dockerfile is present but testcontainers is not."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.touch()
    infra = [DetectedInfrastructure(kind=InfrastructureKind.DOCKER, file=dockerfile)]
    profile = _base_profile(tmp_path, infrastructure_detected=infra)
    engine = RecommendationEngine()
    recs = engine.compute(profile)
    assert any("testcontainers" in r.title.lower() for r in recs)


def test_testcontainers_does_not_fire_without_dockerfile(tmp_path: Path) -> None:
    """Rule must not fire when there is no Dockerfile detected."""
    profile = _base_profile(tmp_path, infrastructure_detected=[])
    engine = RecommendationEngine()
    recs = engine.compute(profile)
    assert not any("testcontainers" in r.title.lower() for r in recs)


# ---------------------------------------------------------------------------
# _rule_db_fixtures
# ---------------------------------------------------------------------------


def test_db_fixtures_fires_when_compose_present_and_no_fixture_tool(tmp_path: Path) -> None:
    """Rule fires when docker-compose is detected but no test fixture strategy is found."""
    compose = tmp_path / "docker-compose.yml"
    compose.touch()
    infra = [DetectedInfrastructure(kind=InfrastructureKind.DOCKER_COMPOSE, file=compose)]
    profile = _base_profile(tmp_path, infrastructure_detected=infra)
    engine = RecommendationEngine()
    recs = engine.compute(profile)
    assert any("database test fixtures" in r.title.lower() for r in recs)


def test_db_fixtures_does_not_fire_without_compose(tmp_path: Path) -> None:
    """Rule must not fire when docker-compose is not detected."""
    profile = _base_profile(tmp_path, infrastructure_detected=[])
    engine = RecommendationEngine()
    recs = engine.compute(profile)
    assert not any("database test fixtures" in r.title.lower() for r in recs)


# ---------------------------------------------------------------------------
# Priority sorting
# ---------------------------------------------------------------------------


def test_priority_sorting_high_before_medium_before_low(tmp_path: Path) -> None:
    """compute() output must be sorted high -> medium -> low priority."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.touch()
    compose = tmp_path / "docker-compose.yml"
    compose.touch()

    # Create a profile that triggers multiple rules at different priorities.
    # Agent (high) + Dockerfile (low) + compose (low) + vibe+no-tests (high).
    profile = _base_profile(
        tmp_path,
        ai_surface=AISurface.AGENT,
        scan_mode="shallow",
        likely_vibe_coded=True,
        runners_detected=[],
        directories=DirectoryClassification(),
        frameworks_detected=[_framework("nextjs", "web")],
        infrastructure_detected=[
            DetectedInfrastructure(kind=InfrastructureKind.DOCKER, file=dockerfile),
            DetectedInfrastructure(kind=InfrastructureKind.DOCKER_COMPOSE, file=compose),
        ],
    )
    engine = RecommendationEngine()
    recs = engine.compute(profile)

    # Extract priorities in order.
    priorities = [r.priority for r in recs]
    _order = {
        RecommendationPriority.high: 0,
        RecommendationPriority.medium: 1,
        RecommendationPriority.low: 2,
    }
    assert all(
        _order[priorities[i]] <= _order[priorities[i + 1]] for i in range(len(priorities) - 1)
    ), f"Priorities out of order: {priorities}"


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def test_deduplication_same_id_appears_once(tmp_path: Path) -> None:
    """compute() must not return duplicate ids even if multiple rules could yield one."""
    # Trigger vibe coder rule which has a stable id.
    profile = _base_profile(
        tmp_path,
        likely_vibe_coded=True,
        runners_detected=[],
        directories=DirectoryClassification(),
    )
    engine = RecommendationEngine()
    recs = engine.compute(profile)
    ids = [r.id for r in recs]
    assert len(ids) == len(set(ids)), "Duplicate recommendation ids found"


# ---------------------------------------------------------------------------
# Rule crash handling
# ---------------------------------------------------------------------------


def test_rule_crash_is_logged_and_skipped(tmp_path: Path, caplog) -> None:
    """A rule that raises must be logged as a warning and skipped, not re-raised."""

    class CrashingEngine(RecommendationEngine):
        def _rule_playwright(self, profile):  # type: ignore[override]
            raise RuntimeError("simulated crash")

    profile = _base_profile(tmp_path)

    with caplog.at_level(logging.WARNING, logger="tailtest.core.recommender.engine"):
        recs = CrashingEngine().compute(profile)

    # Should not raise; the rest of the rules should still run.
    assert isinstance(recs, list)
    assert any("_rule_playwright" in msg for msg in caplog.messages)


# ---------------------------------------------------------------------------
# merge()
# ---------------------------------------------------------------------------


def test_merge_deduplicates_by_id_rules_win(tmp_path: Path) -> None:
    """merge() keeps the rules-based rec when both have the same id."""
    rules_rec = _rec(title="Rules title", source="rules")
    # Build an LLM rec with the same computed id by using the same kind+title+applies_to.
    llm_rec = Recommendation(
        id=rules_rec.id,  # force same id
        kind=rules_rec.kind,
        priority=rules_rec.priority,
        title="LLM title",
        why="LLM why.",
        next_step="LLM step.",
        source="llm",
    )

    engine = RecommendationEngine()
    merged = engine.merge([rules_rec], [llm_rec])

    assert len(merged) == 1
    assert merged[0].source == "rules"
    assert merged[0].title == "Rules title"


def test_merge_appends_llm_recs_not_in_rules(tmp_path: Path) -> None:
    """merge() includes LLM recs that do not conflict with rules-based ones."""
    rules_rec = _rec(title="Rules only", source="rules")
    llm_rec = _rec(title="LLM only", source="llm")

    engine = RecommendationEngine()
    merged = engine.merge([rules_rec], [llm_rec])

    titles = {r.title for r in merged}
    assert "Rules only" in titles
    assert "LLM only" in titles
    assert len(merged) == 2


def test_merge_output_is_sorted_by_priority(tmp_path: Path) -> None:
    """merge() result must be sorted high -> medium -> low."""
    low_rec = _rec(
        title="Low priority",
        priority=RecommendationPriority.low,
        source="rules",
    )
    high_rec = _rec(
        title="High priority",
        priority=RecommendationPriority.high,
        source="llm",
    )
    medium_rec = _rec(
        title="Medium priority",
        priority=RecommendationPriority.medium,
        source="rules",
    )

    engine = RecommendationEngine()
    merged = engine.merge([low_rec, medium_rec], [high_rec])

    priorities = [r.priority for r in merged]
    _order = {
        RecommendationPriority.high: 0,
        RecommendationPriority.medium: 1,
        RecommendationPriority.low: 2,
    }
    assert all(
        _order[priorities[i]] <= _order[priorities[i + 1]] for i in range(len(priorities) - 1)
    ), f"merge() output not sorted: {priorities}"


def test_merge_empty_inputs(tmp_path: Path) -> None:
    """merge() with empty inputs returns an empty list."""
    engine = RecommendationEngine()
    assert engine.merge([], []) == []


def test_merge_rules_only(tmp_path: Path) -> None:
    """merge() with empty llm_recs returns just the rules-based recs."""
    rec = _rec(title="Rules only", source="rules")
    engine = RecommendationEngine()
    merged = engine.merge([rec], [])
    assert len(merged) == 1
    assert merged[0].title == "Rules only"


def test_merge_llm_only(tmp_path: Path) -> None:
    """merge() with empty rules_recs returns just the LLM recs."""
    rec = _rec(title="LLM only", source="llm")
    engine = RecommendationEngine()
    merged = engine.merge([], [rec])
    assert len(merged) == 1
    assert merged[0].title == "LLM only"
