"""BFS test directory discovery for nested project layouts."""

from __future__ import annotations

from pathlib import Path

_SKIP_DIRS = frozenset(
    {
        ".git",
        ".tox",
        ".venv",
        "venv",
        "env",
        ".env",
        "__pycache__",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
        "dist",
        "build",
        ".build",
        "site-packages",
    }
)


def find_test_directories(root: str | Path, max_depth: int = 4) -> list[Path]:
    """Return all directories under *root* that look like test roots.

    A directory is a test root if it:
    - is named ``tests``, ``test``, or ``spec``; OR
    - contains at least one ``test_*.py`` or ``*_test.py`` file directly.

    Stops descending past *max_depth* levels below *root*.
    Skips common non-source directories (venv, node_modules, etc.).
    """
    root = Path(root)
    found: list[Path] = []
    queue: list[tuple[Path, int]] = [(root, 0)]

    while queue:
        current, depth = queue.pop(0)
        if depth > max_depth:
            continue

        is_test_dir = current.name in {"tests", "test", "spec"}
        if not is_test_dir and depth > 0:
            try:
                is_test_dir = any(
                    p.name.startswith("test_") or p.name.endswith("_test.py")
                    for p in current.iterdir()
                    if p.is_file() and p.suffix == ".py"
                )
            except PermissionError:
                continue

        if is_test_dir and depth > 0:
            found.append(current)
            # Don't recurse into test dirs to avoid double-counting
            continue

        try:
            for child in sorted(current.iterdir()):
                if child.is_dir() and child.name not in _SKIP_DIRS:
                    queue.append((child, depth + 1))
        except PermissionError:
            continue

    return found
