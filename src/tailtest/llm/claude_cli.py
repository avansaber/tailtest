# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""Claude Code CLI judge — uses the ``claude`` subprocess for evaluations.

When running inside Claude Code (``claude`` on PATH), this is the preferred
transport: it uses the user's existing Claude Code subscription with zero
marginal cost and no API key required.

Copied from the v1 tailtest project per ADR 0006, with one adjustment: the
v1 version imported ``JudgeResult`` and ``JUDGE_SYSTEM_PROMPT`` from
``tailtest.core.assertions.llm_judge.judge``, which is a Phase 3 module
that doesn't exist yet in v0.1.0. Phase 0 provides minimal stubs at
:mod:`tailtest.core.assertions.llm_judge.judge` so this file imports
cleanly; Phase 3 replaces those stubs with the real implementation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from tailtest.core.assertions.llm_judge.judge import (
    JUDGE_SYSTEM_PROMPT,
    JudgeResult,
)

logger = logging.getLogger(__name__)


class ClaudeCodeJudge:
    """Judge that uses the ``claude`` CLI subprocess for evaluations.

    Works when running inside Claude Code (``claude`` on PATH).
    Uses the user's existing Claude Code subscription — no API key needed.
    """

    async def evaluate(
        self,
        prompt: str,
        response_text: str,
        rubric: str,
        *,
        threshold: float = 0.7,
    ) -> JudgeResult:
        """Ask the ``claude`` CLI to evaluate ``response_text`` against ``rubric``.

        Parameters
        ----------
        prompt:
            The original user input / question that produced the response.
        response_text:
            The agent's response text to evaluate.
        rubric:
            The evaluation rubric the judge should apply.
        threshold:
            Minimum ``score`` required for ``passed`` to be ``True``.
            Defaults to 0.7.
        """
        user_content = (
            f"## Evaluation Rubric\n{rubric}\n\n"
            f"## Original User Input\n{prompt}\n\n"
            f"## AI Assistant Response\n{response_text}\n\n"
            "Evaluate the response according to the rubric above. "
            "Respond with ONLY the JSON object."
        )

        start = time.perf_counter()

        try:
            process = await asyncio.create_subprocess_exec(
                "claude",
                "-p",
                user_content,
                "--output-format",
                "json",
                "--append-system-prompt",
                JUDGE_SYSTEM_PROMPT,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=30,
            )
        except FileNotFoundError:
            return JudgeResult(
                passed=False,
                score=0.0,
                reason=(
                    "claude CLI binary not found. Make sure you are running "
                    "inside Claude Code with the claude binary on PATH."
                ),
                model="claude-cli",
            )
        except TimeoutError:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return JudgeResult(
                passed=False,
                score=0.0,
                reason="claude CLI judge call timed out after 30 seconds.",
                model="claude-cli",
                latency_ms=elapsed_ms,
            )
        except OSError as exc:
            return JudgeResult(
                passed=False,
                score=0.0,
                reason=f"Failed to run claude CLI: {exc}",
                model="claude-cli",
            )

        elapsed_ms = (time.perf_counter() - start) * 1000

        stdout_text = stdout_bytes.decode(errors="replace").strip()
        stderr_text = stderr_bytes.decode(errors="replace").strip()

        if process.returncode != 0:
            detail = stderr_text or stdout_text or f"exit code {process.returncode}"
            return JudgeResult(
                passed=False,
                score=0.0,
                reason=f"claude CLI exited with error: {detail}",
                model="claude-cli",
                latency_ms=elapsed_ms,
                raw_response=stdout_text,
            )

        # Parse the outer JSON envelope from claude --output-format json
        # Expected shape: {"type": "result", "result": "...", "total_cost_usd": ...}
        try:
            outer = json.loads(stdout_text)
        except json.JSONDecodeError:
            logger.warning("claude CLI returned non-JSON output: %s", stdout_text[:200])
            return JudgeResult(
                passed=False,
                score=0.0,
                reason=f"claude CLI returned unparseable output: {stdout_text[:200]}",
                model="claude-cli",
                latency_ms=elapsed_ms,
                raw_response=stdout_text,
            )

        result_text = str(outer.get("result", ""))
        cost = float(outer.get("total_cost_usd", 0.0) or 0.0)

        return self._parse_inner(
            result_text,
            cost=cost,
            latency_ms=elapsed_ms,
            threshold=threshold,
            raw_outer=stdout_text,
        )

    # --------------------------------------------------------------------- #
    # Internal helpers
    # --------------------------------------------------------------------- #

    def _parse_inner(
        self,
        raw: str,
        *,
        cost: float,
        latency_ms: float,
        threshold: float,
        raw_outer: str,
    ) -> JudgeResult:
        """Parse the inner judge JSON from the ``result`` field."""
        text = raw.strip()

        # Strip markdown code fences if the model wrapped its answer
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning(
                "Claude CLI judge returned non-JSON inner response: %s",
                raw[:200],
            )
            return JudgeResult(
                passed=False,
                score=0.0,
                reason=f"Judge returned unparseable response: {raw[:200]}",
                cost_usd=cost,
                model="claude-cli",
                latency_ms=latency_ms,
                raw_response=raw_outer,
            )

        score = float(data.get("score", 0.0))
        score = max(0.0, min(1.0, score))  # clamp to [0, 1]

        # Use the judge's pass/fail if present, otherwise threshold-based
        passed = data.get("passed", score >= threshold)

        reason = str(data.get("reason", "No reason provided."))

        return JudgeResult(
            passed=bool(passed),
            score=score,
            reason=reason,
            cost_usd=cost,
            model="claude-cli",
            latency_ms=latency_ms,
            raw_response=raw_outer,
        )
