"""Judge model resolver — detects the best available LLM for judging.

Auto-detection checks, in priority order:

1. ``ANTHROPIC_API_KEY`` set → ``claude-haiku-4-5-20251001`` (cheapest Anthropic)
2. ``OPENAI_API_KEY`` set → ``gpt-4o-mini`` (cheapest OpenAI)
3. ``GEMINI_API_KEY`` or ``GOOGLE_API_KEY`` set → ``gemini/gemini-2.0-flash``
4. Ollama running on localhost:11434 → ``ollama/llama3.2``
5. None → return ``None`` (deterministic-only mode)

When running inside Claude Code (``claude`` on PATH), the
:class:`~tailtest.llm.claude_cli.ClaudeCodeJudge` is used instead of
litellm — see :func:`is_claude_code_available`.

Copied verbatim from the v1 tailtest project per ADR 0006.
"""

from __future__ import annotations

import logging
import os
import shutil

logger = logging.getLogger(__name__)


def is_claude_code_available() -> bool:
    """Check if the ``claude`` CLI is usable for LLM-first operations.

    v0.3.1 change (Feynman case study 2026-04-07): only requires ``claude``
    on PATH. The v0.3.0 implementation ALSO required ``CLAUDECODE=1`` in
    the environment, which broke the documented fresh-install happy path:
    a user who ran ``pip install tailtester`` and then ``tailtest scan .``
    from a plain terminal would get "Claude Code CLI not available" even
    with ``claude`` authenticated on PATH. The env var is only set
    automatically *inside* a Claude Code session, so the v0.3.0 check
    silently restricted the scanner to one environment.

    The check now returns ``True`` if ``claude`` is on PATH. The
    ``CLAUDECODE`` env var is no longer inspected.
    """
    return shutil.which("claude") is not None


def resolve_judge_model() -> str | None:
    """Detect the best available LLM for judging.

    Priority order:

    1. ``ANTHROPIC_API_KEY`` set → ``claude-haiku-4-5-20251001``
    2. ``OPENAI_API_KEY`` set → ``gpt-4o-mini``
    3. ``GEMINI_API_KEY`` or ``GOOGLE_API_KEY`` set → ``gemini/gemini-2.0-flash``
    4. Ollama running on localhost:11434 → ``ollama/llama3.2``
    5. None → return ``None`` (deterministic-only mode)

    Returns:
        A litellm model identifier string, or ``None`` if no LLM is available.
    """
    # 1. Anthropic
    if os.environ.get("ANTHROPIC_API_KEY"):
        logger.debug("Resolved judge model: claude-haiku-4-5-20251001 (ANTHROPIC_API_KEY set)")
        return "claude-haiku-4-5-20251001"

    # 2. OpenAI
    if os.environ.get("OPENAI_API_KEY"):
        logger.debug("Resolved judge model: gpt-4o-mini (OPENAI_API_KEY set)")
        return "gpt-4o-mini"

    # 3. Gemini
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        logger.debug("Resolved judge model: gemini/gemini-2.0-flash (Gemini API key set)")
        return "gemini/gemini-2.0-flash"

    # 4. Ollama (local) — quick HTTP check with short timeout
    if _is_ollama_running():
        logger.debug("Resolved judge model: ollama/llama3.2 (Ollama running on localhost)")
        return "ollama/llama3.2"

    # 5. Nothing available
    logger.debug("No LLM provider detected for judge model resolution")
    return None


def _is_ollama_running() -> bool:
    """Check if Ollama is running on localhost:11434 with a short timeout."""
    try:
        import httpx

        with httpx.Client(timeout=0.5) as client:
            resp = client.get("http://localhost:11434")
            return resp.status_code == 200
    except Exception:
        return False
