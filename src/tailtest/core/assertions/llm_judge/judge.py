"""LLM-judge stub — minimal Phase 0 definitions to unblock ``tailtest.llm.claude_cli``.

The real implementation lands in Phase 3 (opportunity detection). In
Phase 0 we only need:

- ``JUDGE_SYSTEM_PROMPT`` — the system prompt passed to ``claude -p``
- ``JudgeResult`` — the typed result object returned by a judge call

Both are defined minimally here so that the LLM transport code imports
cleanly without pulling in the full Phase 3 assertion engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field

JUDGE_SYSTEM_PROMPT = """You are a strict, honest evaluator.

You will be given:
1. An evaluation rubric
2. An original user input
3. An AI assistant response

Your job is to evaluate the response against the rubric and return
a JSON object with three fields:

{
  "passed": true | false,
  "score": 0.0 to 1.0 (float),
  "reason": "one or two sentences explaining the verdict"
}

Be precise. Be honest. If the response violates the rubric, mark it as
failed even if it looks plausible on the surface. If you are uncertain,
lean toward failing — false negatives are cheaper than false positives
in a validation context.

Return ONLY the JSON object. No markdown code fences, no commentary.
"""


@dataclass
class JudgeResult:
    """Result of a single judge evaluation.

    Phase 0 minimal shape. Phase 3 may extend this with additional fields
    (rubric metadata, intermediate reasoning, etc.) but must remain
    backwards-compatible.
    """

    passed: bool
    score: float
    reason: str
    cost_usd: float = 0.0
    model: str = ""
    latency_ms: float = 0.0
    raw_response: str = ""
    metadata: dict[str, object] = field(default_factory=dict)
