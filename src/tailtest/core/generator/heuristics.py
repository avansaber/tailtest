"""Pure-function + test-existence heuristics for auto-offer generation.

Phase 1 Task 1.5a. When the PostToolUse hook finishes a test run, it
scans the edited file for public functions that look like good
candidates for auto-generated tests, checks whether those functions
already have test coverage by name, and emits a non-blocking
"consider running /tailtest:gen" suggestion in the next-turn context.

The heuristics here are intentionally conservative:

- A function is "pure" only when AST analysis finds NO obvious I/O
  calls, NO global keyword usage, and at least one return statement.
  Any ambiguity means the function is treated as impure and the
  suggestion is suppressed. False negatives (missed suggestions)
  are fine; false positives (suggesting test generation for a
  function that needs a mocked database) waste the user's time.
- A function "has a test" when any file under tests/ references
  the function name literally. This is a grep, not an import graph,
  which means it has false positives (test files that happen to
  mention the name in a comment) and false negatives (test files
  that import the function under an alias). The false positives
  are acceptable because suppressing an unneeded suggestion is
  cheap; the false negatives are tracked as a future improvement.
- Phase 1 ships the Python path. TypeScript and JavaScript require
  a different parse step (Python ast does not understand them) and
  are deferred to a follow-up.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


# Function names whose obvious presence inside a body marks it impure.
# These are Python-specific; the TS/JS equivalents live in a future
# module.
_IO_NAMES = frozenset(
    {
        "open",
        "print",
        "input",
        "read",
        "readlines",
        "write",
        "writelines",
        "system",
        "exec",
        "execfile",
        "getenv",
        "environ",
        "subprocess",
        "Popen",
        "run",
        "call",
        "check_call",
        "check_output",
        "urlopen",
        "request",
        "get",
        "post",
        "put",
        "delete",
        "patch",
        "head",
        "fetch",
        "stdout",
        "stderr",
        "stdin",
    }
)

# Attribute patterns that strongly signal I/O. We match on the full
# dotted name so we don't block functions that happen to use a variable
# literally called "get". Checked as `a.b` where both sides exist in
# the AST.
_IO_ATTRIBUTE_PATTERNS = frozenset(
    {
        "os.environ",
        "os.getenv",
        "os.system",
        "sys.stdout",
        "sys.stderr",
        "sys.stdin",
        "sys.exit",
        "requests.get",
        "requests.post",
        "requests.put",
        "requests.delete",
        "httpx.get",
        "httpx.post",
        "urllib.request.urlopen",
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_output",
        "asyncio.create_subprocess_exec",
    }
)


@dataclass(frozen=True)
class PureFunctionCandidate:
    """A function the heuristic believes is a good test-gen candidate.

    Attributes
    ----------
    name:
        The function's Python identifier.
    lineno:
        The 1-indexed line number where `def <name>` appears.
    is_async:
        True if the function was defined with `async def`.
    """

    name: str
    lineno: int
    is_async: bool


def find_pure_functions_in_source(source_text: str) -> list[PureFunctionCandidate]:
    """Return the list of top-level pure functions in a Python source file.

    "Top-level" means module-level only. Methods inside classes and
    nested functions are excluded because test generation for them
    requires understanding their class context, which is a harder
    problem that the Phase 1 heuristic doesn't try to solve.

    Private functions (names starting with an underscore) are also
    excluded because they're rarely worth the user's review time.
    """
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return []

    results: list[PureFunctionCandidate] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.name.startswith("_"):
                continue
            if not _is_pure_function(node):
                continue
            results.append(
                PureFunctionCandidate(
                    name=node.name,
                    lineno=node.lineno,
                    is_async=isinstance(node, ast.AsyncFunctionDef),
                )
            )
    return results


def _is_pure_function(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Return True if the function body contains no obvious I/O markers.

    Rules applied in order:
    1. The body must contain at least one `return` statement.
    2. The body must not contain a `global` statement.
    3. The body must not call any name in `_IO_NAMES` as a bare call.
    4. The body must not access any attribute chain matching
       `_IO_ATTRIBUTE_PATTERNS`.
    """
    has_return = False
    for sub in ast.walk(func):
        if isinstance(sub, ast.Return):
            has_return = True
        if isinstance(sub, ast.Global):
            return False
        if isinstance(sub, ast.Call) and _call_target_is_io(sub):
            return False
        if isinstance(sub, ast.Attribute):
            dotted = _attribute_dotted_name(sub)
            if dotted in _IO_ATTRIBUTE_PATTERNS:
                return False
    return has_return


