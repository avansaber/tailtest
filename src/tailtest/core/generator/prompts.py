# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""Prompt templates for the test generator (Phase 1 Task 1.12b).

Each supported language has:
- A ``system`` prompt telling the model what role to play and what
  output format to use.
- A ``build_user_prompt`` function that wraps the source file text
  with language-specific instructions (framework choice, mandatory
  header, assertion requirements).

The templates are deliberately short. A longer template pulls the
model toward elaborate scaffolding that usually fails the per-language
compile check. A short template with one explicit rule per line
performs better on the compile gate in practice.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tailtest.core.generator.ast_signals import DomainSignals

logger = logging.getLogger(__name__)

_PROMPT_WARN_CHARS = 7_500


@dataclass(frozen=True)
class ProjectContext:
    """Project-level context injected into the test-generation prompt.

    Produced by ``TestGenerator._load_project_context()`` from the
    project's ``.tailtest/profile.json``. All fields are optional --
    a shallow scan (no deep scan) leaves ``llm_summary`` as None.
    """

    llm_summary: str | None = None
    primary_language: str | None = None
    runner: str | None = None  # first detected runner name
    framework_category: str = ""  # "" means no framework detected
    likely_vibe_coded: bool = False
    tests_dirs: list[Path] = field(default_factory=list)


def _build_project_context_block(
    ctx: ProjectContext | None,
    signals: DomainSignals | None = None,
    domain_cluster: str | None = None,
) -> str:
    """Render the ``## Project context`` section for the user prompt.

    Returns an empty string when both ``ctx`` and ``signals`` are empty.
    Format is compact key-value lines to minimise token usage. Domain
    signals appear as a subsection at the end of the block.
    """
    lines: list[str] = []

    if ctx is not None:
        if ctx.llm_summary:
            lines.append(f"summary: {ctx.llm_summary[:300]}")
        if ctx.primary_language:
            lines.append(f"language: {ctx.primary_language}")
        if ctx.runner:
            lines.append(f"runner: {ctx.runner}")
        if ctx.framework_category:
            lines.append(f"framework_category: {ctx.framework_category}")
        lines.append(f"vibe_coded: {str(ctx.likely_vibe_coded).lower()}")

    if signals is not None and not signals.is_empty():
        if signals.enum_names:
            lines.append(f"Enums: {', '.join(signals.enum_names)}")
        if signals.exception_names:
            lines.append(f"Exceptions: {', '.join(signals.exception_names)}")
        key_types = [
            t
            for t in signals.top_class_names
            if t not in signals.enum_names and t not in signals.exception_names
        ]
        if key_types:
            lines.append(f"Key types: {', '.join(key_types)}")

    if domain_cluster:
        lines.append(f"Domain cluster (inferred from sibling files): {domain_cluster}")

    if not lines:
        return ""
    return "## Project context\n" + "\n".join(lines) + "\n"


SYSTEM_PROMPT = """You generate starter tests for source files.

Rules you must follow:
1. Output ONLY the test file contents. No markdown fencing, no prose, no explanations before or after.
2. Include the mandatory header on line 1 exactly as given in the user prompt.
3. Write at least one assertion that tests behavior, not implementation details.
4. Prefer tests that compile and pass over tests that fail. A passing test scaffold is more useful than a failing one because the user can expand from there.
5. Do not import modules that are not in the source file's imports or in the standard library for the target language. Do not assume the presence of helper libraries.
6. Do not wrap your output in triple backticks or any other markdown syntax.
7. Do not include comments that reference the generation process itself beyond the mandatory header.

You will be given the source file text and the target framework. Emit valid test code in that framework.
"""


RUST_SYSTEM_PROMPT = """You generate Rust test code for source files.

Rules you must follow:
1. Include the mandatory header comment on line 1 exactly as given in the user prompt.
2. Write at least one assertion using assert!, assert_eq!, or assert_ne! macros.
3. Do not wrap your output in triple backticks or any other markdown syntax.
4. Do not include comments that reference the generation process beyond the mandatory header.
5. Do not import external crates not already present in the source file's Cargo.toml.
6. For colocated style: output ONLY the #[cfg(test)] mod tests { ... } block. Do NOT include the surrounding source code.
7. For integration style: output a complete .rs file starting with the mandatory header comment.
8. All test functions must be annotated with #[test].
9. Prefer tests that compile and pass over tests that detect bugs, because a green scaffold is more useful as a starting point.
"""


