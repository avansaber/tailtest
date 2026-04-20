"""Per-file complexity scoring for H1.

Scores a source file based on path signals and content patterns.
The score drives a per-file depth recommendation that overrides the
session-level depth config for high-complexity files.

Score bands:
  0-5   low    -> simple depth  (2-3 scenarios)
  6-9   medium -> standard depth (5-8 scenarios)
  10+   high   -> thorough depth (10-15 scenarios); reasoning shown to agent
"""

from __future__ import annotations

import os
import re

# Path-name signals (checked against the lowercased filename + directory components)
_PATH_HIGH = ("auth", "permission", "billing", "payment", "checkout", "invoice", "subscription")
_PATH_MED = ("admin", "upload", "delete", "remove", "purge", "migrate")

# Content keyword patterns
_HTTP_PATTERNS = re.compile(
    r"\b(requests\.(get|post|put|patch|delete)|fetch\(|axios\.|http\.client|urllib|aiohttp|httpx)",
    re.IGNORECASE,
)
_DB_PATTERNS = re.compile(
    r"\b(\.execute\(|\.query\(|Model\.objects|\.filter\(|\.save\(|\.commit\(|\.session\.|cursor\.|"
    r"SELECT\s|INSERT\s|UPDATE\s|DELETE\s)",
    re.IGNORECASE,
)
_BRANCH_PATTERN = re.compile(r"\b(if |elif |else:|match |case |switch\s*\()", re.MULTILINE)
_PUBLIC_FUNC_PYTHON = re.compile(r"^def [a-z][a-z0-9_]*\(", re.MULTILINE)
_PUBLIC_FUNC_TS = re.compile(
    r"(^export\s+(async\s+)?function\s+\w+|^\s*public\s+(async\s+)?\w+\s*\()", re.MULTILINE
)

_MAX_BRANCHES = 4   # cap contribution from branches
_MAX_FUNCTIONS = 5  # cap contribution from public functions
_MAX_CONTENT_READ = 8000  # bytes -- avoid reading huge generated files


def score_file(file_path: str) -> tuple[int, str]:
    """Score a file for complexity.

    Returns (score, reasoning_string).
    reasoning_string is non-empty only when score >= 10.
    """
    norm_path = file_path.replace("\\", "/").lower()
    components = norm_path.split("/")
    name_stem = os.path.splitext(components[-1])[0]
    all_parts = " ".join(components)

    score = 0
    reasons: list[str] = []

    # Path signals
    for signal in _PATH_HIGH:
        if signal in all_parts:
            score += 4
            reasons.append(f"+4 {signal}")
            break  # only count once per band

    for signal in _PATH_MED:
        if signal in all_parts:
            score += 3
            reasons.append(f"+3 {signal}")
            break

    # Content scan
    try:
        with open(file_path, "r", errors="ignore") as fh:
            content = fh.read(_MAX_CONTENT_READ)
    except OSError:
        content = ""

    if content:
        http_hits = len(_HTTP_PATTERNS.findall(content))
        if http_hits > 0:
            score += 3
            reasons.append("+3 HTTP")

        db_hits = len(_DB_PATTERNS.findall(content))
        if db_hits > 0:
            score += 3
            reasons.append("+3 DB")

        branch_hits = min(len(_BRANCH_PATTERN.findall(content)), _MAX_BRANCHES)
        if branch_hits > 0:
            score += branch_hits
            reasons.append(f"+{branch_hits} branch{'es' if branch_hits > 1 else ''}")

        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".py":
            func_hits = min(len(_PUBLIC_FUNC_PYTHON.findall(content)), _MAX_FUNCTIONS)
        else:
            func_hits = min(len(_PUBLIC_FUNC_TS.findall(content)), _MAX_FUNCTIONS)
        if func_hits > 0:
            score += func_hits
            reasons.append(f"+{func_hits} function{'s' if func_hits > 1 else ''}")

    depth, _ = score_to_depth(score)
    reasoning = ""
    if score >= 10 and reasons:
        reasoning = f"{name_stem}: {' '.join(reasons)} = {score} scenarios"

    return score, reasoning


def score_to_depth(score: int) -> tuple[str, int]:
    """Map score to (depth_label, scenario_count)."""
    if score >= 10:
        return "thorough", min(score, 15)
    if score >= 6:
        return "standard", min(score, 8)
    return "simple", max(score, 2)


def complexity_context_note(file_path: str, configured_depth: str) -> str:
    """Return a depth hint to include in the context note for a queued file.

    Returns empty string if complexity matches the configured depth (no override needed).
    """
    score, reasoning = score_file(file_path)
    computed_depth, scenario_count = score_to_depth(score)

    depth_rank = {"simple": 0, "standard": 1, "thorough": 2}
    configured_rank = depth_rank.get(configured_depth, 1)
    computed_rank = depth_rank.get(computed_depth, 1)

    if computed_rank <= configured_rank:
        # No override -- configured depth is already sufficient
        return ""

    if reasoning:
        return f"Complexity: {computed_depth} ({reasoning}). Generate ~{scenario_count} scenarios."
    return f"Complexity: {computed_depth}. Generate ~{scenario_count} scenarios."
