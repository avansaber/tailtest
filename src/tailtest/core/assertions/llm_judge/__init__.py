"""tailtest.core.assertions.llm_judge — LLM-judge assertions.

Phase 0: only a minimal ``judge`` stub module exists, so that the
copied ``tailtest.llm.claude_cli`` module imports cleanly. Phase 3
adds LLMJudge and JudgmentResult for agent output validation.
"""

from tailtest.core.assertions.llm_judge.llm_judge import JudgmentResult, LLMJudge

__all__ = ["LLMJudge", "JudgmentResult"]