def build_rust_user_prompt(
    *,
    source_path: str,
    source_text: str,
    style: str,
    crate_name: str,
    scope: str,
) -> str:
    """Assemble the user message for Rust test generation.

    ``style`` is either ``"colocated"`` (append a ``#[cfg(test)] mod tests``
    block to the source file) or ``"integration"`` (create a new file in
    ``tests/``).
    """
    scope_note = (
        "Cover every public function in this file."
        if scope == "module"
        else "Cover the first public function in this file."
    )
    header = "// generated by tailtest - review before committing"

    if style == "colocated":
        style_instructions = (
            "Output ONLY the #[cfg(test)] mod tests block -- "
            "do NOT include any of the surrounding source code shown below. "
            "Start with `use super::*;` inside the mod block to import the parent module."
        )
    else:
        style_instructions = (
            f"Output a complete integration test file. "
            f"Import from the crate with `use {crate_name}::*;` or specific paths."
        )

    return f"""Generate Rust tests for this source file.

## Source file path
{source_path}

## Crate name
{crate_name}

## Test style
{style}: {style_instructions}

## Scope
{scope_note}

## Mandatory header comment
The first line of your output MUST be exactly:
{header}

## Source file contents
```rust
{source_text}
```

## Your output
Emit ONLY the test code ({"#[cfg(test)] mod tests block" if style == "colocated" else "integration test file"}), starting with the mandatory header comment above. No markdown fences. No explanations.
"""


def build_user_prompt(
    *,
    source_path: str,
    source_text: str,
    language: str,
    framework: str,
    header_line: str,
    scope: str,
    project_context: ProjectContext | None = None,
    domain_signals: DomainSignals | None = None,
    category_hints: list[str] | None = None,
    test_style_sample: str | None = None,
    domain_cluster: str | None = None,
) -> str:
    """Assemble the user message passed to ``claude -p``.

    Parameters match ``TestGenerator.generate()``. ``scope`` is either
    ``"module"`` (cover the whole file) or ``"function"`` (cover a
    single exported entry point).

    When ``project_context`` or ``domain_signals`` are provided, a
    ``## Project context`` block is inserted before the source file
    contents to help the model produce domain-aware tests.
    """
    scope_note = (
        "Generate tests covering every public function or class in this file."
        if scope == "module"
        else "Generate a single focused test for the first public function in this file."
    )

    raw_block = _build_project_context_block(project_context, domain_signals, domain_cluster)
    context_block = ("\n" + raw_block) if raw_block else ""

    style_block = ""
    if test_style_sample:
        style_block = (
            "\n## Existing test style (sample)\n"
            "Match the style of this existing test file:\n"
            f"```\n{test_style_sample}\n```\n"
        )

    hints_block = ""
    if category_hints:
        capped = category_hints[:5]
        numbered = "\n".join(f"{i + 1}. {h}" for i, h in enumerate(capped))
        hints_block = f"\n## What to generate\n{numbered}\n"

    prompt = f"""Generate a {framework} test for this {language} source file.

## Source file path
{source_path}

## Target framework
{framework}

## Scope
{scope_note}

## Mandatory header line
The first line of your output MUST be exactly:
{header_line}
{context_block}{style_block}
## Source file contents
```
{source_text}
```
{hints_block}
## Your output
Emit ONLY the test file contents, starting with the mandatory header line above. No markdown fences. No explanations. The file must parse as valid {language} and pass a {framework} collection check.
"""

    if len(prompt) > _PROMPT_WARN_CHARS:
        logger.warning(
            "test-generation prompt is %d chars (threshold %d); "
            "consider a smaller source file or shorter llm_summary",
            len(prompt),
            _PROMPT_WARN_CHARS,
        )
    return prompt
