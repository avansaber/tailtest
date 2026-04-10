"""Rules-based recommendation engine for tailtest opportunity detection."""
from __future__ import annotations

import logging

from tailtest.core.recommendations.schema import (
    Recommendation,
    RecommendationKind,
    RecommendationPriority,
)
from tailtest.core.scan.profile import AISurface, InfrastructureKind, ProjectProfile

logger = logging.getLogger(__name__)

# Web framework names (as detected by detect_frameworks) that indicate a
# frontend project needing E2E coverage.
_WEB_FRAMEWORK_NAMES: frozenset[str] = frozenset(
    {
        "nextjs",
        "vue",
        "svelte",
        "sveltekit",
        "nuxt",
        "remix",
        "react",
    }
)

# E2E test tool names that indicate existing coverage.
_E2E_FRAMEWORK_NAMES: frozenset[str] = frozenset(
    {
        "playwright",
        "cypress",
        "puppeteer",
    }
)

# Dependency/framework names that indicate testcontainers is in use.
_TESTCONTAINERS_NAMES: frozenset[str] = frozenset(
    {
        "testcontainers",
    }
)

# Framework/runner names associated with Docker-based integration testing.
_DOCKER_TEST_NAMES: frozenset[str] = frozenset(
    {
        "testcontainers",
        "pytest-docker",
        "docker-compose",
    }
)

# Severity values from Finding.Severity that count as high-severity SCA.
_HIGH_SCA_SEVERITIES: frozenset[str] = frozenset({"high", "critical"})

_priority_order: dict[RecommendationPriority, int] = {
    RecommendationPriority.high: 0,
    RecommendationPriority.medium: 1,
    RecommendationPriority.low: 2,
}


