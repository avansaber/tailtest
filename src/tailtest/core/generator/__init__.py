# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""tailtest.core.generator, test-generator scaffolding.

Phase 1 Task 1.12b. Given a source file, generate a starter test file
in the project's native framework (pytest, vitest, jest) via a
``claude -p`` subprocess call. Write the file with a mandatory
"review before committing" header. Run a per-language compile check
before returning. On compile failure, delete the file and report the
compiler output so the caller can surface a useful error.

The generator NEVER commits, NEVER overwrites existing tests, and
NEVER stages files to git. Those guarantees are enforced by the
generator module itself and asserted by tests that grep the source
for any git or subprocess-to-git call.
"""

from tailtest.core.generator.generator import (
    GeneratedTest,
    GenerationError,
    GeneratorSkipped,
    TestGenerator,
)
from tailtest.core.generator.prompts import ProjectContext

__all__ = [
    "GeneratedTest",
    "GenerationError",
    "GeneratorSkipped",
    "ProjectContext",
    "TestGenerator",
]
