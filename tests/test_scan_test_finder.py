"""Tests for find_test_directories() BFS helper (Phase 3 Task 3.1)."""

from __future__ import annotations

from pathlib import Path

from tailtest.core.scan.test_finder import find_test_directories

# --- Basic discovery ---


def test_empty_directory_returns_empty(tmp_path: Path) -> None:
    result = find_test_directories(tmp_path)
    assert result == []


def test_directory_named_tests_is_found(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_something.py").write_text("def test_x(): pass\n")

    result = find_test_directories(tmp_path)
    assert tests_dir in result


def test_directory_named_test_is_found(tmp_path: Path) -> None:
    test_dir = tmp_path / "test"
    test_dir.mkdir()

    result = find_test_directories(tmp_path)
    assert test_dir in result


def test_directory_named_spec_is_found(tmp_path: Path) -> None:
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()

    result = find_test_directories(tmp_path)
    assert spec_dir in result


def test_directory_with_test_prefix_files_is_found(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "test_utils.py").write_text("def test_x(): pass\n")

    result = find_test_directories(tmp_path)
    assert src_dir in result


def test_directory_with_test_suffix_files_is_found(tmp_path: Path) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "utils_test.py").write_text("def test_x(): pass\n")

    result = find_test_directories(tmp_path)
    assert src_dir in result


def test_root_itself_not_included_even_if_named_tests(tmp_path: Path) -> None:
    """Depth 0 is the root -- it should never appear in results."""
    (tmp_path / "test_root.py").write_text("def test_x(): pass\n")
    result = find_test_directories(tmp_path)
    assert tmp_path not in result


# --- Skip dirs ---


def test_venv_is_excluded(tmp_path: Path) -> None:
    venv_tests = tmp_path / ".venv" / "tests"
    venv_tests.mkdir(parents=True)

    result = find_test_directories(tmp_path)
    assert venv_tests not in result
    assert not any(".venv" in str(p) for p in result)


def test_node_modules_is_excluded(tmp_path: Path) -> None:
    nm_tests = tmp_path / "node_modules" / "tests"
    nm_tests.mkdir(parents=True)

    result = find_test_directories(tmp_path)
    assert not any("node_modules" in str(p) for p in result)


def test_pycache_is_excluded(tmp_path: Path) -> None:
    cache_dir = tmp_path / "__pycache__" / "tests"
    cache_dir.mkdir(parents=True)

    result = find_test_directories(tmp_path)
    assert not any("__pycache__" in str(p) for p in result)


# --- Depth limit ---


def test_depth_limit_is_respected(tmp_path: Path) -> None:
    # Create a tests dir 5 levels deep (beyond default max_depth=4)
    deep = tmp_path / "a" / "b" / "c" / "d" / "tests"
    deep.mkdir(parents=True)

    result = find_test_directories(tmp_path, max_depth=4)
    assert deep not in result


def test_depth_limit_includes_at_boundary(tmp_path: Path) -> None:
    # 4 levels deep is exactly at default max_depth=4
    at_limit = tmp_path / "a" / "b" / "c" / "tests"
    at_limit.mkdir(parents=True)

    result = find_test_directories(tmp_path, max_depth=4)
    assert at_limit in result


# --- Monorepo / nested layout ---


def test_monorepo_nested_test_dirs_are_all_found(tmp_path: Path) -> None:
    """Simulates a multi-module monorepo where each module has its own tests/."""
    for module in ("frontend", "backend", "shared"):
        module_tests = tmp_path / "packages" / module / "tests"
        module_tests.mkdir(parents=True)
        (module_tests / "test_basic.py").write_text("def test_x(): pass\n")

    result = find_test_directories(tmp_path)
    result_set = set(result)

    for module in ("frontend", "backend", "shared"):
        expected = tmp_path / "packages" / module / "tests"
        assert expected in result_set


def test_no_double_counting_within_test_dir(tmp_path: Path) -> None:
    """Sub-directories of a tests/ dir are not recursed into separately."""
    tests_dir = tmp_path / "tests"
    subdir = tests_dir / "unit"
    subdir.mkdir(parents=True)
    (subdir / "test_something.py").write_text("def test_x(): pass\n")

    result = find_test_directories(tmp_path)
    # tests/ is found, but not tests/unit/ separately
    assert tests_dir in result
    assert subdir not in result


def test_plain_source_dir_without_test_files_not_found(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "module.py").write_text("x = 1\n")

    result = find_test_directories(tmp_path)
    assert src not in result
