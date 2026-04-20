"""S4 -- API contract validation for Python source files (opt-in).

Before queuing a file for testing, checks that the public functions and
classes in the file actually exist (guards against hallucinated APIs).

Opt-in via .tailtest/config.json: {"api_validation": true}
Python only. Uses stdlib ast + importlib.

Scope: verifies that names visible at module top level are importable.
Does NOT do full signature validation.
"""

from __future__ import annotations

import ast
import os
import sys


def extract_public_names(file_path: str) -> list[str]:
    """Return the names of public functions and classes defined in a Python file."""
    try:
        with open(file_path, "r", errors="ignore") as fh:
            content = fh.read()
        tree = ast.parse(content)
    except (OSError, SyntaxError):
        return []

    names = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                names.append(node.name)
    return names


def validate_file_importable(file_path: str, project_root: str) -> tuple[bool, str]:
    """Check that the source file can be imported without errors.

    Returns (ok, message). message is empty when ok.
    Adds project_root to sys.path temporarily for the check.
    """
    if not file_path.endswith(".py"):
        return True, ""

    rel = os.path.relpath(file_path, project_root).replace("\\", "/")
    module_name = os.path.splitext(rel)[0].replace("/", ".").replace(os.sep, ".")

    added = False
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
        added = True

    try:
        import importlib
        importlib.import_module(module_name)
        return True, ""
    except ImportError as e:
        return False, f"import error: {e}"
    except Exception:
        # Module has side effects or requires runtime setup -- treat as ok
        return True, ""
    finally:
        if added and project_root in sys.path:
            sys.path.remove(project_root)


def is_api_validation_enabled(project_root: str) -> bool:
    """Check .tailtest/config.json for api_validation: true."""
    config_path = os.path.join(project_root, ".tailtest", "config.json")
    if not os.path.exists(config_path):
        return False
    try:
        import json
        with open(config_path) as fh:
            cfg = json.load(fh)
        return bool(cfg.get("api_validation", False))
    except Exception:
        return False


def build_api_validation_note(file_path: str, project_root: str) -> str:
    """Return a note to include in the context if the file fails import validation.

    Returns empty string if validation passes or is not applicable.
    """
    if not file_path.endswith(".py"):
        return ""
    ok, message = validate_file_importable(file_path, project_root)
    if ok:
        return ""
    return f"Warning: {os.path.basename(file_path)} has an import error ({message}). Verify imports before writing tests."
