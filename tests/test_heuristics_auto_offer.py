"""Tests for the auto-offer heuristics (Phase 1 Task 1.5a)."""

from __future__ import annotations

from pathlib import Path

from tailtest.core.generator.heuristics import (
    PureFunctionCandidate,
    find_pure_functions_in_source,
    find_uncovered_functions,
    has_test_for_function,
)

# --- Pure function detection -------------------------------------------


def test_find_pure_functions_empty_source() -> None:
    assert find_pure_functions_in_source("") == []


def test_find_pure_functions_detects_simple_return() -> None:
    source = """\
def add(a, b):
    return a + b
"""
    result = find_pure_functions_in_source(source)
    assert len(result) == 1
    assert result[0].name == "add"
    assert result[0].is_async is False


def test_find_pure_functions_detects_async() -> None:
    source = """\
async def fetch_shape():
    return {"kind": "circle"}
"""
    result = find_pure_functions_in_source(source)
    assert len(result) == 1
    assert result[0].is_async is True


def test_find_pure_functions_skips_private_underscored() -> None:
    source = """\
def _private_helper():
    return 1

def public_entry():
    return 2
"""
    result = find_pure_functions_in_source(source)
    names = [c.name for c in result]
    assert "public_entry" in names
    assert "_private_helper" not in names


def test_find_pure_functions_skips_missing_return() -> None:
    """A function with no return is considered side-effect-only, skipped."""
    source = """\
def noop():
    pass
"""
    assert find_pure_functions_in_source(source) == []


def test_find_pure_functions_skips_io_print() -> None:
    source = """\
def logger(msg):
    print(msg)
    return msg
"""
    assert find_pure_functions_in_source(source) == []


def test_find_pure_functions_skips_io_open() -> None:
    source = """\
def reader(path):
    with open(path) as fp:
        return fp.read()
"""
    assert find_pure_functions_in_source(source) == []


def test_find_pure_functions_skips_subprocess() -> None:
    source = """\
import subprocess

def runner():
    result = subprocess.run(["ls"])
    return result.returncode
"""
    assert find_pure_functions_in_source(source) == []


def test_find_pure_functions_skips_os_environ() -> None:
    source = """\
import os

def config():
    return os.environ.get("FOO", "default")
"""
    assert find_pure_functions_in_source(source) == []


def test_find_pure_functions_skips_global_keyword() -> None:
    source = """\
counter = 0

def tick():
    global counter
    counter += 1
    return counter
"""
    assert find_pure_functions_in_source(source) == []


def test_find_pure_functions_skips_nested() -> None:
    """Only top-level functions are candidates; nested defs are ignored."""
    source = """\
def outer():
    def nested():
        return 42
    return nested()
"""
    result = find_pure_functions_in_source(source)
    names = [c.name for c in result]
    # Top-level `outer` might still qualify since its body has a return
    # and no I/O.
    assert "outer" in names
    assert "nested" not in names


def test_find_pure_functions_skips_class_methods() -> None:
    """Methods inside a class are not top-level and are excluded."""
    source = """\
class Calc:
    def add(self, a, b):
        return a + b
"""
    assert find_pure_functions_in_source(source) == []


def test_find_pure_functions_accepts_multiple_candidates() -> None:
    source = """\
def add(a, b):
    return a + b

def multiply(a, b):
    return a * b

def greet(name):
    return f"Hello, {name}!"
"""
    result = find_pure_functions_in_source(source)
    names = sorted(c.name for c in result)
    assert names == ["add", "greet", "multiply"]


def test_find_pure_functions_handles_syntax_error_gracefully() -> None:
    """Broken source returns an empty list, not an exception."""
    assert find_pure_functions_in_source("def broken(") == []


def test_pure_function_candidate_preserves_line_number() -> None:
    source = """\
# header comment

def first():
    return 1

def second():
    return 2
"""
    result = find_pure_functions_in_source(source)
    by_name = {c.name: c for c in result}
    assert by_name["first"].lineno == 3
    assert by_name["second"].lineno == 6


