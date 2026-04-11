# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""RustTIA -- crate-level test impact analysis (Phase 4.5 Task 4.5.2).

Maps a list of changed files to the crate names that need re-testing.
Algorithm: walk up from each file to find the nearest ``Cargo.toml``,
read ``[package].name``, and return the deduplicated set of affected
crate names.  If any file has no Cargo.toml ancestor, return an empty
list (meaning: run all tests).

Workspace-level dependency tracing (knowing which downstream crates
depend on an upstream crate that changed) is a stretch goal; this
implementation handles the direct-file-to-crate mapping only.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path

logger = logging.getLogger(__name__)


class RustTIA:
    """Test-impact-analysis provider for Rust projects.

    Crate-level granularity: returns the names of all crates that
    directly contain any of the changed files.  Does not resolve
    downstream workspace dependencies (that is a future enhancement).
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root

    def impacted_crates(self, changed_files: list[Path]) -> list[str]:
        """Return crate names affected by changes to these files.

        Algorithm:
        1. For each file, walk up to find nearest Cargo.toml.
        2. Read ``[package].name`` from that Cargo.toml.
        3. If any file has no Cargo.toml ancestor, return [] (run all).
        4. Return deduplicated list of crate names.

        An empty return value means "no mapping found -- run all tests".
        """
        if not changed_files:
            return []

        crate_names: list[str] = []
        seen: set[str] = set()

        for f in changed_files:
            crate_root = self._find_crate_root(f)
            if crate_root is None:
                logger.debug("RustTIA: no Cargo.toml ancestor found for %s; run all", f)
                return []

            name = self._read_package_name(crate_root / "Cargo.toml")
            if name is None:
                logger.debug(
                    "RustTIA: could not read package name from %s; run all",
                    crate_root / "Cargo.toml",
                )
                return []

            if name not in seen:
                seen.add(name)
                crate_names.append(name)

        return crate_names

    # --- Private helpers ---

    def _find_crate_root(self, file: Path) -> Path | None:
        """Walk up from ``file`` to find the directory containing ``Cargo.toml``.

        Returns the directory, or None if no Cargo.toml is found before
        the filesystem root.
        """
        try:
            candidate = file.resolve()
        except OSError:
            candidate = file
        current = candidate.parent if candidate.is_file() else candidate
        while True:
            if (current / "Cargo.toml").exists():
                return current
            parent = current.parent
            if parent == current:
                return None
            current = parent

    def _read_package_name(self, cargo_toml: Path) -> str | None:
        """Return ``[package].name`` from a ``Cargo.toml``, or None on error."""
        try:
            data = tomllib.loads(cargo_toml.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            logger.debug("RustTIA: failed to parse %s: %s", cargo_toml, exc)
            return None
        pkg = data.get("package")
        if isinstance(pkg, dict):
            name = pkg.get("name")
            if isinstance(name, str):
                return name
        return None
