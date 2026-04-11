# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""``tailtest_status`` MCP tool — returns config, last run summary, and scan snapshot."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from tailtest import __version__
from tailtest.core.baseline import BaselineManager
from tailtest.core.config import ConfigLoader
from tailtest.core.scan import ProjectScanner
from tailtest.mcp.tools.base import BaseTool, ToolResponse, text_response


class TailtestStatusTool(BaseTool):
    name: ClassVar[str] = "tailtest_status"
    description: ClassVar[str] = (
        "Return an at-a-glance status for tailtest in the current project: "
        "version, depth mode, detected runners, baseline summary, and the "
        "last run if any. Use this for /tailtest:status skill output and for "
        "quick health checks. Cheap — no test execution, just reads local state."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
    }

    async def invoke(self, arguments: dict[str, Any]) -> ToolResponse:
        tailtest_dir = self.project_root / ".tailtest"

        # Config (fresh read per audit gap #4)
        config = ConfigLoader(tailtest_dir).load()

        # Cached profile if one exists, otherwise do a quick scan so we can
        # answer "what does tailtest see here?"
        scanner = ProjectScanner(self.project_root)
        profile = scanner.load_profile(tailtest_dir)
        if profile is None:
            profile = scanner.scan_shallow()

        # Baseline
        baseline = BaselineManager(tailtest_dir).load()

        # Last run summary, if reports/latest.json exists
        last_run = None
        latest_path = tailtest_dir / "reports" / "latest.json"
        if latest_path.exists():
            try:
                last_run_data = json.loads(latest_path.read_text(encoding="utf-8"))
                last_run = {
                    "run_id": last_run_data.get("run_id"),
                    "depth": last_run_data.get("depth"),
                    "summary_line": last_run_data.get("summary_line"),
                    "duration_ms": last_run_data.get("duration_ms"),
                    "tests_passed": last_run_data.get("tests_passed", 0),
                    "tests_failed": last_run_data.get("tests_failed", 0),
                    "new_findings": len(
                        [
                            f
                            for f in last_run_data.get("findings", [])
                            if not f.get("in_baseline", False)
                        ]
                    ),
                }
            except Exception:  # noqa: BLE001
                last_run = {"error": "failed to parse .tailtest/reports/latest.json"}

        payload = {
            "tailtest_version": __version__,
            "project_root": str(self.project_root),
            "config": {
                "depth": config.depth.value,
                "schema_version": config.schema_version,
                "interview_completed": config.interview_completed,
                "auto_offer_generation": config.notifications.auto_offer_generation,
            },
            "profile_summary": {
                "primary_language": profile.primary_language,
                "total_files_walked": profile.total_files_walked,
                "runners": [r.name for r in profile.runners_detected],
                "ai_surface": profile.ai_surface.value,
                "likely_vibe_coded": profile.likely_vibe_coded,
                "scan_status": profile.scan_status.value,
            },
            "baseline": {
                "total_entries": len(baseline.entries),
                "generated_at": baseline.generated_at.isoformat(),
            },
            "last_run": last_run,
        }
        return text_response(json.dumps(payload, indent=2))
