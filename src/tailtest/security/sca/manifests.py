# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""Dependency manifest parsers + diff (Phase 2 Task 2.3).

Pure-function helpers that extract `PackageRef` objects from the
most common modern dependency manifests:

- `pyproject.toml` (PEP 621 `[project.dependencies]` and
  `[project.optional-dependencies]`)
- `package.json` (`dependencies`, `devDependencies`,
  `peerDependencies`, `optionalDependencies`)

Deferred to follow-ups:
- `requirements.txt` (legacy Python format; less common in modern
  PEP 621 projects but still widespread enough to matter)
- `package-lock.json` and `pnpm-lock.yaml` (nested transitive
  graphs that require lockfile-aware parsing and diffing)
- `Cargo.toml` (Phase 4.5 when the Rust runner ships)
- `go.mod` (later)

Design rules:
- Parsers are pure functions that take the text content and
  return `list[PackageRef]`. Callers do the file IO.
- Version strings are stored verbatim from the manifest (e.g.
  `>=1.2.3,<2.0`, `^1.2.3`, `1.2.3`). The OSV API accepts
  these as-is in most cases; exotic version specifiers may need
  normalization in a future revision.
- `diff_manifests(old, new)` returns a `ManifestDiff` with the
  packages that were ADDED or BUMPED. Packages that were removed
  are NOT returned (removing a dependency does not introduce a
  new vulnerability; at worst it does not fix an existing one,
  which is not actionable at hook time).
- Every parser handles broken or missing input gracefully by
  returning an empty list and logging a warning. The hot loop
  never raises on a malformed manifest.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

try:
    import tomllib
except ImportError:  # pragma: no cover, Python 3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PackageRef:
    """One (name, version, ecosystem) tuple extracted from a manifest.

    `ecosystem` matches the OSV API's ecosystem enum:
    `PyPI` for Python packages, `npm` for JavaScript, `crates.io`
    for Rust, `Go` for Go modules, etc.

    `version` is stored verbatim as a string because manifest
    formats carry widely different version specifier syntaxes
    (`>=1.2.3`, `^1.2.3`, `1.2.3`, `~1.2`, etc.). The OSV API
    accepts these in most cases; normalization is the job of a
    future revision.

    `source_spec` is a short tag indicating which section of the
    manifest the ref came from, so reporters can distinguish
    runtime deps from dev deps. Examples: `project.dependencies`,
    `project.optional-dependencies.dev`, `devDependencies`.
    """

    name: str
    version: str
    ecosystem: str
    source_spec: str

    @property
    def key(self) -> tuple[str, str]:
        """Identity tuple used for diffing, (ecosystem, name)."""
        return (self.ecosystem, self.name)


@dataclass(frozen=True)
class ManifestDiff:
    """Result of comparing two manifest snapshots.

    `added` is packages that appear in `new` but not `old`.
    `bumped` is packages whose version string changed between
    `old` and `new`. Each entry is a `(old_ref, new_ref)` pair so
    callers can show "foo 1.2.3 -> 1.3.0" style output.
    """

    added: list[PackageRef]
    bumped: list[tuple[PackageRef, PackageRef]]

    @property
    def changed_refs(self) -> list[PackageRef]:
        """Return every ref that should be checked against OSV.

        Includes both added packages and the NEW version of bumped
        packages. Removed packages are not included per the
        design rule in the module docstring.
        """
        refs = list(self.added)
        for _old, new in self.bumped:
            refs.append(new)
        return refs


# --- pyproject.toml parsing --------------------------------------------