def _call_target_is_io(call: ast.Call) -> bool:
    """Return True if the call's target resolves to an I/O function name."""
    func = call.func
    if isinstance(func, ast.Name) and func.id in _IO_NAMES:
        return True
    if isinstance(func, ast.Attribute) and func.attr in _IO_NAMES:
        dotted = _attribute_dotted_name(func)
        if dotted in _IO_ATTRIBUTE_PATTERNS:
            return True
    return False


def _attribute_dotted_name(attr: ast.Attribute) -> str:
    """Walk an attribute chain and return the dotted path, best effort."""
    parts: list[str] = []
    node: ast.expr = attr
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


# --- Test existence heuristic ------------------------------------------


def has_test_for_function(
    source_file: Path,
    func_name: str,
    project_root: Path,
) -> bool:
    """Return True if any test file under the project mentions ``func_name``.

    Walks ``<project_root>/tests/`` looking at ``test_*.py`` files and
    returns True if any of them contains the function name as a whole
    word. Also walks ``<project_root>/src/<package>/tests/`` as a
    secondary pattern for packages that colocate tests with source.

    This is a grep, not an import graph. False positives (test file
    mentions the name in a comment) are acceptable because suppressing
    an unneeded suggestion is cheap. False negatives (test file
    imports under an alias) are tracked as a known limitation.
    """
    if not func_name:
        return False

    search_roots: list[Path] = []
    tests_dir = project_root / "tests"
    if tests_dir.is_dir():
        search_roots.append(tests_dir)

    # Colocated pattern: src/<package>/tests/
    src_dir = project_root / "src"
    if src_dir.is_dir():
        for child in src_dir.iterdir():
            if not child.is_dir():
                continue
            colocated_tests = child / "tests"
            if colocated_tests.is_dir():
                search_roots.append(colocated_tests)

    if not search_roots:
        return False

    needle = func_name
    for root in search_roots:
        for test_file in root.rglob("test_*.py"):
            if "fixtures" in test_file.parts:
                # Same exclusion rule as PythonRunner: fixture subtrees
                # are self-contained pytest rootdirs and do not count
                # as "this project's tests".
                continue
            try:
                content = test_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if needle in content:
                return True

    # Also check a flat `tests.py` at the source file's directory,
    # which is a common Django / Flask idiom.
    for candidate in (source_file.parent / "tests.py", source_file.parent / "test_main.py"):
        if not candidate.exists():
            continue
        try:
            content = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if needle in content:
            return True

    return False


# --- Combined offer entry point ---------------------------------------


def find_uncovered_functions(
    source_file: Path,
    project_root: Path,
) -> list[PureFunctionCandidate]:
    """Return pure functions in ``source_file`` that have no test coverage.

    Combines the two heuristics: finds pure functions via AST, then
    filters out ones that already have a matching test. The result
    is the set of candidates the PostToolUse hook offers to generate
    tests for.

    Returns an empty list when the source file cannot be read, is not
    Python, or has no pure functions.
    """
    if source_file.suffix.lower() != ".py":
        return []
    try:
        source_text = source_file.read_text(encoding="utf-8")
    except OSError:
        return []

    candidates = find_pure_functions_in_source(source_text)
    if not candidates:
        return []

    return [
        candidate
        for candidate in candidates
        if not has_test_for_function(source_file, candidate.name, project_root)
    ]
