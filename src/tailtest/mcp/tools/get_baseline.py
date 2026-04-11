# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""``get_baseline`` MCP tool — returns the current baseline summary."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from tailtest.core.baseline import BaselineManager
from tailtest.mcp.tools.base import BaseTool, ToolResponse, error_response, text_response


class GetBaselineTool(BaseTool):
    name: ClassVar[str] = "get_baseline"
    description: ClassVar[str] = (
        "Return a summary of the current tailtest baseline: how many findings "
        "are accepted as existing debt, the newest entry's first-seen date, and "
        "a breakdown by finding kind. Use this to answer 'what debt has tailtest "
        "already accepted?' questions."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max number of recent entries to include. Default 10.",
                "default": 10,
            },
        },
    }

    async def invoke(self, arguments: dict[str, Any]) -> ToolResponse:
        limit = int(arguments.get("limit", 10))
        try:
            manager = BaselineManager(self.project_root / ".tailtest")
            baseline = manager.load()
        except Exception as exc:  # noqa: BLE001
            return error_response(f"failed to load baseline: {type(exc).__name__}: {exc}")

        counts_by_kind: dict[str, int] = {}
        for entry in baseline.entries.values():
            counts_by_kind[entry.kind] = counts_by_kind.get(entry.kind, 0) + 1

        # Most-recent entries by first_seen
        recent = sorted(
            baseline.entries.values(),
            key=lambda e: e.first_seen,
            reverse=True,
        )[:limit]

        payload = {
            "exists": manager.exists(),
            "schema_version": baseline.schema_version,
            "generated_at": baseline.generated_at.isoformat(),
            "total_entries": len(baseline.entries),
            "counts_by_kind": counts_by_kind,
            "recent": [
                {
                    "id": e.id,
                    "kind": e.kind,
                    "file": e.file,
                    "line": e.line,
                    "rule_id": e.rule_id,
                    "first_seen": e.first_seen.isoformat(),
                    "failure_streak": e.failure_streak,
                    "reason": e.reason,
                }
                for e in recent
            ],
        }
        return text_response(json.dumps(payload, indent=2))