class RecommendationEngine:
    """Compute actionable recommendations from a ProjectProfile.

    Rules are cheap and deterministic -- no LLM calls. Run on every session.
    LLM recommendations (source='llm') from scan_deep() are merged separately
    by the caller.
    """

    def compute(self, profile: ProjectProfile) -> list[Recommendation]:
        """Return all triggered recommendations for *profile*.

        Each rule is independent. A rule returns a Recommendation or None.
        Results are sorted: high priority first, then medium, then low.
        Duplicate ids are deduplicated (first occurrence wins).
        """
        rules = [
            self._rule_playwright,
            self._rule_testcontainers,
            self._rule_db_fixtures,
            self._rule_enable_ai_checks,
            self._rule_vibe_coder_test_gen,
            self._rule_sca_upgrade,
        ]

        seen_ids: set[str] = set()
        results: list[Recommendation] = []

        for rule in rules:
            try:
                rec = rule(profile)
            except Exception as exc:  # noqa: BLE001, defensive
                logger.warning("Recommendation rule %s failed: %s", rule.__name__, exc)
                continue
            if rec is None:
                continue
            if rec.id in seen_ids:
                continue
            seen_ids.add(rec.id)
            results.append(rec)

        # Primary sort: by priority (high first).
        # Secondary sort when vibe-coded: add_test kind is promoted to
        # the front of its priority tier so the most actionable item
        # for a vibe-coded project appears first.
        likely_vibe_coded = getattr(profile, "likely_vibe_coded", False)
        if likely_vibe_coded:
            results.sort(
                key=lambda r: (
                    _priority_order.get(r.priority, 99),
                    0 if r.kind == RecommendationKind.add_test else 1,
                )
            )
        else:
            results.sort(key=lambda r: _priority_order.get(r.priority, 99))
        return results

    # ------------------------------------------------------------------
    # Individual rules
    # ------------------------------------------------------------------

    def _rule_playwright(self, profile: ProjectProfile) -> Recommendation | None:
        """Recommend Playwright when a web framework is present but no E2E tool is."""
        try:
            framework_names = {f.name for f in profile.frameworks_detected}
        except Exception:  # noqa: BLE001
            return None

        has_web = bool(framework_names & _WEB_FRAMEWORK_NAMES)
        if not has_web:
            return None

        has_e2e = bool(framework_names & _E2E_FRAMEWORK_NAMES)
        if has_e2e:
            return None

        return Recommendation(
            kind=RecommendationKind.install_tool,
            priority=RecommendationPriority.medium,
            title="Add Playwright for end-to-end tests",
            why="This project has a web framework but no E2E test suite detected.",
            next_step=(
                "Run: `npm init playwright@latest` -- then add a smoke test"
                " for your main user flow."
            ),
        )

    def _rule_testcontainers(self, profile: ProjectProfile) -> Recommendation | None:
        """Recommend testcontainers when a Dockerfile is present but testcontainers is not."""
        try:
            infra_kinds = {i.kind for i in profile.infrastructure_detected}
        except Exception:  # noqa: BLE001
            return None

        has_docker = InfrastructureKind.DOCKER in infra_kinds
        if not has_docker:
            return None

        try:
            framework_names = {f.name for f in profile.frameworks_detected}
            runner_names = {r.name for r in profile.runners_detected}
        except Exception:  # noqa: BLE001
            return None

        all_tool_names = framework_names | runner_names
        has_testcontainers = bool(all_tool_names & _TESTCONTAINERS_NAMES)
        if has_testcontainers:
            return None

        return Recommendation(
            kind=RecommendationKind.install_tool,
            priority=RecommendationPriority.low,
            title="Use testcontainers for integration tests",
            why="A Dockerfile is present but no testcontainers dependency was detected.",
            next_step=(
                "Add `testcontainers` to your dev dependencies and wrap DB calls"
                " in container fixtures."
            ),
        )

    def _rule_db_fixtures(self, profile: ProjectProfile) -> Recommendation | None:
        """Recommend DB fixtures when docker-compose is present but no test fixture strategy is."""
        try:
            infra_kinds = {i.kind for i in profile.infrastructure_detected}
        except Exception:  # noqa: BLE001
            return None

        has_compose = InfrastructureKind.DOCKER_COMPOSE in infra_kinds
        if not has_compose:
            return None

        try:
            framework_names = {f.name for f in profile.frameworks_detected}
            runner_names = {r.name for r in profile.runners_detected}
        except Exception:  # noqa: BLE001
            return None

        all_tool_names = framework_names | runner_names
        has_fixture_strategy = bool(all_tool_names & _DOCKER_TEST_NAMES)
        if has_fixture_strategy:
            return None

        return Recommendation(
            kind=RecommendationKind.configure_runner,
            priority=RecommendationPriority.low,
            title="Add database test fixtures",
            why="Database services detected but no test fixture strategy found.",
            next_step=(
                "Add a `conftest.py` fixture that starts a test database before"
                " your test suite runs."
            ),
        )

    def _rule_enable_ai_checks(self, profile: ProjectProfile) -> Recommendation | None:
        """Recommend enabling AI checks when an agent project has them disabled."""
        try:
            ai_surface = profile.ai_surface
        except Exception:  # noqa: BLE001
            return None

        if ai_surface != AISurface.AGENT:
            return None

        # scan_mode is "shallow" or "partial" when not in thorough depth.
        # The profile has no 'depth' field and no 'ai_checks_enabled' field.
        # We trigger whenever ai_surface=AGENT and scan_mode is not 'deep'.
        try:
            scan_mode = profile.scan_mode
        except Exception:  # noqa: BLE001
            scan_mode = "shallow"

        if scan_mode == "deep":
            # A deep scan implies thorough depth; checks are likely enabled.
            return None

        return Recommendation(
            kind=RecommendationKind.enable_ai_checks,
            priority=RecommendationPriority.high,
            title="Enable AI-specific checks for this agent project",
            why=(
                "tailtest detected an AI agent but AI-specific checks"
                " (LLM-judge assertions) are disabled."
            ),
            next_step=(
                "Run `/tailtest accept-ai-checks` to enable checks at thorough depth."
                " They only run when you set depth to `thorough`."
            ),
        )

    def _rule_vibe_coder_test_gen(self, profile: ProjectProfile) -> Recommendation | None:
        """Recommend test generation for vibe-coded projects with no tests."""
        try:
            likely_vibe_coded = profile.likely_vibe_coded
        except Exception:  # noqa: BLE001
            return None

        if not likely_vibe_coded:
            return None

        # Check for test coverage: runners_detected is non-empty means tests exist,
        # or directories.tests is non-empty.
        try:
            has_runners = bool(profile.runners_detected)
            has_test_dirs = bool(profile.directories.tests)
        except Exception:  # noqa: BLE001
            return None

        has_tests = has_runners or has_test_dirs
        if has_tests:
            return None

        return Recommendation(
            kind=RecommendationKind.add_test,
            priority=RecommendationPriority.high,
            title="Generate tests for your key functions",
            why="This looks like a vibe-coded project with no test coverage yet.",
            next_step=(
                "Run `/tailtest gen <your-main-file.py>` to generate a starter"
                " test file for your most important module."
            ),
        )

    def _rule_sca_upgrade(self, profile: ProjectProfile) -> Recommendation | None:
        """Recommend upgrading vulnerable dependencies when high-severity SCA findings exist.

        The ProjectProfile does not carry raw SCA Finding objects, but the
        recommendations list (populated by deep scan) may include SCA-related
        entries. We also check for the presence of serialized recommendations
        with SCA indicators in the profile. If no SCA data is available in the
        profile this rule returns None safely.
        """
        try:
            recs_raw = profile.recommendations
        except Exception:  # noqa: BLE001
            return None

        if not recs_raw:
            return None

        # Look for any existing recommendation that signals a high-severity
        # vulnerability (a deep-scan result with 'sca' or 'upgrade' in its
        # kind or title and a high/critical priority).
        for entry in recs_raw:
            if not isinstance(entry, dict):
                continue
            try:
                priority = str(entry.get("priority", "")).lower()
                title = str(entry.get("title", "")).lower()
                kind = str(entry.get("kind", "")).lower()
            except Exception:  # noqa: BLE001
                continue

            is_high_priority = priority in _HIGH_SCA_SEVERITIES
            looks_like_sca = (
                "sca" in kind
                or "vulnerab" in title
                or "upgrade" in title
                or "cve" in title
            )
            if is_high_priority and looks_like_sca:
                return Recommendation(
                    kind=RecommendationKind.install_tool,
                    priority=RecommendationPriority.high,
                    title="Upgrade vulnerable dependencies",
                    why=(
                        "One or more dependencies have known high-severity vulnerabilities."
                    ),
                    next_step=(
                        "Run `python3 -m pip install --upgrade <package>` for each"
                        " flagged dependency. Check `.tailtest/reports/latest.html`"
                        " for details."
                    ),
                )

        return None

    # ------------------------------------------------------------------
    # Merge helper
    # ------------------------------------------------------------------

    def merge(
        self,
        rules_recs: list[Recommendation],
        llm_recs: list[Recommendation],
    ) -> list[Recommendation]:
        """Merge rules-based and LLM recommendations, deduplicating by id.

        Rules-based recommendations take priority on id conflicts.
        LLM recommendations are appended after, then the combined list is
        re-sorted by priority.
        """
        seen_ids: set[str] = set()
        merged: list[Recommendation] = []

        for rec in rules_recs:
            if rec.id not in seen_ids:
                seen_ids.add(rec.id)
                merged.append(rec)

        for rec in llm_recs:
            if rec.id not in seen_ids:
                seen_ids.add(rec.id)
                merged.append(rec)

        merged.sort(key=lambda r: _priority_order.get(r.priority, 99))
        return merged
