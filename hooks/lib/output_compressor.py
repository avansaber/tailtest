"""Compress verbose text output to a concise summary.

Designed for test runner output passed as text -- strips boilerplate,
keeps failure lines. Also used to cap last_failures context length.
"""

from __future__ import annotations

_KEEP_PATTERNS = (
    "FAILED",
    "PASSED",
    "ERROR",
    "AssertionError",
    "assert ",
    "Expected",
    "Received",
    "assert_",
    "TypeError",
    "ValueError",
    "KeyError",
    "AttributeError",
)

_MAX_LINES = 50


def compress_output(text: str, max_lines: int = _MAX_LINES) -> str:
    """Return a compressed version of test runner output.

    Keeps lines that contain failure signals. If total lines are already
    under max_lines, returns unchanged. Appends a truncation note if lines
    were removed.
    """
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text

    kept = [line for line in lines if any(p in line for p in _KEEP_PATTERNS)]

    if not kept:
        kept = lines[:max_lines]
        return "\n".join(kept) + f"\n[...truncated {len(lines) - max_lines} lines]"

    if len(kept) > max_lines:
        kept = kept[:max_lines]

    removed = len(lines) - len(kept)
    return "\n".join(kept) + (f"\n[...{removed} verbose lines omitted]" if removed > 0 else "")
