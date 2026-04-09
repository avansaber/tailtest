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
    "get_default_registry",
    "register_runner",
]
