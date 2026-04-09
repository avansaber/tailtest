"""tailtest.llm — LLM client abstraction for the v0.1 build.

This package wraps the various ways tailtest can talk to an LLM:

- :mod:`tailtest.llm.claude_cli` — the :class:`ClaudeCodeJudge` class which
  uses the host ``claude`` CLI subprocess. Preferred when running inside
  Claude Code because it uses the user's existing subscription with zero
  marginal cost.

- :mod:`tailtest.llm.resolver` — judge model resolution helpers
  (:func:`is_claude_code_available`, :func:`resolve_judge_model`) that pick
  the best available LLM provider in this priority order:
  Claude Code CLI > Anthropic key > OpenAI key > Gemini key > Ollama
  > deterministic-only.

The full litellm-backed judge for non-Claude-Code flows is built out in
Phase 3 at :mod:`tailtest.core.assertions.llm_judge.judge`. In Phase 0
we only ship the transport layer; the assertion engine lands later.

Files copied verbatim from the v1 tailtest project per ADR 0006.
"""
