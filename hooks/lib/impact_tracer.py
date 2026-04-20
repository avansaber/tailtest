"""H5 -- AST-based import graph for Python files (opt-in).

Finds which other project files import the given source file.
Used to surface the impact radius when a file is queued for testing.

Opt-in via .tailtest/config.json: {"impact_tracing": true}
Python only. Uses stdlib ast -- no dependencies.
"""

from __future__ import annotations

import ast
import os
import re

_SKIP_DIRS = {
    "node_modules", ".venv", "venv", ".env", "env", "dist", "build",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".git", ".tailtest",
    "migrations", "vendor", "target",
}
_MAX_FILES = 500  # cap to keep the walk fast


def _module_name_from_path(rel_path: str) -> str:
    """Convert a relative file path to a dotted module name."""
    no_ext = os.path.splitext(rel_path)[0]
    return no_ext.replace(os.sep, ".").replace("/", ".")


def _imports_from_source(content: str) -> list[str]:
    """Return all module names imported by a Python source file."""
    imported = []
    try:
        tree = ast.parse(content, type_comments=False)
    except SyntaxError:
        return imported
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.append(node.module)
    return imported


def find_importers(source_rel_path: str, project_root: str) -> list[str]:
    """Return relative paths of Python files that import the given source file.

    Only searches Python files. Caps at _MAX_FILES scanned.
    """
    target_module = _module_name_from_path(source_rel_path)
    # Also accept the leaf name for relative imports
    target_leaf = target_module.split(".")[-1]

    importers: list[str] = []
    scanned = 0

    for root, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            abs_path = os.path.join(root, filename)
            rel_path = os.path.relpath(abs_path, project_root).replace("\\", "/")
            if rel_path == source_rel_path:
                continue
            scanned += 1
            if scanned > _MAX_FILES:
                break
            try:
                with open(abs_path, "r", errors="ignore") as fh:
                    content = fh.read(4096)
            except OSError:
                continue
            imported = _imports_from_source(content)
            for mod in imported:
                if mod == target_module or mod.endswith(f".{target_leaf}"):
                    importers.append(rel_path)
                    break
        if scanned > _MAX_FILES:
            break

    return importers


def format_impact_note(source_path: str, importers: list[str]) -> str:
    """Return a one-line impact note, or empty string if no importers found."""
    if not importers:
        return ""
    names = ", ".join(os.path.basename(p) for p in importers[:3])
    overflow = len(importers) - 3
    suffix = f" (+{overflow} more)" if overflow > 0 else ""
    return f"Impact: {os.path.basename(source_path)} is imported by {names}{suffix}."


def is_impact_tracing_enabled(project_root: str) -> bool:
    """Check .tailtest/config.json for impact_tracing: true."""
    config_path = os.path.join(project_root, ".tailtest", "config.json")
    if not os.path.exists(config_path):
        return False
    try:
        import json
        with open(config_path) as fh:
            cfg = json.load(fh)
        return bool(cfg.get("impact_tracing", False))
    except Exception:
        return False
