"""ProjectScanner -- orchestrates the detectors and produces a ProjectProfile.

Phase 1 Task 1.12a. See `detectors.py` for the per-capability logic and
`profile.py` for the data shape. See ADR 0010 for the design rationale.

Phase 3 Task 3.1 adds `scan_deep()`: an async method that calls the claude
CLI to produce a structured summary + recommendations and caches the result.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from tailtest.core.recommendations import (
    DismissalStore,
    Recommendation,
    RecommendationKind,
    RecommendationPriority,
)
from tailtest.core.scan import detectors
from tailtest.core.scan.profile import (
    ProjectProfile,
    ScanStatus,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DeepScanResult schema
# ---------------------------------------------------------------------------

_DEEP_SCAN_MODEL = "claude-haiku-4-5-20251001"
_DEEP_SCAN_MAX_INPUT_TOKENS = 8_000  # chars-based budget (rough approximation)
_DEEP_SCAN_TIMEOUT_SECS = 60
_DEEP_SCAN_CACHE_TTL_SECS = 24 * 3600  # 24 hours

# Files gathered for the deep scan context (in priority order).
_CONTEXT_FILES = [
    "README.md",
    "CLAUDE.md",
    "AGENTS.md",
    "ROADMAP.md",
]
_MANIFEST_FILES = [
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "requirements.txt",
]

_MAX_FILE_LINES = 500

_DEEP_SCAN_PROMPT_TEMPLATE = """\
You are analyzing a software project. Below is context gathered from the \
project's key files and a structural profile produced by static analysis.

{context}

