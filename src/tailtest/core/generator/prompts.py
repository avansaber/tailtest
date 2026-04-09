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


def build_user_prompt(
    *,
    source_path: str,
    source_text: str,
    language: str,
    framework: str,
    header_line: str,
    scope: str,
) -> str:
    """Assemble the user message passed to ``claude -p``.

    Parameters match ``TestGenerator.generate()``. ``scope`` is either
    ``"module"`` (cover the whole file) or ``"function"`` (cover a
    single exported entry point).
    """
    scope_note = (
        "Generate tests covering every public function or class in this file."
        if scope == "module"
        else "Generate a single focused test for the first public function in this file."
    )
    return f"""Generate a {framework} test for this {language} source file.

## Source file path
{source_path}

## Target framework
{framework}

## Scope
{scope_note}

## Mandatory header line
The first line of your output MUST be exactly:
{header_line}

## Source file contents
```
{source_text}
```

## Your output
Emit ONLY the test file contents, starting with the mandatory header line above. No markdown fences. No explanations. The file must parse as valid {language} and pass a {framework} collection check.
"""
