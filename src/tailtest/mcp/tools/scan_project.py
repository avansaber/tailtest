# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""``scan_project`` MCP tool — runs the ProjectScanner and returns a ProjectProfile."""

from __future__ import annotations

from typing import Any, ClassVar

from tailtest.core.scan import ProjectScanner
from tailtest.mcp.tools.base import BaseTool, ToolResponse, error_response, text_response


class ScanProjectTool(BaseTool):
    name: ClassVar[str] = "scan_project"
    description: ClassVar[str] = (
        "Scan the project and return a structured summary of what tailtest sees: "
        "languages, frameworks, runners, plan files, AI-surface markers, and the "
        "likely_vibe_coded heuristic. Use this first whenever you want to understand "
        "a new project. Phase 1 ships only shallow scan (fast, no LLM calls). "
        "Deep scan (with LLM-generated summary) lands in Phase 3."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "deep": {
                "type": "boolean",
                "description": "Run deep scan (Phase 3 only; falls back to shallow in Phase 1).",
                "default": False,
            },
        },
    }

    async def invoke(self, arguments: dict[str, Any]) -> ToolResponse:
        try:
            scanner = ProjectScanner(self.project_root)
            profile = scanner.scan_shallow()
            # Phase 1: always shallow. Phase 3 will honor the `deep` flag.
            return text_response(profile.to_json(indent=2))
        except Exception as exc:  # noqa: BLE001
            return error_response(f"scan_project failed: {type(exc).__name__}: {exc}")
