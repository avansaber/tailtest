"""Tests for the validator subagent system prompt (Phase 5 Task 5.1).

Validates that:
- The prompt file exists and is non-empty.
- The YAML frontmatter parses correctly with required fields.
- Tool restrictions are explicitly stated in the prompt body.
- The output format section defines the expected JSON schema.
- The prompt body is within the ~500-word design budget.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Path is relative to the repo root. Tests run from the repo root.
AGENTS_DIR = Path(__file__).parent.parent / "agents"
VALIDATOR_MD = AGENTS_DIR / "validator.md"


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split YAML frontmatter from body. Returns (fields, body)."""
    if not text.startswith("---"):
        return {}, text
    end = text.index("---", 3)
    fm_block = text[3:end].strip()
    body = text[end + 3 :].strip()
    fields: dict[str, str] = {}
    for line in fm_block.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fields[k.strip()] = v.strip()
    return fields, body


@pytest.fixture(scope="module")
def validator_text() -> str:
    assert VALIDATOR_MD.exists(), f"agents/validator.md not found at {VALIDATOR_MD}"
    return VALIDATOR_MD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def frontmatter(validator_text: str) -> dict[str, str]:
    fm, _ = _parse_frontmatter(validator_text)
    return fm


@pytest.fixture(scope="module")
def body(validator_text: str) -> str:
    _, b = _parse_frontmatter(validator_text)
    return b


# --- Existence and basic structure ---


def test_validator_md_exists() -> None:
    assert VALIDATOR_MD.exists()


def test_validator_md_non_empty(validator_text: str) -> None:
    assert len(validator_text.strip()) > 100


def test_has_frontmatter(validator_text: str) -> None:
    assert validator_text.startswith("---"), "File must start with YAML frontmatter"


# --- Frontmatter fields ---


def test_frontmatter_name(frontmatter: dict[str, str]) -> None:
    assert frontmatter.get("name") == "tailtest-validator"


def test_frontmatter_description(frontmatter: dict[str, str]) -> None:
    desc = frontmatter.get("description", "")
    assert "Validates" in desc or "validates" in desc
    assert "Read-only" in desc or "read-only" in desc or "Read only" in desc


def test_frontmatter_tools(frontmatter: dict[str, str]) -> None:
    tools = frontmatter.get("tools", "")
    for required in ("Read", "Grep", "Glob", "Bash"):
        assert required in tools, f"tools field missing {required!r}"


def test_frontmatter_no_write_in_tools(frontmatter: dict[str, str]) -> None:
    tools = frontmatter.get("tools", "")
    for forbidden in ("Write", "Edit", "MultiEdit"):
        assert forbidden not in tools, f"tools field must NOT contain {forbidden!r}"


def test_frontmatter_model(frontmatter: dict[str, str]) -> None:
    model = frontmatter.get("model", "")
    assert model, "model field must be set"


def test_frontmatter_mcp_servers(frontmatter: dict[str, str]) -> None:
    mcp = frontmatter.get("mcpServers", "")
    assert "tailtest" in mcp


# --- Body: tool constraints ---


def test_body_explicitly_forbids_write(body: str) -> None:
    # The prompt must tell the validator it cannot use Write/Edit.
    assert "Write" in body and "Edit" in body, (
        "Body must explicitly name Write and Edit as forbidden tools"
    )


def test_body_says_not_modify_files(body: str) -> None:
    lower = body.lower()
    assert "not" in lower and ("modif" in lower or "fix" in lower or "write" in lower)


# --- Body: output format ---


def test_body_has_json_output_format(body: str) -> None:
    assert "json" in body.lower(), "Body must describe a JSON output format"


def test_body_has_severity_field(body: str) -> None:
    assert '"severity"' in body or "severity" in body


def test_body_has_reasoning_field(body: str) -> None:
    assert '"reasoning"' in body or "reasoning" in body


def test_body_has_confidence_field(body: str) -> None:
    assert '"confidence"' in body or "confidence" in body


def test_body_has_fix_suggestion_field(body: str) -> None:
    assert '"fix_suggestion"' in body or "fix_suggestion" in body


def test_body_defines_severity_values(body: str) -> None:
    for level in ("critical", "high", "medium", "low"):
        assert level in body, f"Severity level {level!r} not mentioned in body"


# --- Body: process / role ---


def test_body_establishes_role(body: str) -> None:
    lower = body.lower()
    assert "validator" in lower or "jiminy" in lower or "verify" in lower


def test_body_has_ordered_process(body: str) -> None:
    # Must have at least steps 1-3 enumerated.
    assert re.search(r"\b1\.", body) and re.search(r"\b2\.", body) and re.search(r"\b3\.", body)


# --- Word count sanity ---


def test_body_word_count_within_budget(body: str) -> None:
    word_count = len(body.split())
    # Design budget is ~500 words; allow up to 800 for growth.
    assert word_count <= 800, f"Body is {word_count} words; trim to ≤800"
    assert word_count >= 100, f"Body is only {word_count} words; too sparse"