Analyze this project and return a JSON object with this exact shape:
{{
  "summary": "2-3 sentence plain-English description of what this project does",
  "concerns": ["one sentence per concern, max 5"],
  "recommendations": [
    {{
      "kind": "install_tool|enable_depth|add_test|configure_runner|enable_ai_checks",
      "priority": "high|medium|low",
      "title": "short title",
      "why": "one sentence justification",
      "next_step": "concrete action the user can take"
    }}
  ]
}}
Only return valid JSON. No markdown fences. Max 5 recommendations.\
"""


@dataclass
class DeepScanResult:
    """Result of an AI-powered deep project analysis."""

    summary: str
    concerns: list[str] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    cached: bool = False
    content_hash: str = ""


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
        """Read the profile JSON from disk, or None if missing/invalid.

        If the profile contains recommendations, dismissal state from
        .tailtest/dismissed.json is applied so callers see up-to-date
        `dismissed_until` values.
        """
        target = tailtest_dir or (self.project_root / ".tailtest")
        path = target / "profile.json"
        if not path.exists():
            return None
        try:
            text = path.read_text(encoding="utf-8")
            profile = ProjectProfile.from_json(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to load cached profile: %s", exc)
            return None

        # Apply stored dismissals to the recommendations list.
        if profile.recommendations:
            try:
                store = DismissalStore(self.project_root)
                recs = [Recommendation.model_validate(r) for r in profile.recommendations]
                recs = store.apply(recs)
                profile = profile.model_copy(
                    update={"recommendations": [r.model_dump(mode="json") for r in recs]}
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("failed to apply dismissals to profile: %s", exc)

        return profile

    def is_cache_fresh(self, profile: ProjectProfile) -> bool:
        """Check if a cached profile is still valid by re-hashing structural files."""
        current_hash = detectors.compute_content_hash(self.project_root)
        return profile.content_hash == current_hash

    # --- Deep scan (Phase 3 Task 3.1) ---

    async def scan_deep(
        self,
        *,
        run_id: str | None = None,
        force: bool = False,
    ) -> DeepScanResult | None:
        """Run a deep AI-powered project analysis.

        Returns a DeepScanResult or None if the LLM is unavailable or
        the response cannot be parsed. Writes .tailtest/scan.md on success.

        Uses a content hash to cache results. Pass force=True to bypass
        the cache.
        """
        try:
            return await self._scan_deep_inner(run_id=run_id, force=force)
        except Exception as exc:  # noqa: BLE001
            logger.warning("scan_deep failed unexpectedly: %s", exc)
            return None

    async def _scan_deep_inner(
        self,
        *,
        run_id: str | None,
        force: bool,
    ) -> DeepScanResult | None:
        tailtest_dir = self.project_root / ".tailtest"
        cache_dir = tailtest_dir / "cache"

        # 1. Gather context files and compute a content hash for cache keying.
        context_parts, gathered_content = self._gather_context()
        cache_key = self._compute_deep_cache_key(gathered_content)
        cache_file = cache_dir / f"deep_scan_{cache_key[:16]}.json"

        # 2. Cache hit path.
        if not force:
            cached_result = self._load_deep_cache(cache_file)
            if cached_result is not None:
                self._write_scan_md(cached_result, tailtest_dir)
                return cached_result

        # 3. Build prompt and call the LLM.
        context_text = "\n\n".join(context_parts)
        prompt = _DEEP_SCAN_PROMPT_TEMPLATE.format(context=context_text)

        raw_text = await self._call_claude(prompt)
        if raw_text is None:
            return None

        # 4. Parse the JSON response.
        result = self._parse_deep_response(raw_text, cache_key)
        if result is None:
            return None

        # 5. Write outputs.
        cache_dir.mkdir(parents=True, exist_ok=True)
        tailtest_dir.mkdir(parents=True, exist_ok=True)

        self._save_deep_cache(cache_file, result)
        self._write_scan_md(result, tailtest_dir)
        self._merge_into_profile_json(result, tailtest_dir)

        return result

    # --- Context gathering ---

    def _gather_context(self) -> tuple[list[str], str]:
        """Read context files and return (parts, concatenated_content).

        Parts are formatted sections for the LLM prompt. The concatenated
        content is used for the cache key hash.
        """
        parts: list[str] = []
        all_content_pieces: list[str] = []

        char_budget = _DEEP_SCAN_MAX_INPUT_TOKENS * 4  # rough chars-per-token

        for filename in _CONTEXT_FILES + _MANIFEST_FILES:
            path = self.project_root / filename
            if not path.exists() or not path.is_file():
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue

            if len(lines) > _MAX_FILE_LINES:
                lines = lines[:_MAX_FILE_LINES]
                truncated = True
            else:
                truncated = False

            content = "\n".join(lines)
            if char_budget - len(content) < 0:
                # Skip files that would blow the budget.
                break
            char_budget -= len(content)

            suffix = " (truncated)" if truncated else ""
            parts.append(f"=== {filename}{suffix} ===\n{content}")
            all_content_pieces.append(content)

        # Also include a compact JSON profile summary.
        profile = self.scan_shallow()
        profile_summary = self._profile_to_summary_dict(profile)
        profile_json = json.dumps(profile_summary, indent=2)
        parts.append(f"=== profile.json (structural analysis) ===\n{profile_json}")
        all_content_pieces.append(profile_json)

        concatenated = "\n\n".join(all_content_pieces)
        return parts, concatenated

    def _profile_to_summary_dict(self, profile: ProjectProfile) -> dict:
        return {
            "primary_language": profile.primary_language,
            "languages": profile.languages,
            "frameworks": [f.name for f in profile.frameworks_detected],
            "runners": [r.name for r in profile.runners_detected],
            "ai_surface": profile.ai_surface.value if profile.ai_surface else None,
            "ai_confidence": profile.ai_confidence.value if profile.ai_confidence else None,
            "likely_vibe_coded": profile.likely_vibe_coded,
            "total_files": profile.total_files_walked,
        }

    # --- Cache key ---

    def _compute_deep_cache_key(self, content: str) -> str:
        h = hashlib.sha256()
        h.update(str(self.project_root).encode("utf-8"))
        h.update(b"\x00")
        h.update(content.encode("utf-8"))
        return h.hexdigest()

    # --- Cache I/O ---

    def _load_deep_cache(self, cache_file: Path) -> DeepScanResult | None:
        if not cache_file.exists():
            return None
        # TTL check via mtime.
        age = time.time() - cache_file.stat().st_mtime
        if age > _DEEP_SCAN_CACHE_TTL_SECS:
            return None
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            return self._dict_to_result(data, cached=True)
        except Exception as exc:  # noqa: BLE001
            logger.debug("deep scan cache unreadable: %s", exc)
            return None

    def _save_deep_cache(self, cache_file: Path, result: DeepScanResult) -> None:
        try:
            data = self._result_to_dict(result)
            cache_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("failed to write deep scan cache: %s", exc)

    def _result_to_dict(self, result: DeepScanResult) -> dict:
        return {
            "summary": result.summary,
            "concerns": result.concerns,
            "recommendations": [r.model_dump(mode="json") for r in result.recommendations],
            "content_hash": result.content_hash,
        }

    def _dict_to_result(self, data: dict, *, cached: bool = False) -> DeepScanResult:
        recommendations: list[Recommendation] = []
        for r in data.get("recommendations") or []:
            if not isinstance(r, dict):
                continue
            try:
                kind_val = r.get("kind", "")
                priority_val = r.get("priority", "medium")
                # Validate enum values; skip invalid entries rather than crashing.
                kind = RecommendationKind(kind_val)
                priority = RecommendationPriority(priority_val)
                recommendations.append(
                    Recommendation(
                        kind=kind,
                        priority=priority,
                        title=r.get("title", ""),
                        why=r.get("why", ""),
                        next_step=r.get("next_step", ""),
                        source="llm",
                    )
                )
            except (ValueError, Exception) as exc:  # noqa: BLE001
                logger.debug("skipping invalid recommendation from LLM: %s", exc)
        return DeepScanResult(
            summary=data.get("summary", ""),
            concerns=list(data.get("concerns") or []),
            recommendations=recommendations,
            cached=cached,
            content_hash=data.get("content_hash", ""),
        )

    # --- LLM call ---

    async def _call_claude(self, prompt: str) -> str | None:
        """Call claude -p with the given prompt, return the result text or None."""
        try:
            process = await asyncio.create_subprocess_exec(
                "claude",
                "-p",
                prompt,
                "--output-format",
                "json",
                "--model",
                _DEEP_SCAN_MODEL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=_DEEP_SCAN_TIMEOUT_SECS,
            )
        except FileNotFoundError:
            logger.warning(
                "scan_deep: claude CLI not found. Ensure you are running inside Claude Code."
            )
            return None
        except TimeoutError:
            logger.warning(
                "scan_deep: claude CLI call timed out after %ds", _DEEP_SCAN_TIMEOUT_SECS
            )
            return None
        except OSError as exc:
            logger.warning("scan_deep: failed to run claude CLI: %s", exc)
            return None

        if process.returncode != 0:
            stderr_text = stderr_bytes.decode(errors="replace").strip()
            stdout_text = stdout_bytes.decode(errors="replace").strip()
            detail = stderr_text or stdout_text or f"exit code {process.returncode}"
            logger.warning("scan_deep: claude CLI exited with error: %s", detail)
            return None

        stdout_text = stdout_bytes.decode(errors="replace").strip()
        # The claude --output-format json envelope has shape:
        # {"type": "result", "result": "<inner text>", ...}
        try:
            outer = json.loads(stdout_text)
            return str(outer.get("result", ""))
        except json.JSONDecodeError:
            # If the output is not the JSON envelope, treat it as plain text.
            return stdout_text

    # --- Response parsing ---

    def _parse_deep_response(self, raw: str, content_hash: str) -> DeepScanResult | None:
        text = raw.strip()
        # Strip markdown code fences if the model wrapped its response.
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("scan_deep: LLM returned non-JSON response: %s", raw[:300])
            return None

        if not isinstance(data, dict) or "summary" not in data:
            logger.warning("scan_deep: LLM response missing required 'summary' key")
            return None

        result = self._dict_to_result(data)
        result.content_hash = content_hash
        return result

    # --- Output writers ---

    def _write_scan_md(self, result: DeepScanResult, tailtest_dir: Path) -> None:
        """Write a human-readable .tailtest/scan.md from the DeepScanResult."""
        lines: list[str] = [
            "# Project Deep Scan",
            "",
            "## Summary",
            "",
            result.summary,
            "",
        ]
        if result.concerns:
            lines += ["## Concerns", ""]
            for concern in result.concerns:
                lines.append(f"- {concern}")
            lines.append("")

        if result.recommendations:
            lines += [
                "## Recommendations",
                "",
                "| Priority | Title | Why | Next Step |",
                "| --- | --- | --- | --- |",
            ]
            for rec in result.recommendations:
                title = rec.title.replace("|", "\\|")
                why = rec.why.replace("|", "\\|")
                next_step = rec.next_step.replace("|", "\\|")
                lines.append(f"| {rec.priority} | {title} | {why} | {next_step} |")
            lines.append("")

        cached_note = " (from cache)" if result.cached else ""
        lines.append(f"*Generated by tailtest scan_deep{cached_note}.*")

        try:
            tailtest_dir.mkdir(parents=True, exist_ok=True)
            (tailtest_dir / "scan.md").write_text("\n".join(lines), encoding="utf-8")
        except OSError as exc:
            logger.warning("scan_deep: failed to write scan.md: %s", exc)

    def _merge_into_profile_json(self, result: DeepScanResult, tailtest_dir: Path) -> None:
        """Merge deep_scan data into .tailtest/profile.json if it exists."""
        profile_path = tailtest_dir / "profile.json"
        try:
            if profile_path.exists():
                existing = json.loads(profile_path.read_text(encoding="utf-8"))
            else:
                existing = {}
            existing["deep_scan"] = self._result_to_dict(result)
            # Also store serialized recommendations in the top-level profile field.
            existing["recommendations"] = [
                r.model_dump(mode="json") for r in result.recommendations
            ]
            profile_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("scan_deep: failed to merge into profile.json: %s", exc)
