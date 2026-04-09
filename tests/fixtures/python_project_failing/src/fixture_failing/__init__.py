"""Fixture package with a buggy function used by runner tests."""


def subtract(a: int, b: int) -> int:
    # Intentional bug: should be a - b
    return a + b
