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
    _RS_HEADER,
)
from tailtest.core.generator.prompts import (
    RUST_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_rust_user_prompt,
    build_user_prompt,
)

# --- Language + framework detection ------------------------------------


def test_detect_language_by_extension(tmp_path: Path) -> None:
    """Language detection should use the file suffix, lowercased."""
    gen = TestGenerator(tmp_path)
    assert gen._detect_language(Path("foo.py")) == "python"
    assert gen._detect_language(Path("foo.ts")) == "typescript"
    assert gen._detect_language(Path("foo.tsx")) == "typescript"
    assert gen._detect_language(Path("foo.js")) == "javascript"
    assert gen._detect_language(Path("foo.mjs")) == "javascript"
    assert gen._detect_language(Path("foo.rs")) == "rust"
    assert gen._detect_language(Path("foo.go")) == ""


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
    (tmp_path / "foo.go").write_text("package main\n")
    gen = TestGenerator(tmp_path)
    with pytest.raises(GeneratorSkipped, match="unsupported language"):
        await gen.generate(tmp_path / "foo.go")


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
    f = tmp_path / "x.go"
    f.write_text("package main\n")
    result = await check_file(f, "go")
    assert result.ok is True
    assert result.tool == "skipped"


@pytest.mark.asyncio
async def test_check_rust_no_cargo_toml_skipped(tmp_path: Path) -> None:
    """check_rust returns skipped when there is no Cargo.toml in the tree."""
    from tailtest.core.generator.compile_check import check_rust

    f = tmp_path / "lib.rs"
    f.write_text("pub fn add(a: i32, b: i32) -> i32 { a + b }\n")
    result = await check_rust(f)
    # No Cargo.toml -> skipped (not a failure).
    assert result.ok is True
    assert result.tool == "skipped"


# --- Rust assertion detection ------------------------------------------


def test_has_assertion_rust() -> None:
    assert TestGenerator._has_assertion("assert_eq!(add(2, 3), 5);", "rust") is True
    assert TestGenerator._has_assertion("assert!(x > 0);", "rust") is True
    assert TestGenerator._has_assertion("assert_ne!(a, b);", "rust") is True
    assert TestGenerator._has_assertion("let _ = foo();", "rust") is False


# --- Rust test style detection -----------------------------------------


def test_detect_rust_test_style_colocated(tmp_path: Path) -> None:
    """Prefer colocated when src/ files already use #[cfg(test)]."""
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "t"\nversion = "0.1.0"\n')
    src = tmp_path / "src"
    src.mkdir()
    (src / "lib.rs").write_text(
        "pub fn add(a: i32, b: i32) -> i32 { a + b }\n\n#[cfg(test)]\nmod tests { }\n"
    )
    gen = TestGenerator(tmp_path)
    assert gen._detect_rust_test_style(src / "lib.rs") == "colocated"


def test_detect_rust_test_style_integration_when_no_colocated(tmp_path: Path) -> None:
    """Fall back to integration when tests/*.rs exist and no src/ file has #[cfg(test)]."""
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "t"\nversion = "0.1.0"\n')
    src = tmp_path / "src"
    src.mkdir()
    (src / "lib.rs").write_text("pub fn add(a: i32, b: i32) -> i32 { a + b }\n")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "integration_test.rs").write_text("// existing integration test\n")
    gen = TestGenerator(tmp_path)
    assert gen._detect_rust_test_style(src / "lib.rs") == "integration"


def test_detect_rust_test_style_defaults_to_colocated(tmp_path: Path) -> None:
    """Default to colocated when no existing test files signal a preference."""
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "t"\nversion = "0.1.0"\n')
    src = tmp_path / "src"
    src.mkdir()
    (src / "lib.rs").write_text("pub fn add(a: i32, b: i32) -> i32 { a + b }\n")
    gen = TestGenerator(tmp_path)
    assert gen._detect_rust_test_style(src / "lib.rs") == "colocated"


# --- Rust integration test detection ----------------------------------


