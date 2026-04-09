"""SCA (Software Composition Analysis) via the OSV API (Phase 2 Task 2.3)."""

from tailtest.security.sca.manifests import (
    ManifestDiff,
    PackageRef,
    diff_manifests,
    parse_package_json,
    parse_pyproject_toml,
)
from tailtest.security.sca.osv import (
    OSVLookup,
    OSVNotAvailable,
    OSVVulnerability,
)

__all__ = [
    "ManifestDiff",
    "OSVLookup",
    "OSVNotAvailable",
    "OSVVulnerability",
    "PackageRef",
    "diff_manifests",
    "parse_package_json",
    "parse_pyproject_toml",
]