# --- Has test for function ---------------------------------------------


def test_has_test_for_function_true_when_name_in_tests_dir(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text("def test_add():\n    assert add(1, 2) == 3\n")
    (tmp_path / "src").mkdir()
    src_file = tmp_path / "src" / "calc.py"
    src_file.write_text("def add(a, b): return a + b\n")

    assert has_test_for_function(src_file, "add", tmp_path) is True


def test_has_test_for_function_false_when_no_match(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_other.py").write_text("def test_other(): assert True\n")
    src_file = tmp_path / "src" / "calc.py"
    src_file.parent.mkdir()
    src_file.write_text("def multiply(a, b): return a * b\n")

    assert has_test_for_function(src_file, "multiply", tmp_path) is False


def test_has_test_for_function_skips_fixture_subtrees(tmp_path: Path) -> None:
    fixture_dir = tmp_path / "tests" / "fixtures" / "inner"
    fixture_dir.mkdir(parents=True)
    (fixture_dir / "test_inner.py").write_text("def test_widget(): pass\n# widget\n")
    src_file = tmp_path / "src" / "widget.py"
    src_file.parent.mkdir()
    src_file.write_text("def widget(): return 1\n")

    # Match is inside tests/fixtures, which is excluded per the same
    # rule as PythonRunner's heuristic.
    assert has_test_for_function(src_file, "widget", tmp_path) is False


def test_has_test_for_function_finds_colocated_tests(tmp_path: Path) -> None:
    """Packages that colocate tests under src/<pkg>/tests/ are matched."""
    package_dir = tmp_path / "src" / "mypkg"
    (package_dir / "tests").mkdir(parents=True)
    (package_dir / "tests" / "test_helper.py").write_text(
        "def test_helper():\n    assert helper() == 1\n"
    )
    src_file = package_dir / "helper.py"
    src_file.write_text("def helper(): return 1\n")

    assert has_test_for_function(src_file, "helper", tmp_path) is True


def test_has_test_for_function_returns_false_on_empty_name(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_foo.py").write_text("x = 1\n")
    src_file = tmp_path / "src" / "foo.py"
    src_file.parent.mkdir()
    src_file.write_text("x = 1\n")
    assert has_test_for_function(src_file, "", tmp_path) is False


# --- Combined find_uncovered_functions ---------------------------------


def test_find_uncovered_functions_happy_path(tmp_path: Path) -> None:
    src = tmp_path / "src" / "calc.py"
    src.parent.mkdir()
    src.write_text("def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n")

    # Only `add` has a test.
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text(
        "from calc import add\ndef test_add():\n    assert add(1, 2) == 3\n"
    )

    uncovered = find_uncovered_functions(src, tmp_path)
    names = [c.name for c in uncovered]
    assert "sub" in names
    assert "add" not in names


def test_find_uncovered_functions_empty_for_non_python(tmp_path: Path) -> None:
    src = tmp_path / "src" / "widget.ts"
    src.parent.mkdir()
    src.write_text("export function foo() { return 1; }\n")
    assert find_uncovered_functions(src, tmp_path) == []


def test_find_uncovered_functions_empty_for_nonexistent(tmp_path: Path) -> None:
    assert find_uncovered_functions(tmp_path / "nowhere.py", tmp_path) == []


def test_find_uncovered_functions_empty_when_everything_covered(tmp_path: Path) -> None:
    src = tmp_path / "src" / "calc.py"
    src.parent.mkdir()
    src.write_text("def add(a, b):\n    return a + b\n")

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text("def test_add(): assert True\n# add\n")

    assert find_uncovered_functions(src, tmp_path) == []


# --- Dataclass shape ---------------------------------------------------


def test_pure_function_candidate_is_frozen() -> None:
    candidate = PureFunctionCandidate(name="foo", lineno=10, is_async=False)
    try:
        candidate.name = "bar"  # type: ignore[misc]
        raise AssertionError("PureFunctionCandidate should be frozen")
    except (AttributeError, TypeError):
        pass
