"""Style sampling -- find recent test files and extract code style context."""

from __future__ import annotations

import fnmatch
import os
import re
from typing import Optional

TEST_FILE_PATTERNS: dict[str, list[str]] = {
    "python": ["test_*.py", "*_test.py"],
    "typescript": ["*.test.ts", "*.spec.ts", "*.test.tsx", "*.spec.tsx"],
    "javascript": ["*.test.js", "*.spec.js", "*.test.ts", "*.spec.ts"],
    "ruby": ["*_spec.rb", "*_test.rb"],
    "go": ["*_test.go"],
    "java": ["*Test.java", "*Tests.java", "*IT.java"],
    "php": ["*Test.php", "*_test.php"],
}

_HELPER_MODULE_RE = re.compile(
    r"test[-_]utils?|helpers?|factories|factory|test[-_]setup",
    re.IGNORECASE,
)


def find_recent_test_files(
    project_root: str,
    runners: dict,
    max_files: int = 3,
) -> list[str]:
    """Return up to max_files most-recently-modified test file paths (absolute)."""
    candidates: list[tuple[float, str]] = []
    _skip_dirs = {"node_modules", ".venv", "venv", "__pycache__", "dist", "build", "vendor"}

    for language, runner in runners.items():
        patterns = TEST_FILE_PATTERNS.get(language, [])
        if not patterns:
            continue
        test_loc = runner.get("test_location", "")
        if test_loc in (".", "inline"):
            search_root = project_root
        else:
            search_root = os.path.join(project_root, test_loc.rstrip("/"))

        if not os.path.isdir(search_root):
            continue

        for dirpath, dirnames, filenames in os.walk(search_root):
            dirnames[:] = [d for d in dirnames if d not in _skip_dirs]
            for filename in filenames:
                if any(fnmatch.fnmatch(filename, pat) for pat in patterns):
                    abs_path = os.path.join(dirpath, filename)
                    try:
                        mtime = os.path.getmtime(abs_path)
                        candidates.append((mtime, abs_path))
                    except OSError:
                        pass

    candidates.sort(key=lambda x: x[0], reverse=True)
    seen: set[str] = set()
    result: list[str] = []
    for _, path in candidates:
        if path not in seen:
            seen.add(path)
            result.append(path)
            if len(result) >= max_files:
                break
    return result


def extract_style_snippet(file_path: str, max_lines: int = 30) -> Optional[str]:
    """Return the first max_lines lines of a test file as a stripped string."""
    try:
        with open(file_path, encoding="utf-8", errors="replace") as fh:
            lines = []
            for i, line in enumerate(fh):
                if i >= max_lines:
                    break
                lines.append(line)
        return "".join(lines).rstrip()
    except OSError:
        return None


def detect_custom_helpers(snippets: list[str]) -> list[str]:
    """Detect custom test helper imports in test file snippets."""
    helpers: list[str] = []
    seen: set[str] = set()

    for snippet in snippets:
        for m in re.finditer(
            r"^from\s+conftest\s+import\s+(.+)$", snippet, re.MULTILINE
        ):
            names = m.group(1).strip()
            key = f"conftest:{names}"
            if key not in seen:
                seen.add(key)
                helpers.append(f"`from conftest import {names}`")

        for m in re.finditer(
            r"^import\s+\{([^}]+)\}\s+from\s+['\"]([^'\"]+)['\"]",
            snippet,
            re.MULTILINE,
        ):
            names = m.group(1).strip()
            module = m.group(2)
            module_base = module.split("/")[-1]
            if _HELPER_MODULE_RE.search(module_base):
                key = f"js:{module}:{names[:40]}"
                if key not in seen:
                    seen.add(key)
                    helpers.append(f"`import {{{names}}} from '{module}'`")

        if len(helpers) >= 5:
            break

    return helpers[:5]


def build_style_context(project_root: str, runners: dict) -> Optional[str]:
    """Sample recent test files and return a style-context block, or None."""
    recent = find_recent_test_files(project_root, runners, max_files=3)
    if not recent:
        return None

    snippets: list[str] = []
    parts: list[str] = []

    for file_path in recent:
        snippet = extract_style_snippet(file_path, max_lines=30)
        if snippet is None:
            continue
        snippets.append(snippet)
        rel_path = os.path.relpath(file_path, project_root).replace("\\", "/")
        parts.append(f"--- {rel_path} ---\n{snippet}")

    if not parts:
        return None

    custom_helpers = detect_custom_helpers(snippets)

    lines: list[str] = [
        f"tailtest style context ({len(parts)} recent test file(s) sampled):",
        "Match the style, patterns, and conventions shown below when generating tests.",
        "",
    ]
    lines.extend(parts)

    if custom_helpers:
        lines.append("")
        lines.append(
            "Custom test helpers detected -- use these instead of bare"
            " render/mount/instantiation:"
        )
        for h in custom_helpers:
            lines.append(f"  {h}")

    return "\n".join(lines)