def test_looks_like_rust_integration_test(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "t"\nversion = "0.1.0"\n')
    tests = tmp_path / "tests"
    tests.mkdir()
    integration_test = tests / "my_test.rs"
    integration_test.write_text("// integration test\n")
    gen = TestGenerator(tmp_path)
    assert gen._looks_like_rust_integration_test(integration_test) is True
    # Source file is not an integration test
    src = tmp_path / "src" / "lib.rs"
    src.parent.mkdir()
    src.write_text("pub fn f() {}\n")
    assert gen._looks_like_rust_integration_test(src) is False


# --- Rust skip conditions ---------------------------------------------


@pytest.mark.asyncio
async def test_generate_rust_skips_integration_test_file(tmp_path: Path) -> None:
    """Raises GeneratorSkipped when the source is itself an integration test."""
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "t"\nversion = "0.1.0"\n')
    tests = tmp_path / "tests"
    tests.mkdir()
    test_file = tests / "my_test.rs"
    test_file.write_text("// existing integration test\n")
    gen = TestGenerator(tmp_path)
    with pytest.raises(GeneratorSkipped, match="integration test"):
        await gen.generate(test_file)


@pytest.mark.asyncio
async def test_generate_rust_skips_when_cfg_test_exists(tmp_path: Path) -> None:
    """Raises GeneratorSkipped when the source already has a #[cfg(test)] block."""
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "t"\nversion = "0.1.0"\n')
    src = tmp_path / "src"
    src.mkdir()
    src_file = src / "lib.rs"
    src_file.write_text(
        "pub fn add(a: i32, b: i32) -> i32 { a + b }\n\n#[cfg(test)]\nmod tests { }\n"
    )
    gen = TestGenerator(tmp_path)
    with pytest.raises(GeneratorSkipped, match="already has a #\\[cfg\\(test\\)\\]"):
        await gen.generate(src_file)


@pytest.mark.asyncio
async def test_generate_rust_skips_when_integration_test_already_exists(tmp_path: Path) -> None:
    """Raises GeneratorSkipped when the integration test file already exists."""
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "t"\nversion = "0.1.0"\n')
    src = tmp_path / "src"
    src.mkdir()
    src_file = src / "lib.rs"
    src_file.write_text("pub fn add(a: i32, b: i32) -> i32 { a + b }\n")
    # Create the integration tests directory with an existing .rs file to
    # make the style detection return "integration".
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "other_test.rs").write_text("// other test\n")
    # Pre-create the target integration test file.
    target = tests_dir / "lib_test.rs"
    target.write_text("// do not overwrite\n")
    gen = TestGenerator(tmp_path)
    with pytest.raises(GeneratorSkipped, match="already exists"):
        await gen.generate(src_file)
    assert "do not overwrite" in target.read_text()


# --- Rust colocated generation (mocked claude) -----------------------


@pytest.mark.asyncio
async def test_generate_rust_colocated_happy_path(tmp_path: Path) -> None:
    """Colocated generation appends the mod tests block to the source file."""
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "mylib"\nversion = "0.1.0"\n')
    src = tmp_path / "src"
    src.mkdir()
    src_file = src / "lib.rs"
    original = "pub fn add(a: i32, b: i32) -> i32 { a + b }\n"
    src_file.write_text(original)

    mod_block = (
        f"{_RS_HEADER}\n"
        "#[cfg(test)]\n"
        "mod tests {\n"
        "    use super::*;\n"
        "\n"
        "    #[test]\n"
        "    fn test_add() {\n"
        "        assert_eq!(add(2, 3), 5);\n"
        "    }\n"
        "}\n"
    )
    stdout = _mock_claude_result(mod_block)

    async def fake_exec(*args, **kwargs):
        return _MockProcess(stdout=stdout)

    gen = TestGenerator(tmp_path)
    with (
        patch("shutil.which", return_value="/fake/cargo"),
        patch("asyncio.create_subprocess_exec", new=fake_exec),
    ):
        # cargo check will also run via check_rust; mock it to succeed.
        from tailtest.core.generator import compile_check as cc

        async def fake_check_rust(path):
            from tailtest.core.generator.compile_check import CompileCheckResult

            return CompileCheckResult(ok=True, tool="cargo-check", message="")

        with patch.object(cc, "check_rust", fake_check_rust):
            result = await gen.generate(src_file)

    assert result.language == "rust"
    assert result.framework == "cargo"
    assert result.test_path == src_file  # colocated: same file
    content = src_file.read_text()
    # Original source still present.
    assert "pub fn add" in content
    # mod tests block appended.
    assert "#[cfg(test)]" in content
    assert "assert_eq!(add(2, 3), 5)" in content
    assert _RS_HEADER in content


# --- Rust integration test generation (mocked claude) ----------------


@pytest.mark.asyncio
async def test_generate_rust_integration_happy_path(tmp_path: Path) -> None:
    """Integration generation creates a new file in tests/."""
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "mylib"\nversion = "0.1.0"\n')
    src = tmp_path / "src"
    src.mkdir()
    src_file = src / "lib.rs"
    src_file.write_text("pub fn add(a: i32, b: i32) -> i32 { a + b }\n")
    # Signal integration style: existing tests/*.rs.
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "other_test.rs").write_text("// placeholder\n")

    integration_body = (
        f"{_RS_HEADER}\n"
        "use mylib::*;\n"
        "\n"
        "#[test]\n"
        "fn test_add() {\n"
        "    assert_eq!(add(2, 3), 5);\n"
        "}\n"
    )
    stdout = _mock_claude_result(integration_body)

    async def fake_exec(*args, **kwargs):
        return _MockProcess(stdout=stdout)

    gen = TestGenerator(tmp_path)
    with (
        patch("shutil.which", return_value="/fake/cargo"),
        patch("asyncio.create_subprocess_exec", new=fake_exec),
    ):
        from tailtest.core.generator import compile_check as cc

        async def fake_check_rust(path):
            from tailtest.core.generator.compile_check import CompileCheckResult

            return CompileCheckResult(ok=True, tool="cargo-check", message="")

        with patch.object(cc, "check_rust", fake_check_rust):
            result = await gen.generate(src_file)

    expected_test_path = tmp_path / "tests" / "lib_test.rs"
    assert result.test_path == expected_test_path
    assert expected_test_path.exists()
    content = expected_test_path.read_text()
    assert _RS_HEADER in content
    assert "assert_eq!" in content


