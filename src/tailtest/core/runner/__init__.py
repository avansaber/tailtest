# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""tailtest.core.runner — pluggable test runner interface + registry.

Phase 1 Task 1.2 defines `BaseRunner`; Tasks 1.2a and 1.2b implement
`PythonRunner` (pytest) and `JSRunner` (jest/vitest). Phase 4.5 adds
`RustRunner` (cargo test). Phase 2 adds the custom runner adapter for
unsupported languages.

Registering a runner class via `register_runner` makes it visible to
the engine's language-based dispatch in `get_runner_for_language`.
"""

from tailtest.core.runner.base import (
    BaseRunner,
    RunnerNotAvailable,
    RunnerRegistry,
    TestID,
    get_default_registry,
    register_runner,
)

__all__ = [
    "BaseRunner",
    "RunnerNotAvailable",
    "RunnerRegistry",
    "TestID",
    "_register_all_runners",
    "get_default_registry",
    "register_runner",
]


def _register_all_runners() -> None:
    """Import every runner module to trigger their @register_runner decorators.

    Call this once in each entry-point (CLI commands, hooks) before calling
    get_default_registry().all_for_project(). Omitting it leaves node_test,
    ava, mocha, tape, and rust unregistered so they are silently skipped.
    """
    import tailtest.core.runner.ava  # noqa: F401
    import tailtest.core.runner.javascript  # noqa: F401
    import tailtest.core.runner.mocha  # noqa: F401
    import tailtest.core.runner.node_test  # noqa: F401
    import tailtest.core.runner.python  # noqa: F401
    import tailtest.core.runner.rust  # noqa: F401
    import tailtest.core.runner.tape  # noqa: F401
