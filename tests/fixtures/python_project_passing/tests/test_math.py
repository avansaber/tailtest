"""Fixture tests — all passing. Used by PythonRunner tests.

Fixture layout follows the pytest-xdist / pytest-testmon convention: the
fixture's own `pyproject.toml` sets `[tool.pytest.ini_options] pythonpath = ["src"]`,
so `from fixture_passing import ...` Just Works without any sys.path hacks.
"""

from fixture_passing import add, multiply


def test_add_simple() -> None:
    assert add(1, 2) == 3


def test_add_zero() -> None:
    assert add(0, 0) == 0


def test_multiply_simple() -> None:
    assert multiply(3, 4) == 12
