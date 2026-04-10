"""Tests for LLMJudge assertions and judgment cache behavior.

All tests mock _call_claude to avoid real API calls.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tailtest.core.assertions.llm_judge import LLMJudge
from tailtest.hook.session_start import _reap_expired_judgments

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_judge(tmp_path: Path) -> LLMJudge:
    return LLMJudge(tmp_path)


def _raw_response(verdict: str, reasoning: str) -> str:
    return json.dumps({"verdict": verdict, "reasoning": reasoning})


# ---------------------------------------------------------------------------
# check_faithfulness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_faithfulness_pass(tmp_path: Path) -> None:
    judge = _make_judge(tmp_path)
    raw = _raw_response("pass", "The response directly addresses the intent.")
    with patch.object(judge, "_call_claude", new=AsyncMock(return_value=raw)):
        result = await judge.check_faithfulness("The answer is 42.", "What is the answer?")
    assert result.verdict == "pass"
    assert result.assertion_kind == "faithfulness"
    assert "directly addresses" in result.reasoning
    assert result.cached is False


@pytest.mark.asyncio
async def test_faithfulness_fail(tmp_path: Path) -> None:
    judge = _make_judge(tmp_path)
    raw = _raw_response("fail", "Response ignores the stated intent completely.")
    with patch.object(judge, "_call_claude", new=AsyncMock(return_value=raw)):
        result = await judge.check_faithfulness("Unrelated answer.", "Ask about weather.")
    assert result.verdict == "fail"
    assert result.assertion_kind == "faithfulness"


# ---------------------------------------------------------------------------
# check_pii_leakage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pii_leakage_pass(tmp_path: Path) -> None:
    judge = _make_judge(tmp_path)
    raw = _raw_response("pass", "No PII found in the text.")
    with patch.object(judge, "_call_claude", new=AsyncMock(return_value=raw)):
        result = await judge.check_pii_leakage("The stock market rose today.")
    assert result.verdict == "pass"
    assert result.assertion_kind == "pii_leakage"


@pytest.mark.asyncio
async def test_pii_leakage_fail(tmp_path: Path) -> None:
    judge = _make_judge(tmp_path)
    raw = _raw_response("fail", "Email address detected in text.")
    with patch.object(judge, "_call_claude", new=AsyncMock(return_value=raw)):
        result = await judge.check_pii_leakage("Contact user@example.com for details.")
    assert result.verdict == "fail"
    assert result.assertion_kind == "pii_leakage"


# ---------------------------------------------------------------------------
# check_tool_call_correctness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_call_correctness_uncertain(tmp_path: Path) -> None:
    judge = _make_judge(tmp_path)
    raw = _raw_response("uncertain", "Cannot determine if this is correct without more context.")
    with patch.object(judge, "_call_claude", new=AsyncMock(return_value=raw)):
        result = await judge.check_tool_call_correctness(
            "Edit", {"file_path": "/tmp/foo.py"}, "Editing a source file."
        )
    assert result.verdict == "uncertain"
    assert result.assertion_kind == "tool_call_correctness"


# ---------------------------------------------------------------------------
# Cache hit: second call does not invoke LLM
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_hit_no_second_llm_call(tmp_path: Path) -> None:
    judge = _make_judge(tmp_path)
    raw = _raw_response("pass", "Looks good.")
    mock_call = AsyncMock(return_value=raw)
    with patch.object(judge, "_call_claude", new=mock_call):
        result1 = await judge.check_faithfulness("Answer.", "Question?")
    assert result1.cached is False
    assert mock_call.call_count == 1

    # Second call with same args -- LLM should NOT be called again
    mock_call2 = AsyncMock(return_value=raw)
    with patch.object(judge, "_call_claude", new=mock_call2):
        result2 = await judge.check_faithfulness("Answer.", "Question?")
    assert result2.cached is True
    assert result2.verdict == "pass"
    mock_call2.assert_not_called()


# ---------------------------------------------------------------------------
# Cache miss on expired file: LLM called again
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_miss_on_expired_file(tmp_path: Path) -> None:
    judge = _make_judge(tmp_path)
    raw = _raw_response("pass", "Fine.")
    mock_call = AsyncMock(return_value=raw)

    # First call writes cache
    with patch.object(judge, "_call_claude", new=mock_call):
        await judge.check_pii_leakage("Clean text here.")
    assert mock_call.call_count == 1

    # Make the cache file look old (beyond TTL)
    cache_dir = tmp_path / ".tailtest" / "cache" / "judgments"
    for p in cache_dir.glob("*.json"):
        old_time = time.time() - 86401  # just over 24h
        import os

        os.utime(p, (old_time, old_time))

    # Second call should invoke LLM again because cache is expired
    mock_call2 = AsyncMock(return_value=raw)
    with patch.object(judge, "_call_claude", new=mock_call2):
        result2 = await judge.check_pii_leakage("Clean text here.")
    assert mock_call2.call_count == 1
    assert result2.cached is False


# ---------------------------------------------------------------------------
# Cache miss when no file: LLM called, file written
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_miss_no_file_calls_llm_and_writes(tmp_path: Path) -> None:
    judge = _make_judge(tmp_path)
    raw = _raw_response("pass", "All good.")
    mock_call = AsyncMock(return_value=raw)

    cache_dir = tmp_path / ".tailtest" / "cache" / "judgments"
    assert not cache_dir.exists()

    with patch.object(judge, "_call_claude", new=mock_call):
        result = await judge.check_pii_leakage("No PII here.")

    mock_call.assert_called_once()
    assert result.verdict == "pass"
    assert cache_dir.exists()
    written_files = list(cache_dir.glob("*.json"))
    assert len(written_files) == 1


# ---------------------------------------------------------------------------
# LLM unavailable (FileNotFoundError) -> uncertain, does not raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_unavailable_returns_uncertain(tmp_path: Path) -> None:
    judge = _make_judge(tmp_path)

    async def _raise_file_not_found(*_args, **_kwargs):
        raise RuntimeError("claude CLI not found -- LLM judge requires Claude Code")

    with patch.object(judge, "_call_claude", new=_raise_file_not_found):
        result = await judge.check_faithfulness("Any response.", "Any intent.")

    assert result.verdict == "uncertain"
    assert "Judge unavailable" in result.reasoning
    assert result.assertion_kind == "faithfulness"


# ---------------------------------------------------------------------------
# LLM non-zero exit -> uncertain, does not raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_nonzero_exit_returns_uncertain(tmp_path: Path) -> None:
    judge = _make_judge(tmp_path)

    async def _raise_nonzero(*_args, **_kwargs):
        raise RuntimeError("claude -p exited 1: some error")

    with patch.object(judge, "_call_claude", new=_raise_nonzero):
        result = await judge.check_pii_leakage("Some text.")

    assert result.verdict == "uncertain"
    assert "Judge unavailable" in result.reasoning


# ---------------------------------------------------------------------------
# Malformed JSON response -> uncertain with parse error in reasoning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_json_response_returns_uncertain(tmp_path: Path) -> None:
    judge = _make_judge(tmp_path)
    with patch.object(judge, "_call_claude", new=AsyncMock(return_value="not valid json at all")):
        result = await judge.check_tool_call_correctness("Edit", {}, "context")

    assert result.verdict == "uncertain"
    assert "Could not parse judge response" in result.reasoning


# ---------------------------------------------------------------------------
# _reap_expired_judgments: expired files deleted, fresh files kept
# ---------------------------------------------------------------------------


def test_reap_expired_deletes_old_keeps_fresh(tmp_path: Path) -> None:
    import os

    cache_dir = tmp_path / ".tailtest" / "cache" / "judgments"
    cache_dir.mkdir(parents=True)

    expired = cache_dir / "expired.json"
    fresh = cache_dir / "fresh.json"
    expired.write_text(json.dumps({"verdict": "pass", "reasoning": "x", "assertion_kind": "y"}))
    fresh.write_text(json.dumps({"verdict": "pass", "reasoning": "x", "assertion_kind": "y"}))

    # Make expired look old
    old_time = time.time() - 86401
    os.utime(expired, (old_time, old_time))

    _reap_expired_judgments(tmp_path)

    assert not expired.exists()
    assert fresh.exists()


# ---------------------------------------------------------------------------
# _reap_expired_judgments: missing cache dir is not an error
# ---------------------------------------------------------------------------


def test_reap_missing_dir_is_not_an_error(tmp_path: Path) -> None:
    # Should not raise even if the judgments dir does not exist
    _reap_expired_judgments(tmp_path)