# --- Rust compile failure rollback ------------------------------------


@pytest.mark.asyncio
async def test_generate_rust_colocated_rolls_back_on_compile_failure(tmp_path: Path) -> None:
    """When cargo check fails, the source file is restored to its original state."""
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "t"\nversion = "0.1.0"\n')
    src = tmp_path / "src"
    src.mkdir()
    src_file = src / "lib.rs"
    original = "pub fn add(a: i32, b: i32) -> i32 { a + b }\n"
    src_file.write_text(original)

    # The generated block has an assert so it passes the assertion gate,
    # but cargo check will be mocked to fail.
    broken_block = (
        f"{_RS_HEADER}\n"
        "#[cfg(test)]\nmod tests {\n    use super::*;\n"
        "    #[test]\n    fn test_add() { assert_eq!(add(2, 3), 5); }\n"
        "}\n"
    )
    stdout = _mock_claude_result(broken_block)

    async def fake_exec(*args, **kwargs):
        return _MockProcess(stdout=stdout)

    gen = TestGenerator(tmp_path)
    with (
        patch("shutil.which", return_value="/fake/cargo"),
        patch("asyncio.create_subprocess_exec", new=fake_exec),
    ):
        from tailtest.core.generator import compile_check as cc
        from tailtest.core.generator.compile_check import CompileCheckResult

        async def fail_check_rust(path):
            return CompileCheckResult(ok=False, tool="cargo-check", message="type error")

        with (
            patch.object(cc, "check_rust", fail_check_rust),
            pytest.raises(GenerationError, match="compile check failed"),
        ):
            await gen.generate(src_file)

    # Source file must be restored to its original content.
    assert src_file.read_text() == original


# --- Rust prompt assembly ----------------------------------------------


def test_build_rust_user_prompt_colocated_style() -> None:
    out = build_rust_user_prompt(
        source_path="src/lib.rs",
        source_text="pub fn add(a: i32, b: i32) -> i32 { a + b }",
        style="colocated",
        crate_name="mylib",
        scope="module",
    )
    assert "src/lib.rs" in out
    assert "mylib" in out
    assert "colocated" in out
    assert "#[cfg(test)] mod tests block" in out
    assert _RS_HEADER in out


def test_build_rust_user_prompt_integration_style() -> None:
    out = build_rust_user_prompt(
        source_path="src/lib.rs",
        source_text="pub fn add(a: i32, b: i32) -> i32 { a + b }",
        style="integration",
        crate_name="mylib",
        scope="module",
    )
    assert "integration" in out
    assert "mylib" in out
    assert _RS_HEADER in out


def test_rust_system_prompt_rules() -> None:
    assert "assert!" in RUST_SYSTEM_PROMPT
    assert "backticks" in RUST_SYSTEM_PROMPT.lower() or "fencing" in RUST_SYSTEM_PROMPT.lower()
    assert "colocated" in RUST_SYSTEM_PROMPT


# --- Rust crate root helpers ------------------------------------------


def test_find_rust_crate_root_walks_up(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "t"\nversion = "0.1.0"\n')
    deep = tmp_path / "src" / "sub" / "mod.rs"
    deep.parent.mkdir(parents=True)
    deep.write_text("// deep file\n")
    gen = TestGenerator(tmp_path)
    assert gen._find_rust_crate_root(deep) == tmp_path


def test_find_rust_crate_root_returns_none_when_missing(tmp_path: Path) -> None:
    f = tmp_path / "orphan.rs"
    f.write_text("// no cargo\n")
    gen = TestGenerator(tmp_path)
    assert gen._find_rust_crate_root(f) is None


def test_rust_crate_name_reads_package_name(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "awesome_crate"\nversion = "0.1.0"\n')
    f = tmp_path / "src" / "lib.rs"
    f.parent.mkdir()
    f.write_text("pub fn f() {}\n")
    gen = TestGenerator(tmp_path)
    assert gen._rust_crate_name(f) == "awesome_crate"


def test_resolve_rust_integration_test_path(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "t"\nversion = "0.1.0"\n')
    src = tmp_path / "src" / "lib.rs"
    src.parent.mkdir()
    src.write_text("pub fn f() {}\n")
    gen = TestGenerator(tmp_path)
    assert gen._resolve_rust_integration_test_path(src) == tmp_path / "tests" / "lib_test.rs"


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
