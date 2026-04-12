# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""AST-based domain signal extraction for the test generator (Phase 8 Task 8.2).

Reads a source file with ``ast.parse()`` (Python) or lightweight regex
(TypeScript/JavaScript) and extracts the domain vocabulary that tells the
LLM what types, exceptions, and function shapes exist -- without executing
or importing the file.

Design rules:
- NEVER exec or import the source file. AST/regex only.
- NEVER raise. Every failure path returns an empty ``DomainSignals``.
- All list caps are hard limits. The prompt token budget is tight.
"""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Known Enum base class names (unqualified).
_ENUM_BASES = frozenset(["Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"])

# Suffixes that mark exception subclasses.
_EXCEPTION_SUFFIXES = ("Error", "Exception", "Warning")

# Parameter names that indicate auth/actor involvement.
_AUTH_PARAM_NAMES = frozenset(["user", "role", "actor", "principal", "permissions"])

# Substrings in decorator names that indicate auth requirements.
_AUTH_DECORATOR_KEYWORDS = ("require", "permission")


@dataclass(frozen=True)
class DomainSignals:
    """Vocabulary extracted from a single source file.

    All list fields are capped at their per-field maximum so callers
    can render them directly without truncation logic.
    """

    enum_names: list[str] = field(default_factory=list)
    exception_names: list[str] = field(default_factory=list)
    top_class_names: list[str] = field(default_factory=list)
    public_function_signatures: list[str] = field(default_factory=list)
    has_auth_patterns: bool = False

    def is_empty(self) -> bool:
        """Return True when no signals were detected."""
        return (
            not self.enum_names
            and not self.exception_names
            and not self.top_class_names
            and not self.public_function_signatures
            and not self.has_auth_patterns
        )


_NUMERIC_TYPE_KEYWORDS = frozenset(["int", "float", "Decimal", "Amount"])

_COMMENT_PREFIX: dict[str, str] = {
    "python": "#",
    "typescript": "//",
    "javascript": "//",
    "rust": "//",
}


def build_detection_note(
    signals: DomainSignals,
    project_context: object | None,
    user_context: str | None,
    *,
    language: str = "python",
) -> str:
    """Build the line-2 detection note for a generated test file.

    Priority order:
    1. ``user_context`` (explicit ``--context`` override)
    2. Domain signals (enum/exception/class names found in the source)
    3. ``project_context.llm_summary`` (deep-scan project description)
    4. Fallback message when no context is available
    """
    prefix = _COMMENT_PREFIX.get(language, "#")

    if user_context:
        return f"{prefix} tailtest used your description: {user_context}"

    if not signals.is_empty():
        entities: list[str] = []
        entities.extend(signals.enum_names)
        entities.extend(signals.exception_names)
        entities.extend(signals.top_class_names)
        if not entities:
            # Only function signatures found -- use function names as domain vocabulary.
            entities = [sig.split("(")[0] for sig in signals.public_function_signatures[:3]]
        top = ", ".join(entities[:3])
        return f"{prefix} tailtest detected: {top} -- review before committing"

    if project_context is not None:
        summary = getattr(project_context, "llm_summary", None)
        if summary:
            return f"{prefix} tailtest context: {summary[:80]}"

    return f"{prefix} tailtest: no domain context available -- review generated tests carefully"


def build_category_hints(
    signals: DomainSignals,
    *,
    likely_vibe_coded: bool = False,
) -> list[str]:
    """Return a list of targeted test-category instructions for the LLM.

    Each hint is a concrete, actionable sentence. The list is capped at 5
    entries -- the first 5 that fire, in priority order.
    """
    hints: list[str] = []

    if signals.enum_names:
        names = ", ".join(signals.enum_names)
        hints.append(
            f"For each Enum found ({names}), generate at least one test asserting "
            "that an invalid value raises an error or is rejected."
        )

    # State-transition hint: only when >= 3 enum types exist AND a function
    # actually takes one of those enum types as a parameter.
    if len(signals.enum_names) >= 3:
        enum_set = set(signals.enum_names)
        sig_text = " ".join(signals.public_function_signatures)
        if any(name in sig_text for name in enum_set):
            hints.append(
                "Generate at least one test that verifies an invalid state transition "
                "is rejected (e.g., a terminal state cannot transition back to a draft state)."
            )

    if signals.exception_names:
        names = ", ".join(signals.exception_names)
        hints.append(
            f"For each exception class found ({names}), generate at least one test "
            "that asserts it is raised under the expected condition."
        )

    sig_text = " ".join(signals.public_function_signatures)
    if any(kw in sig_text for kw in _NUMERIC_TYPE_KEYWORDS):
        hints.append(
            "For numeric parameters, include at least one boundary test with a zero "
            "value and one with a negative value."
        )

    if signals.has_auth_patterns:
        hints.append(
            "The function signature suggests authorization is involved. "
            "Include at least one test for an unauthorized caller."
        )

    if likely_vibe_coded and not signals.enum_names and not signals.exception_names:
        hints.append(
            "This appears to be a new project without established domain types. "
            "Focus on happy-path coverage and at least one edge case per parameter."
        )

    return hints[:5]


def cluster_domain_from_source_tree(
    source: Path,
    project_root: Path,
    *,
    max_files: int = 20,
) -> str | None:
    """Infer the dominant domain from sibling source files.

    Walks sibling ``.py`` files in the same directory as ``source``
    (one level only -- no recursion), collects all public function names,
    groups by common prefix (first 1-2 underscore-separated segments),
    and returns a compact description of the largest cluster.

    Returns ``None`` when fewer than 3 sibling files exist or when no
    prefix cluster has >= 3 members. Never raises.
    """
    try:
        return _cluster_inner(source, project_root, max_files=max_files)
    except Exception:  # noqa: BLE001
        logger.debug("cluster_domain_from_source_tree failed", exc_info=True)
        return None


def _cluster_inner(source: Path, project_root: Path, *, max_files: int) -> str | None:
    sibling_dir = source.parent
    siblings = [
        p
        for p in sibling_dir.iterdir()
        if p.is_file() and p.suffix == ".py" and p != source
    ]
    if len(siblings) < 3:
        return None

    # Collect public function names from up to max_files siblings.
    all_names: list[str] = []
    for sib in siblings[:max_files]:
        try:
            text = sib.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()[:50]
            tree = ast.parse("\n".join(lines))
        except (OSError, SyntaxError):
            continue
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    all_names.append(node.name)

    if not all_names:
        return None

    # Group by prefix (first 1-2 segments when split on "_").
    prefix_map: dict[str, list[str]] = {}
    for name in all_names:
        parts = name.split("_")
        key = "_".join(parts[:2]) if len(parts) >= 3 else parts[0]
        prefix_map.setdefault(key, []).append(name)

    # Find the largest cluster.
    best_key, best_members = max(prefix_map.items(), key=lambda kv: len(kv[1]))
    if len(best_members) < 3:
        return None

    examples = ", ".join(best_members[:3])
    return f"{best_key}: {len(best_members)} functions ({examples})"


def extract_domain_signals(source: Path) -> DomainSignals:
    """Extract domain vocabulary from ``source``.

    Dispatches to the Python AST extractor for ``.py`` files and to a
    regex-based extractor for TypeScript/JavaScript. Returns an empty
    ``DomainSignals`` on any read or parse error -- never raises.
    """
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.debug("ast_signals: could not read %s: %s", source, exc)
        return DomainSignals()

    suffix = source.suffix.lower()
    if suffix == ".py":
        return _extract_python(text)
    if suffix in {".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"}:
        return _extract_js_ts(text)
    return DomainSignals()


# ---------------------------------------------------------------------------
# Python extractor (AST)
# ---------------------------------------------------------------------------


def _extract_python(text: str) -> DomainSignals:
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        logger.debug("ast_signals: Python parse error: %s", exc)
        return DomainSignals()

    enum_names: list[str] = []
    exception_names: list[str] = []
    top_class_names: list[str] = []
    signatures: list[str] = []
    has_auth = False

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            if _is_enum_class(node):
                if len(enum_names) < 8:
                    enum_names.append(node.name)
            elif _is_exception_class(node):
                if len(exception_names) < 5:
                    exception_names.append(node.name)
            else:
                if len(top_class_names) < 5:
                    top_class_names.append(node.name)

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_"):
                continue
            if not has_auth and (_has_auth_params(node) or _has_auth_decorators(node)):
                has_auth = True
            if len(signatures) < 5:
                sig = _format_py_signature(node)
                if sig:
                    signatures.append(sig[:80])

    return DomainSignals(
        enum_names=enum_names,
        exception_names=exception_names,
        top_class_names=top_class_names,
        public_function_signatures=signatures,
        has_auth_patterns=has_auth,
    )


def _is_enum_class(node: ast.ClassDef) -> bool:
    return any(_base_name(b) in _ENUM_BASES for b in node.bases)


def _is_exception_class(node: ast.ClassDef) -> bool:
    for base in node.bases:
        name = _base_name(base)
        if name == "Exception" or any(name.endswith(s) for s in _EXCEPTION_SUFFIXES):
            return True
    return False


def _base_name(base: ast.expr) -> str:
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return ""


def _has_auth_params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(arg.arg in _AUTH_PARAM_NAMES for arg in node.args.args)


def _has_auth_decorators(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for dec in node.decorator_list:
        name = _decorator_name(dec)
        if any(kw in name.lower() for kw in _AUTH_DECORATOR_KEYWORDS):
            return True
    return False


def _decorator_name(dec: ast.expr) -> str:
    if isinstance(dec, ast.Name):
        return dec.id
    if isinstance(dec, ast.Attribute):
        return dec.attr
    if isinstance(dec, ast.Call):
        return _decorator_name(dec.func)
    return ""


def _format_py_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Return a compact ``name(param: Type, ...)`` string for ``node``."""
    parts: list[str] = []
    for arg in node.args.args:
        if arg.arg in ("self", "cls"):
            continue
        if arg.annotation is not None:
            try:
                ann = ast.unparse(arg.annotation)
            except Exception:  # noqa: BLE001
                ann = ""
            parts.append(f"{arg.arg}: {ann}" if ann else arg.arg)
        else:
            parts.append(arg.arg)
    return f"{node.name}({', '.join(parts)})"


# ---------------------------------------------------------------------------
# TypeScript/JavaScript extractor (regex)
# ---------------------------------------------------------------------------


def _extract_js_ts(text: str) -> DomainSignals:
    enum_names: list[str] = []
    top_class_names: list[str] = []
    signatures: list[str] = []

    for m in re.finditer(r"\benum\s+([A-Za-z_]\w*)", text):
        if len(enum_names) < 8:
            enum_names.append(m.group(1))

    for m in re.finditer(r"\bclass\s+([A-Za-z_]\w*)", text):
        if len(top_class_names) < 5:
            top_class_names.append(m.group(1))

    for m in re.finditer(r"\bfunction\s+([A-Za-z_]\w*)\s*\(([^)]*)\)", text):
        name = m.group(1)
        if name.startswith("_"):
            continue
        params = m.group(2).strip()
        sig = f"{name}({params})"[:80]
        if len(signatures) < 5:
            signatures.append(sig)

    return DomainSignals(
        enum_names=enum_names,
        exception_names=[],
        top_class_names=top_class_names,
        public_function_signatures=signatures,
        has_auth_patterns=False,
    )