def parse_pyproject_toml(text: str) -> list[PackageRef]:
    """Parse a ``pyproject.toml`` file and return its PyPI PackageRefs.

    Reads ``[project.dependencies]`` and
    ``[project.optional-dependencies]`` per PEP 621. Does NOT read
    ``[build-system].requires`` because those are build-time deps
    that are not part of the installed runtime.

    Returns an empty list on any parse failure.
    """
    if not text.strip():
        return []

    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        logger.warning("pyproject.toml parse failed: %s", exc)
        return []

    project = data.get("project")
    if not isinstance(project, dict):
        return []

    refs: list[PackageRef] = []

    runtime_deps = project.get("dependencies")
    if isinstance(runtime_deps, list):
        for entry in runtime_deps:
            if not isinstance(entry, str):
                continue
            name, version = _split_pep508_requirement(entry)
            if name:
                refs.append(
                    PackageRef(
                        name=name,
                        version=version,
                        ecosystem="PyPI",
                        source_spec="project.dependencies",
                    )
                )

    optional = project.get("optional-dependencies")
    if isinstance(optional, dict):
        for group_name, group_list in optional.items():
            if not isinstance(group_list, list):
                continue
            for entry in group_list:
                if not isinstance(entry, str):
                    continue
                name, version = _split_pep508_requirement(entry)
                if name:
                    refs.append(
                        PackageRef(
                            name=name,
                            version=version,
                            ecosystem="PyPI",
                            source_spec=f"project.optional-dependencies.{group_name}",
                        )
                    )

    return refs


def _split_pep508_requirement(requirement: str) -> tuple[str, str]:
    """Split a PEP 508 requirement like ``click>=8.1`` into ``(click, >=8.1)``.

    Handles common shapes:
    - Bare name: ``click`` -> ``("click", "")``
    - Comparison: ``click>=8.1`` -> ``("click", ">=8.1")``
    - Multiple specs: ``click>=8.1,<9`` -> ``("click", ">=8.1,<9")``
    - Environment markers: ``click>=8.1; python_version>='3.11'``
      -> ``("click", ">=8.1")`` (markers stripped)
    - Extras: ``click[colors]>=8.1`` -> ``("click", ">=8.1")``
      (extras dropped, since the extras set does not affect the
      vulnerability lookup)

    Returns ``("", "")`` when the input does not look like a valid
    requirement.
    """
    text = requirement.strip()
    if not text:
        return ("", "")

    # Drop environment markers (everything after the first semicolon)
    if ";" in text:
        text = text.split(";", 1)[0].strip()

    # Drop extras (everything between [ and ])
    if "[" in text and "]" in text:
        before = text.split("[", 1)[0]
        after = text.split("]", 1)[1]
        text = (before + after).strip()

    # Split on the first comparison operator
    for op_prefix in (">=", "<=", "==", "!=", "~=", ">", "<"):
        idx = text.find(op_prefix)
        if idx > 0:
            name = text[:idx].strip()
            version = text[idx:].strip()
            if name and _is_valid_package_name(name):
                return (name, version)
            return ("", "")

    # Bare name, no version specifier
    name = text.strip()
    if name and _is_valid_package_name(name):
        return (name, "")
    return ("", "")


def _is_valid_package_name(name: str) -> bool:
    """Return True for a plausible PyPI package name.

    Matches PEP 508 naming loosely: letters, digits, hyphens,
    underscores, dots. Rejects empty strings and names starting
    with a digit (conservative).
    """
    if not name:
        return False
    if not name[0].isalpha() and name[0] != "_":
        return False
    return all(ch.isalnum() or ch in "-_." for ch in name)


# --- package.json parsing ----------------------------------------------


_PACKAGE_JSON_SECTIONS = (
    "dependencies",
    "devDependencies",
    "peerDependencies",
    "optionalDependencies",
)


def parse_package_json(text: str) -> list[PackageRef]:
    """Parse a ``package.json`` file and return its npm PackageRefs.

    Reads all four dependency sections: runtime, dev, peer, and
    optional. Each dependency becomes a ``PackageRef`` with
    ecosystem ``npm``. The section name is stored in
    ``source_spec`` so reporters can distinguish dev deps.

    Returns an empty list on any parse failure.
    """
    if not text.strip():
        return []

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("package.json parse failed: %s", exc)
        return []

    if not isinstance(data, dict):
        return []

    refs: list[PackageRef] = []
    for section in _PACKAGE_JSON_SECTIONS:
        section_deps = data.get(section)
        if not isinstance(section_deps, dict):
            continue
        for name, version in section_deps.items():
            if not isinstance(name, str) or not name:
                continue
            version_str = str(version) if version is not None else ""
            refs.append(
                PackageRef(
                    name=name,
                    version=version_str,
                    ecosystem="npm",
                    source_spec=section,
                )
            )
    return refs


