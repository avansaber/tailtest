"""ProjectScanner — orchestrates the detectors and produces a ProjectProfile.

Phase 1 Task 1.12a. See `detectors.py` for the per-capability logic and
`profile.py` for the data shape. See ADR 0010 for the design rationale.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from tailtest.core.scan import detectors
from tailtest.core.scan.profile import (
    ProjectProfile,
    ScanStatus,
)

logger = logging.getLogger(__name__)


class ProjectScanner:
    """Walks a project directory and produces a `ProjectProfile`.

    Phase 1 ships only the shallow-scan path. Deep scan (one ``claude -p``
    call, adds `llm_summary`) is Phase 3 Task 3.1 and not yet implemented.
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).resolve()

    def scan_shallow(self) -> ProjectProfile:
        """Produce a ProjectProfile without any LLM calls.

        Budget: ≤5 seconds on projects up to ~10,000 files. On larger
        projects, walks stop early and set `scan_status = "partial"` +
        `scan_mode = "partial"`. Never raises — exceptions are caught
        and returned as a `ScanStatus.FAILED` profile with `scan_error`
        populated.
        """
        start_ms = time.monotonic() * 1000.0
        try:
            return self._scan_inner(start_ms)
        except Exception as exc:  # noqa: BLE001 — we genuinely want to catch anything
            logger.exception("project scan failed")
            elapsed = time.monotonic() * 1000.0 - start_ms
            return ProjectProfile(
                root=self.project_root,
                scan_status=ScanStatus.FAILED,
                scan_error=f"{type(exc).__name__}: {exc}",
                scan_duration_ms=elapsed,
                scan_mode="failed",
            )

    def _scan_inner(self, start_ms: float) -> ProjectProfile:
        # 1. Walk the file tree
        files, hit_ceiling = detectors.walk_project(self.project_root)

        # 2. Detect languages + pick the primary
        languages, primary_language = detectors.detect_languages(files)

        # 3. Parse manifests for frameworks
        frameworks = detectors.detect_frameworks(self.project_root)

        # 4. Detect test runners
        runners = detectors.detect_runners(self.project_root, languages)

        # 5. Infrastructure
        infrastructure = detectors.detect_infrastructure(self.project_root)

        # 6. Plan files (CLAUDE.md, AGENTS.md, README, ROADMAP, .cursor/, .claude/)
        plan_files = detectors.detect_plan_files(self.project_root)

        # 7. Top-level directory classification
        directories = detectors.classify_directories(self.project_root)

        # 8. AI surface determination (uses detected frameworks + file contents)
        ai_surface, ai_confidence, ai_signals = detectors.detect_ai_surface(
            self.project_root, files, frameworks
        )

        # 9. Vibe-coded heuristic (cheap filesystem check on plan files)
        likely_vibe_coded, vibe_signals = detectors.compute_likely_vibe_coded(plan_files)

        # 10. Content hash for cache invalidation
        content_hash = detectors.compute_content_hash(self.project_root)

        elapsed = time.monotonic() * 1000.0 - start_ms
        scan_mode = "partial" if hit_ceiling else "shallow"
        scan_status = ScanStatus.PARTIAL if hit_ceiling else ScanStatus.OK

        return ProjectProfile(
            root=self.project_root,
            scan_status=scan_status,
            scan_mode=scan_mode,
            total_files_walked=len(files),
            scan_duration_ms=elapsed,
            content_hash=content_hash,
            languages=languages,
            primary_language=primary_language,
            runners_detected=runners,
            frameworks_detected=frameworks,
            infrastructure_detected=infrastructure,
            plan_files_detected=plan_files,
            directories=directories,
            ai_surface=ai_surface,
            ai_confidence=ai_confidence,
            ai_signals=ai_signals,
            likely_vibe_coded=likely_vibe_coded,
            vibe_coded_signals=vibe_signals,
        )

    # --- Persistence ---

    def save_profile(self, profile: ProjectProfile, tailtest_dir: Path | None = None) -> Path:
        """Write the profile JSON to ``<tailtest_dir>/profile.json``.

        If ``tailtest_dir`` is None, defaults to ``<project_root>/.tailtest``.
        Creates the directory if it doesn't exist. Returns the path written.
        """
        target = tailtest_dir or (self.project_root / ".tailtest")
        target.mkdir(parents=True, exist_ok=True)
        path = target / "profile.json"
        path.write_text(profile.to_json(), encoding="utf-8")
        return path

    def load_profile(self, tailtest_dir: Path | None = None) -> ProjectProfile | None:
        """Read the profile JSON from disk, or None if missing/invalid."""
        target = tailtest_dir or (self.project_root / ".tailtest")
        path = target / "profile.json"
        if not path.exists():
            return None
        try:
            text = path.read_text(encoding="utf-8")
            return ProjectProfile.from_json(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to load cached profile: %s", exc)
            return None

    def is_cache_fresh(self, profile: ProjectProfile) -> bool:
        """Check if a cached profile is still valid by re-hashing structural files."""
        current_hash = detectors.compute_content_hash(self.project_root)
        return profile.content_hash == current_hash
