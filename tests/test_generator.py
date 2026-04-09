"""Tests for the test generator (Phase 1 Task 1.12b).

Exercises language detection, framework selection, target path
resolution, test-file skip heuristic, prompt assembly, claude
subprocess invocation (mocked), fence stripping, header enforcement,
assertion guarantee, and per-language compile check integration.

The claude CLI subprocess is mocked in all tests so the suite does
not spend real Claude Code tokens and does not depend on a working
claude binary in CI.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tailtest.core.generator import (
    GeneratedTest,
    GenerationError,
    GeneratorSkipped,
    TestGenerator,
)
from tailtest.core.generator.compile_check import (
    check_file,
    check_python,
)
from tailtest.core.generator.generator import (
    _PYTHON_HEADER,
)
from tailtest.core.generator.prompts import SYSTEM_PROMPT, build_user_prompt

# --- Language + framework detection ------------------------------------


def test_detect_language_by_extension(tmp_path: Path) -> None:
    """Language detection should use the file suffix, lowercased."""
    gen = TestGenerator(tmp_path)
    assert gen._detect_language(Path("foo.py")) == "python"
    assert gen._detect_language(Path("foo.ts")) == "typescript"
    assert gen._detect_language(Path("foo.tsx")) == "typescript"
    assert gen._detect_language(Path("foo.js")) == "javascript"
    assert gen._detect_language(Path("foo.mjs")) == "javascript"
    assert gen._detect_language(Path("foo.rs")) == ""


def test_resolve_test_path_python(tmp_path: Path) -> None:
    """Python source files map to tests/unit/test_<stem>.py."""
    gen = TestGenerator(tmp_path)
    out = gen._resolve_test_path(tmp_path / "src" / "widget.py", "python")
    assert out == tmp_path / "tests" / "unit" / "test_widget.py"


def test_resolve_test_path_typescript(tmp_path: Path) -> None:
    """TypeScript source files map to tests/<stem>.test.ts."""
    gen = TestGenerator(tmp_path)
    out = gen._resolve_test_path(tmp_path / "src" / "widget.ts", "typescript")
    assert out == tmp_path / "tests" / "widget.test.ts"


def test_resolve_test_path_normalizes_mts_to_ts(tmp_path: Path) -> None:
    """.mts / .cts source extensions get normalized to .ts for the test file."""
    gen = TestGenerator(tmp_path)
    assert (
        gen._resolve_test_path(tmp_path / "src" / "widget.mts", "typescript")
        == tmp_path / "tests" / "widget.test.ts"
    )


def test_resolve_test_path_normalizes_mjs_to_js(tmp_path: Path) -> None:
    """.mjs / .cjs source extensions get normalized to .js for the test file."""
    gen = TestGenerator(tmp_path)
    assert (
        gen._resolve_test_path(tmp_path / "src" / "widget.mjs", "javascript")
        == tmp_path / "tests" / "widget.test.js"
    )


# --- Test-file skip heuristic ------------------------------------------


def test_looks_like_test_file_python(tmp_path: Path) -> None:
    gen = TestGenerator(tmp_path)
    assert gen._looks_like_test_file(Path("tests/test_widget.py")) is True
    assert gen._looks_like_test_file(Path("tests/unit/test_widget.py")) is True
    assert gen._looks_like_test_file(Path("src/widget.py")) is False


def test_looks_like_test_file_typescript(tmp_path: Path) -> None:
    gen = TestGenerator(tmp_path)
    assert gen._looks_like_test_file(Path("tests/widget.test.ts")) is True
    assert gen._looks_like_test_file(Path("__tests__/widget.spec.ts")) is True
    assert gen._looks_like_test_file(Path("src/widget.ts")) is False


# --- Skipped generations -----------------------------------------------


@pytest.mark.asyncio
async def test_generate_skips_nonexistent_source(tmp_path: Path) -> None:
    gen = TestGenerator(tmp_path)
    with pytest.raises(GeneratorSkipped, match="does not exist"):
        await gen.generate(tmp_path / "nowhere.py")


@pytest.mark.asyncio
async def test_generate_skips_unsupported_language(tmp_path: Path) -> None:
    (tmp_path / "foo.rs").write_text("fn main() {}\n")
    gen = TestGenerator(tmp_path)
    with pytest.raises(GeneratorSkipped, match="unsupported language"):
        await gen.generate(tmp_path / "foo.rs")


@pytest.mark.asyncio
async def test_generate_skips_test_file_itself(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    src = tmp_path / "tests" / "test_widget.py"
    src.write_text("def test_x(): pass\n")
    gen = TestGenerator(tmp_path)
    with pytest.raises(GeneratorSkipped, match="itself a test file"):
        await gen.generate(src)


@pytest.mark.asyncio
async def test_generate_skips_when_target_already_exists(tmp_path: Path) -> None:
    src = tmp_path / "src" / "widget.py"
    src.parent.mkdir()
    src.write_text("def add(a, b): return a + b\n")
    target = tmp_path / "tests" / "unit" / "test_widget.py"
    target.parent.mkdir(parents=True)
    target.write_text("# existing test, do not overwrite\n")
    gen = TestGenerator(tmp_path)
    with pytest.raises(GeneratorSkipped, match="already exists"):
        await gen.generate(src)
    # The existing test must be untouched.
    assert "do not overwrite" in target.read_text()


# --- Prompt assembly ----------------------------------------------------


def test_build_user_prompt_contains_source_and_header() -> None:
    out = build_user_prompt(
        source_path="src/widget.py",
        source_text="def add(a, b): return a + b",
        language="python",
        framework="pytest",
        header_line=_PYTHON_HEADER,
        scope="module",
    )
    assert "src/widget.py" in out
    assert "pytest" in out
    assert "def add(a, b)" in out
    assert _PYTHON_HEADER in out
    assert "module" in out.lower() or "every public function" in out


def test_system_prompt_includes_no_fencing_rule() -> None:
    """The system prompt must instruct the model to avoid markdown fences."""
    assert "backticks" in SYSTEM_PROMPT.lower() or "fencing" in SYSTEM_PROMPT.lower()


def test_system_prompt_requires_assertion() -> None:
    """The system prompt must require at least one assertion."""
    assert "assertion" in SYSTEM_PROMPT.lower()


# --- Fence stripping ----------------------------------------------------


def test_strip_fences_removes_triple_backticks() -> None:
    raw = "```python\ndef test_x():\n    assert True\n```\n"
    out = TestGenerator._strip_fences(raw)
    assert "```" not in out
    assert "def test_x" in out


def test_strip_fences_handles_no_fences() -> None:
    raw = "def test_x():\n    assert True\n"
    out = TestGenerator._strip_fences(raw)
    assert "def test_x" in out


# --- Assertion guarantee -----------------------------------------------


def test_has_assertion_python() -> None:
    assert TestGenerator._has_assertion("def test_x():\n    assert x == 1", "python") is True
    assert (
        TestGenerator._has_assertion("def test_x():\n    pytest.raises(ValueError)", "python")
        is True
    )
    assert TestGenerator._has_assertion("def test_x():\n    pass", "python") is False


def test_has_assertion_typescript() -> None:
    assert (
        TestGenerator._has_assertion("it('works', () => { expect(1).toBe(1); });", "typescript")
        is True
    )
    assert TestGenerator._has_assertion("it('noop', () => {});", "typescript") is False


# --- Preview ------------------------------------------------------------


def test_make_preview_short_file_returns_full() -> None:
    text = "line1\nline2\nline3"
    assert TestGenerator._make_preview(text) == text


def test_make_preview_long_file_truncates_with_note() -> None:
    text = "\n".join(f"line{i}" for i in range(30))
    out = TestGenerator._make_preview(text, max_lines=5)
    assert out.count("\n") == 5
    assert "10 more lines" in out or "more lines" in out


# --- End-to-end with mocked claude -------------------------------------


def _mock_claude_result(text: str, cost: float = 0.001) -> bytes:
    """Build a bytes payload that mimics `claude -p --output-format json`."""
    outer = {"type": "result", "result": text, "total_cost_usd": cost}
    return json.dumps(outer).encode("utf-8")


class _MockProcess:
    """Minimal async subprocess stand-in for create_subprocess_exec."""

    def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


@pytest.mark.asyncio
async def test_generate_python_happy_path(tmp_path: Path) -> None:
    """End-to-end: source file, mocked claude, generated file lands with header + assertion."""
    src = tmp_path / "src" / "widget.py"
    src.parent.mkdir()
    src.write_text("def add(a, b):\n    return a + b\n")

    generated = (
        f"{_PYTHON_HEADER}\n"
        "from src.widget import add\n\n"
        "def test_add_simple():\n"
        "    assert add(2, 3) == 5\n"
    )
    stdout = _mock_claude_result(generated)

    async def fake_exec(*args, **kwargs):
        return _MockProcess(stdout=stdout)

    gen = TestGenerator(tmp_path)
    with (
        patch("shutil.which", return_value="/fake/claude"),
        patch("asyncio.create_subprocess_exec", new=fake_exec),
    ):
        result = await gen.generate(src)

    assert isinstance(result, GeneratedTest)
    assert result.language == "python"
    assert result.framework == "pytest"
    assert result.test_path == tmp_path / "tests" / "unit" / "test_widget.py"
    assert result.test_path.exists()
    content = result.test_path.read_text()
    assert _PYTHON_HEADER in content.splitlines()[0]
    assert "assert add(2, 3)" in content
    assert result.compile_check.ok
    assert result.compile_check.tool == "ast"


@pytest.mark.asyncio
async def test_generate_prepends_missing_header(tmp_path: Path) -> None:
    """If the model skips the header line, the generator prepends it."""
    src = tmp_path / "src" / "widget.py"
    src.parent.mkdir()
    src.write_text("def add(a, b):\n    return a + b\n")

    generated = "def test_add():\n    assert True\n"  # No header.
    stdout = _mock_claude_result(generated)

    async def fake_exec(*args, **kwargs):
        return _MockProcess(stdout=stdout)

    gen = TestGenerator(tmp_path)
    with (
        patch("shutil.which", return_value="/fake/claude"),
        patch("asyncio.create_subprocess_exec", new=fake_exec),
    ):
        result = await gen.generate(src)

    content = result.test_path.read_text()
    assert _PYTHON_HEADER in content.splitlines()[0]


@pytest.mark.asyncio
async def test_generate_rejects_output_without_assertion(tmp_path: Path) -> None:
    """A generated file with zero assertions must fail and never land on disk."""
    src = tmp_path / "src" / "widget.py"
    src.parent.mkdir()
    src.write_text("def add(a, b):\n    return a + b\n")

    generated = f"{_PYTHON_HEADER}\ndef test_nothing():\n    pass\n"
    stdout = _mock_claude_result(generated)

    async def fake_exec(*args, **kwargs):
        return _MockProcess(stdout=stdout)

    gen = TestGenerator(tmp_path)
    with (
        patch("shutil.which", return_value="/fake/claude"),
        patch("asyncio.create_subprocess_exec", new=fake_exec),
        pytest.raises(GenerationError, match="does not contain any assertion"),
    ):
        await gen.generate(src)

    target = tmp_path / "tests" / "unit" / "test_widget.py"
    assert not target.exists()


@pytest.mark.asyncio
async def test_generate_deletes_file_on_compile_failure(tmp_path: Path) -> None:
    """A syntactically broken generated file must be deleted before raising."""
    src = tmp_path / "src" / "widget.py"
    src.parent.mkdir()
    src.write_text("def add(a, b):\n    return a + b\n")

    # Deliberately broken Python. Has an `assert` so it gets past the
    # assertion gate, but the syntax is invalid so ast.parse fails.
    generated = f"{_PYTHON_HEADER}\nassert (\n"
    stdout = _mock_claude_result(generated)

    async def fake_exec(*args, **kwargs):
        return _MockProcess(stdout=stdout)

    gen = TestGenerator(tmp_path)
    with (
        patch("shutil.which", return_value="/fake/claude"),
        patch("asyncio.create_subprocess_exec", new=fake_exec),
        pytest.raises(GenerationError, match="compile check failed"),
    ):
        await gen.generate(src)

    target = tmp_path / "tests" / "unit" / "test_widget.py"
    assert not target.exists(), "Broken file must be deleted before raising"


@pytest.mark.asyncio
async def test_generate_raises_when_claude_missing(tmp_path: Path) -> None:
    """Missing claude CLI surfaces as a clear GenerationError."""
    src = tmp_path / "src" / "widget.py"
    src.parent.mkdir()
    src.write_text("def add(a, b): return a + b\n")

    gen = TestGenerator(tmp_path)
    with (
        patch("shutil.which", return_value=None),
        pytest.raises(GenerationError, match="claude CLI not found"),
    ):
        await gen.generate(src)


@pytest.mark.asyncio
async def test_generate_raises_on_claude_nonzero_exit(tmp_path: Path) -> None:
    src = tmp_path / "src" / "widget.py"
    src.parent.mkdir()
    src.write_text("def add(a, b): return a + b\n")

    async def fake_exec(*args, **kwargs):
        return _MockProcess(stdout=b"", stderr=b"auth failed", returncode=2)

    gen = TestGenerator(tmp_path)
    with (
        patch("shutil.which", return_value="/fake/claude"),
        patch("asyncio.create_subprocess_exec", new=fake_exec),
        pytest.raises(GenerationError, match="claude CLI exited with error"),
    ):
        await gen.generate(src)


# --- Compile check module ----------------------------------------------


@pytest.mark.asyncio
async def test_check_python_happy_path(tmp_path: Path) -> None:
    f = tmp_path / "ok.py"
    f.write_text("def x(): return 1\n")
    result = await check_python(f)
    assert result.ok is True
    assert result.tool == "ast"


@pytest.mark.asyncio
async def test_check_python_syntax_error(tmp_path: Path) -> None:
    f = tmp_path / "broken.py"
    f.write_text("def x( return 1\n")
    result = await check_python(f)
    assert result.ok is False
    assert "line" in result.message or "syntax" in result.message.lower()


@pytest.mark.asyncio
async def test_check_file_dispatches_by_language(tmp_path: Path) -> None:
    f = tmp_path / "ok.py"
    f.write_text("x = 1\n")
    result = await check_file(f, "python")
    assert result.ok is True
    assert result.tool == "ast"


@pytest.mark.asyncio
async def test_check_file_unknown_language_is_skipped(tmp_path: Path) -> None:
    f = tmp_path / "x.rs"
    f.write_text("fn main() {}\n")
    result = await check_file(f, "rust")
    assert result.ok is True
    assert result.tool == "skipped"


# --- Never-commits guarantee -------------------------------------------


def test_generator_source_contains_no_git_calls() -> None:
    """Static guarantee: the generator module text has no git subprocess calls.

    The generator must never stage, commit, or otherwise touch git.
    This test greps the source file for obvious offenders so a future
    refactor does not silently introduce a git side effect.
    """
    source = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "tailtest"
        / "core"
        / "generator"
        / "generator.py"
    ).read_text()
    forbidden = ["git add", "git commit", "git stage", '"git"', "'git'"]
    for needle in forbidden:
        assert needle not in source, f"generator must not reference {needle}"