# --- Cargo.lock parsing ------------------------------------------------


def parse_cargo_lock(text: str) -> list[PackageRef]:
    """Parse a ``Cargo.lock`` file and return registry ``PackageRef`` objects.

    ``Cargo.lock`` is a TOML file with one ``[[package]]`` section per
    resolved dependency. Each section has at minimum a ``name`` and
    ``version`` field. Registry packages (those sourced from crates.io or
    another Cargo registry) also carry a ``source`` field that starts with
    ``"registry+"``. Workspace-local packages and git dependencies are
    intentionally excluded because they are not in the crates.io advisory
    database queried via OSV.

    All returned refs use ecosystem ``"crates.io"`` which is the OSV API
    identifier for the Rust crate registry.

    Returns an empty list on any parse failure.
    """
    if not text.strip():
        return []

    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        logger.warning("Cargo.lock parse failed: %s", exc)
        return []

    packages = data.get("package")
    if not isinstance(packages, list):
        return []

    refs: list[PackageRef] = []
    for pkg in packages:
        if not isinstance(pkg, dict):
            continue
        name = pkg.get("name")
        version = pkg.get("version")
        source = pkg.get("source", "")
        if not isinstance(name, str) or not name:
            continue
        if not isinstance(version, str) or not version:
            continue
        # Skip workspace-local packages (no source) and git dependencies.
        # Only registry packages have vulnerabilities in crates.io's advisory DB.
        if not isinstance(source, str) or "registry+" not in source:
            continue
        refs.append(
            PackageRef(
                name=name,
                version=version,
                ecosystem="crates.io",
                source_spec="Cargo.lock",
            )
        )

    return refs


# --- Diff ---------------------------------------------------------------


def diff_manifests(old: list[PackageRef], new: list[PackageRef]) -> ManifestDiff:
    """Compute the diff between two manifest snapshots.

    Returns a ``ManifestDiff`` containing:
    - `added`: packages present in `new` but not in `old`
    - `bumped`: packages whose version string changed

    Uses `(ecosystem, name)` as the identity key so the same
    package name in different ecosystems (e.g. a PyPI `foo` vs an
    npm `foo`) is NOT treated as the same dependency.

    Order is preserved relative to `new` for added packages and
    relative to `old` for bumped packages (stable for tests).
    """
    old_by_key: dict[tuple[str, str], PackageRef] = {ref.key: ref for ref in old}
    new_by_key: dict[tuple[str, str], PackageRef] = {ref.key: ref for ref in new}

    added: list[PackageRef] = []
    bumped: list[tuple[PackageRef, PackageRef]] = []

    for ref in new:
        old_ref = old_by_key.get(ref.key)
        if old_ref is None:
            added.append(ref)
        elif old_ref.version != ref.version:
            bumped.append((old_ref, ref))

    # Deduplicate by key in case a manifest lists the same package
    # in multiple sections (e.g. once in dependencies, once in
    # optional-dependencies). Prefer the first occurrence.
    seen_added: set[tuple[str, str]] = set()
    deduped_added: list[PackageRef] = []
    for ref in added:
        if ref.key in seen_added:
            continue
        seen_added.add(ref.key)
        deduped_added.append(ref)

    seen_bumped: set[tuple[str, str]] = set()
    deduped_bumped: list[tuple[PackageRef, PackageRef]] = []
    for old_ref, new_ref in bumped:
        if new_ref.key in seen_bumped:
            continue
        seen_bumped.add(new_ref.key)
        deduped_bumped.append((old_ref, new_ref))

    _ = new_by_key  # reserved for future use when we surface removed packages

    return ManifestDiff(added=deduped_added, bumped=deduped_bumped)
