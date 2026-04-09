"""Fixture tests — 1 pass, 1 intentional fail, 1 skip. Used by PythonRunner tests.

The failing test is **intentional** — it exists so PythonRunner's JUnit XML
parser can be verified against a real pytest failure. The fixture project's
pyproject.toml sets `pythonpath = ["src"]` (pytest 7+) so `from fixture_failing
import ...` works without sys.path hacks.
"""

import pytest

from fixture_failing import subtract


def test_subtract_passing_case() -> None:
    # 0 - 0 is 0 either way, so this passes even with the bug.
    assert subtract(0, 0) == 0


def test_subtract_real_bug() -> None:
    # Hits the bug: returns 5 instead of -1.
    # This failure is INTENTIONAL — the outer PythonRunner tests verify
    # that it gets parsed into a Finding correctly.
    assert subtract(2, 3) == -1


@pytest.mark.skip(reason="intentional skip for fixture")
def test_subtract_skipped() -> None:
    raise RuntimeError("this should not run")
