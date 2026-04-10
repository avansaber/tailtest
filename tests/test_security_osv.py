"""Tests for OSV SCA (Phase 2 Task 2.3).

Covers three surface areas:

1. Pure manifest parsers — ``parse_pyproject_toml``,
   ``parse_package_json``, and ``diff_manifests`` — tested with
   inline strings so no tempfiles are needed.
2. Pure OSV response parser — ``parse_osv_batch_response`` and
   its helpers (``_extract_highest_cvss_score``,
   ``_parse_cvss_score_string``, ``_cvss_to_unified_severity``,
   ``_normalize_version_for_osv``) — tested with canned OSV JSON.
3. ``OSVLookup.check_manifest_diff`` end-to-end with a mocked
   ``httpx.AsyncClient`` so the suite never touches the network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from tailtest.core.findings.schema import FindingKind, Severity
from tailtest.security.sca.cargo_audit import (
    _map_rustsec_severity,
    _parse_cargo_audit_json,
    cargo_audit_available,
)
from tailtest.security.sca.manifests import (
    ManifestDiff,
    PackageRef,
    _is_valid_package_name,
    _split_pep508_requirement,
    diff_manifests,
    parse_cargo_lock,
    parse_package_json,
    parse_pyproject_toml,
)
from tailtest.security.sca.osv import (
    OSV_QUERY_BATCH_URL,
    OSVLookup,
    OSVVulnerability,
    _cvss_to_unified_severity,
    _extract_highest_cvss_score,
    _normalize_version_for_osv,
    _osv_vuln_from_dict,
    _parse_cvss_score_string,
    parse_osv_batch_response,
)

# --- pyproject.toml parsing --------------------------------------------


def test_parse_pyproject_empty_returns_empty_list() -> None:
    assert parse_pyproject_toml("") == []
    assert parse_pyproject_toml("   \n") == []


def test_parse_pyproject_malformed_returns_empty_list() -> None:
    assert parse_pyproject_toml("not = valid = toml = at all [") == []


def test_parse_pyproject_missing_project_table_returns_empty_list() -> None:
    text = '[build-system]\nrequires = ["hatchling"]\n'
    assert parse_pyproject_toml(text) == []


def test_parse_pyproject_runtime_dependencies() -> None:
    text = """
[project]
name = "example"
version = "0.1.0"
dependencies = [
    "click>=8.1",
    "httpx>=0.27,<1.0",
    "requests",
]
"""
    refs = parse_pyproject_toml(text)
    assert len(refs) == 3
    click_ref = refs[0]
    assert click_ref.name == "click"
    assert click_ref.version == ">=8.1"
    assert click_ref.ecosystem == "PyPI"
    assert click_ref.source_spec == "project.dependencies"

    httpx_ref = refs[1]
    assert httpx_ref.name == "httpx"
    assert httpx_ref.version == ">=0.27,<1.0"

    requests_ref = refs[2]
    assert requests_ref.name == "requests"
    assert requests_ref.version == ""


def test_parse_pyproject_optional_dependencies() -> None:
    text = """
[project]
name = "example"
version = "0.1.0"
dependencies = ["click>=8.1"]

[project.optional-dependencies]
dev = ["pytest>=8.0", "ruff>=0.5"]
test = ["pytest-asyncio>=0.23"]
"""
    refs = parse_pyproject_toml(text)
    assert len(refs) == 4
    runtime_refs = [r for r in refs if r.source_spec == "project.dependencies"]
    assert len(runtime_refs) == 1
    assert runtime_refs[0].name == "click"

    dev_refs = [r for r in refs if r.source_spec == "project.optional-dependencies.dev"]
    assert len(dev_refs) == 2
    assert {r.name for r in dev_refs} == {"pytest", "ruff"}

    test_refs = [r for r in refs if r.source_spec == "project.optional-dependencies.test"]
    assert len(test_refs) == 1
    assert test_refs[0].name == "pytest-asyncio"


def test_parse_pyproject_ignores_non_string_entries() -> None:
    """Malformed TOML with a dict entry in dependencies list should not crash."""
    # PEP 621 forbids this but we still handle it defensively.
    text = """
[project]
name = "ex"
version = "0.1.0"
dependencies = ["click>=8.1"]
"""
    refs = parse_pyproject_toml(text)
    assert len(refs) == 1


def test_parse_pyproject_build_system_requires_ignored() -> None:
    """Build-system.requires are build-time, not runtime, and should not be parsed."""
    text = """
[build-system]
requires = ["setuptools>=61", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "ex"
version = "0.1.0"
dependencies = ["click>=8.1"]
"""
    refs = parse_pyproject_toml(text)
    assert len(refs) == 1
    assert refs[0].name == "click"


# --- PEP 508 splitter --------------------------------------------------


def test_split_bare_name() -> None:
    assert _split_pep508_requirement("requests") == ("requests", "")


def test_split_simple_comparison() -> None:
    assert _split_pep508_requirement("click>=8.1") == ("click", ">=8.1")


def test_split_multiple_specs() -> None:
    assert _split_pep508_requirement("httpx>=0.27,<1.0") == ("httpx", ">=0.27,<1.0")


def test_split_strips_environment_markers() -> None:
    name, version = _split_pep508_requirement("tomli>=2.0; python_version<'3.11'")
    assert name == "tomli"
    assert version == ">=2.0"


def test_split_strips_extras() -> None:
    assert _split_pep508_requirement("click[colors]>=8.1") == ("click", ">=8.1")


def test_split_handles_whitespace() -> None:
    assert _split_pep508_requirement("  click  >=  8.1  ")[0] == "click"


def test_split_all_operators() -> None:
    for op in (">=", "<=", "==", "!=", "~=", ">", "<"):
        name, version = _split_pep508_requirement(f"foo{op}1.0")
        assert name == "foo"
        assert version == f"{op}1.0"


def test_split_empty_returns_empty_tuple() -> None:
    assert _split_pep508_requirement("") == ("", "")
    assert _split_pep508_requirement("   ") == ("", "")


def test_split_invalid_name_returns_empty() -> None:
    """Leading digit / junk characters should be rejected."""
    assert _split_pep508_requirement("1invalid>=1.0") == ("", "")
    assert _split_pep508_requirement("@invalid") == ("", "")


# --- Valid package name ------------------------------------------------


def test_is_valid_package_name_accepts_common_names() -> None:
    assert _is_valid_package_name("click")
    assert _is_valid_package_name("python-dateutil")
    assert _is_valid_package_name("pytest-asyncio")
    assert _is_valid_package_name("typing_extensions")
    assert _is_valid_package_name("zope.interface")
    assert _is_valid_package_name("_private")


def test_is_valid_package_name_rejects_invalid() -> None:
    assert not _is_valid_package_name("")
    assert not _is_valid_package_name("1start-with-digit")
    assert not _is_valid_package_name("has space")
    assert not _is_valid_package_name("has/slash")
    assert not _is_valid_package_name("@scoped")


# --- package.json parsing ----------------------------------------------


def test_parse_package_json_empty_returns_empty_list() -> None:
    assert parse_package_json("") == []
    assert parse_package_json("  \n  ") == []


def test_parse_package_json_malformed_returns_empty_list() -> None:
    assert parse_package_json("{not json") == []


def test_parse_package_json_non_object_root() -> None:
    assert parse_package_json("[1, 2, 3]") == []
    assert parse_package_json('"just a string"') == []


def test_parse_package_json_runtime_dependencies() -> None:
    text = json.dumps(
        {
            "name": "example",
            "version": "1.0.0",
            "dependencies": {
                "express": "^4.18.2",
                "lodash": "~4.17.21",
            },
        }
    )
    refs = parse_package_json(text)
    assert len(refs) == 2
    by_name = {r.name: r for r in refs}
    assert by_name["express"].version == "^4.18.2"
    assert by_name["express"].ecosystem == "npm"
    assert by_name["express"].source_spec == "dependencies"
    assert by_name["lodash"].version == "~4.17.21"


def test_parse_package_json_all_four_sections() -> None:
    text = json.dumps(
        {
            "name": "ex",
            "version": "1.0.0",
            "dependencies": {"express": "^4.0.0"},
            "devDependencies": {"vitest": "^1.0.0"},
            "peerDependencies": {"react": ">=16.0.0"},
            "optionalDependencies": {"fsevents": "^2.0.0"},
        }
    )
    refs = parse_package_json(text)
    assert len(refs) == 4
    sources = {r.source_spec for r in refs}
    assert sources == {
        "dependencies",
        "devDependencies",
        "peerDependencies",
        "optionalDependencies",
    }


def test_parse_package_json_scoped_packages() -> None:
    text = json.dumps(
        {
            "name": "ex",
            "version": "1.0.0",
            "dependencies": {
                "@types/node": "^20.0.0",
                "@scope/package": "1.2.3",
            },
        }
    )
    refs = parse_package_json(text)
    assert len(refs) == 2
    names = {r.name for r in refs}
    assert names == {"@types/node", "@scope/package"}


def test_parse_package_json_ignores_non_dict_sections() -> None:
    """When deps section is a list or string, ignore it quietly."""
    text = json.dumps(
        {
            "name": "ex",
            "version": "1.0.0",
            "dependencies": ["express", "lodash"],  # invalid shape
            "devDependencies": {"vitest": "^1.0.0"},
        }
    )
    refs = parse_package_json(text)
    assert len(refs) == 1
    assert refs[0].name == "vitest"


def test_parse_package_json_null_version_becomes_empty_string() -> None:
    text = json.dumps(
        {
            "name": "ex",
            "version": "1.0.0",
            "dependencies": {"express": None},
        }
    )
    refs = parse_package_json(text)
    assert len(refs) == 1
    assert refs[0].version == ""


# --- diff_manifests ---------------------------------------------------


def _pkg(name: str, version: str, ecosystem: str = "PyPI") -> PackageRef:
    return PackageRef(name=name, version=version, ecosystem=ecosystem, source_spec="test")


def test_diff_empty_old_and_new() -> None:
    diff = diff_manifests([], [])
    assert diff.added == []
    assert diff.bumped == []
    assert diff.changed_refs == []


def test_diff_all_added_when_old_empty() -> None:
    new = [_pkg("click", "8.1"), _pkg("httpx", "0.27")]
    diff = diff_manifests([], new)
    assert len(diff.added) == 2
    assert diff.bumped == []


def test_diff_detects_bumped_version() -> None:
    old = [_pkg("click", "8.0")]
    new = [_pkg("click", "8.1")]
    diff = diff_manifests(old, new)
    assert diff.added == []
    assert len(diff.bumped) == 1
    assert diff.bumped[0][0].version == "8.0"
    assert diff.bumped[0][1].version == "8.1"


def test_diff_unchanged_packages_not_reported() -> None:
    old = [_pkg("click", "8.1")]
    new = [_pkg("click", "8.1")]
    diff = diff_manifests(old, new)
    assert diff.added == []
    assert diff.bumped == []


def test_diff_removed_packages_not_reported() -> None:
    """Removed packages do not appear in the diff per the design rule."""
    old = [_pkg("click", "8.1"), _pkg("httpx", "0.27")]
    new = [_pkg("click", "8.1")]
    diff = diff_manifests(old, new)
    assert diff.added == []
    assert diff.bumped == []


def test_diff_same_name_different_ecosystems() -> None:
    """PyPI `foo` and npm `foo` are distinct packages."""
    old = [_pkg("foo", "1.0", "PyPI")]
    new = [_pkg("foo", "1.0", "PyPI"), _pkg("foo", "1.0", "npm")]
    diff = diff_manifests(old, new)
    assert len(diff.added) == 1
    assert diff.added[0].ecosystem == "npm"


def test_diff_changed_refs_includes_added_and_bumped() -> None:
    old = [_pkg("click", "8.0")]
    new = [_pkg("click", "8.1"), _pkg("httpx", "0.27")]
    diff = diff_manifests(old, new)
    changed = diff.changed_refs
    assert len(changed) == 2
    assert any(r.name == "click" and r.version == "8.1" for r in changed)
    assert any(r.name == "httpx" for r in changed)


def test_diff_deduplicates_same_package_in_multiple_sections() -> None:
    """A package listed in both runtime and dev sections should appear once."""
    new = [
        PackageRef("pytest", "8.0", "PyPI", "project.dependencies"),
        PackageRef("pytest", "8.0", "PyPI", "project.optional-dependencies.dev"),
    ]
    diff = diff_manifests([], new)
    assert len(diff.added) == 1


def test_manifest_diff_dataclass_is_frozen() -> None:
    diff = ManifestDiff(added=[], bumped=[])
    with pytest.raises((AttributeError, TypeError)):
        diff.added = [_pkg("x", "1.0")]  # type: ignore[misc]


def test_package_ref_key_uses_ecosystem_and_name() -> None:
    ref = _pkg("click", "8.1")
    assert ref.key == ("PyPI", "click")


# --- parse_osv_batch_response ------------------------------------------


def test_parse_osv_empty_response() -> None:
    data: dict[str, Any] = {"results": []}
    result = parse_osv_batch_response(data, batch_size=0)
    assert result == []


def test_parse_osv_non_dict_returns_padded_empty_lists() -> None:
    result = parse_osv_batch_response("not a dict", batch_size=3)
    assert result == [[], [], []]


def test_parse_osv_missing_results_key() -> None:
    data: dict[str, Any] = {"errors": []}
    result = parse_osv_batch_response(data, batch_size=2)
    assert result == [[], []]


def test_parse_osv_pads_when_response_shorter_than_batch() -> None:
    data = {"results": [{"vulns": []}]}
    result = parse_osv_batch_response(data, batch_size=3)
    assert len(result) == 3
    assert all(r == [] for r in result)


def test_parse_osv_truncates_when_response_longer_than_batch() -> None:
    data = {"results": [{"vulns": []}, {"vulns": []}, {"vulns": []}]}
    result = parse_osv_batch_response(data, batch_size=2)
    assert len(result) == 2


def test_parse_osv_single_vuln_entry() -> None:
    data = {
        "results": [
            {
                "vulns": [
                    {
                        "id": "GHSA-abcd-1234",
                        "summary": "Buffer overflow in XYZ",
                        "details": "A detailed explanation.",
                        "severity": [{"type": "CVSS_V3", "score": "7.5"}],
                        "references": [
                            {"type": "ADVISORY", "url": "https://example.com/a"},
                        ],
                        "aliases": ["CVE-2024-0001"],
                    }
                ]
            }
        ]
    }
    result = parse_osv_batch_response(data, batch_size=1)
    assert len(result) == 1
    assert len(result[0]) == 1
    vuln = result[0][0]
    assert vuln.vuln_id == "GHSA-abcd-1234"
    assert vuln.summary == "Buffer overflow in XYZ"
    assert vuln.cvss_score == 7.5
    assert vuln.references == ["https://example.com/a"]
    assert vuln.aliases == ["CVE-2024-0001"]


def test_parse_osv_missing_id_drops_entry() -> None:
    data = {
        "results": [
            {
                "vulns": [
                    {"summary": "no id"},
                    {"id": "GHSA-valid"},
                ]
            }
        ]
    }
    result = parse_osv_batch_response(data, batch_size=1)
    assert len(result[0]) == 1
    assert result[0][0].vuln_id == "GHSA-valid"


def test_parse_osv_ignores_non_dict_result_entries() -> None:
    data = {"results": ["not a dict", {"vulns": []}, 42]}
    result = parse_osv_batch_response(data, batch_size=3)
    assert result == [[], [], []]


def test_parse_osv_string_references() -> None:
    """OSV sometimes emits references as strings rather than {type,url}."""
    data = {
        "results": [
            {
                "vulns": [
                    {
                        "id": "GHSA-x",
                        "references": ["https://foo.com", "https://bar.com"],
                    }
                ]
            }
        ]
    }
    result = parse_osv_batch_response(data, batch_size=1)
    assert result[0][0].references == ["https://foo.com", "https://bar.com"]


def test_parse_osv_affected_package_metadata() -> None:
    data = {
        "results": [
            {
                "vulns": [
                    {
                        "id": "GHSA-x",
                        "affected": [
                            {
                                "package": {"name": "requests", "ecosystem": "PyPI"},
                                "ranges": [
                                    {
                                        "type": "ECOSYSTEM",
                                        "events": [
                                            {"introduced": "2.0.0"},
                                            {"fixed": "2.31.0"},
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        ]
    }
    result = parse_osv_batch_response(data, batch_size=1)
    vuln = result[0][0]
    assert vuln.affected_package == "requests"
    assert "2.0.0" in vuln.affected_version
    # Task 2.4: fixed version extracted from the "fixed" event.
    assert vuln.fixed_version == "2.31.0"


def test_parse_osv_fixed_version_absent_stays_empty() -> None:
    """Advisories without a fixed version yield an empty string."""
    data = {
        "results": [
            {
                "vulns": [
                    {
                        "id": "GHSA-y",
                        "affected": [
                            {
                                "package": {"name": "foo", "ecosystem": "PyPI"},
                                "ranges": [
                                    {
                                        "type": "ECOSYSTEM",
                                        "events": [{"introduced": "1.0"}],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        ]
    }
    result = parse_osv_batch_response(data, batch_size=1)
    assert result[0][0].fixed_version == ""


def test_osv_vuln_from_dict_missing_fields_uses_defaults() -> None:
    vuln = _osv_vuln_from_dict({"id": "GHSA-min"})
    assert vuln is not None
    assert vuln.vuln_id == "GHSA-min"
    assert vuln.summary == ""
    assert vuln.details == ""
    assert vuln.cvss_score == 0.0
    assert vuln.references == []
    assert vuln.aliases == []
    assert vuln.cwe_id == ""


def test_osv_vuln_from_dict_rejects_missing_id() -> None:
    assert _osv_vuln_from_dict({"summary": "no id"}) is None
    assert _osv_vuln_from_dict({"id": ""}) is None
    assert _osv_vuln_from_dict({"id": 42}) is None  # type: ignore[dict-item]


def test_osv_vuln_from_dict_text_severity_fallback() -> None:
    """Phase 2 Task 2.10a: when CVSS score is 0, fall back to
    database_specific.severity text label.
    """
    raw = {
        "id": "GHSA-text-fallback",
        "severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:H/PR:N/UI:R/S:U/C:H/I:N/A:N"}],
        "database_specific": {"severity": "HIGH"},
    }
    vuln = _osv_vuln_from_dict(raw)
    assert vuln is not None
    # CVSS vector returned 0.0 (no trailing score), so the text
    # fallback kicks in and HIGH maps to a score in the >= 7.0 band.
    assert vuln.cvss_score >= 7.0
    assert _cvss_to_unified_severity(vuln.cvss_score) == Severity.HIGH


def test_osv_vuln_from_dict_text_severity_only() -> None:
    """When OSV has no `severity` field at all, text label still
    drives the score (the GHSA-652x-xj99-gmcc shape from the
    real API)."""
    raw = {
        "id": "GHSA-no-cvss",
        "summary": "vuln with no CVSS",
        "database_specific": {"severity": "MODERATE"},
    }
    vuln = _osv_vuln_from_dict(raw)
    assert vuln is not None
    assert _cvss_to_unified_severity(vuln.cvss_score) == Severity.MEDIUM


def test_osv_vuln_from_dict_extracts_cwe_id() -> None:
    """Phase 2 Task 2.10a: cwe_id pulled from database_specific.cwe_ids."""
    raw = {
        "id": "GHSA-with-cwe",
        "database_specific": {
            "severity": "MODERATE",
            "cwe_ids": ["CWE-200", "CWE-522"],
        },
    }
    vuln = _osv_vuln_from_dict(raw)
    assert vuln is not None
    # First CWE wins.
    assert vuln.cwe_id == "CWE-200"


def test_osv_vuln_from_dict_cwe_id_empty_when_no_database_specific() -> None:
    raw = {"id": "GHSA-no-cwe"}
    vuln = _osv_vuln_from_dict(raw)
    assert vuln is not None
    assert vuln.cwe_id == ""


def test_osv_vuln_from_dict_skips_non_cwe_strings_in_cwe_ids() -> None:
    """Some advisories include non-CWE entries; skip them."""
    raw = {
        "id": "GHSA-mixed-cwe",
        "database_specific": {
            "cwe_ids": ["just a description", "CWE-79", "more text"],
        },
    }
    vuln = _osv_vuln_from_dict(raw)
    assert vuln is not None
    assert vuln.cwe_id == "CWE-79"


def test_osv_vuln_from_dict_cvss_takes_precedence_over_text() -> None:
    """When the CVSS score IS parseable, it takes precedence over
    the text label so a real CVSS rating overrides a coarse
    text label."""
    raw = {
        "id": "GHSA-cvss-wins",
        "severity": [{"type": "CVSS_V3", "score": "9.8"}],
        "database_specific": {"severity": "LOW"},  # would map to ~2.0
    }
    vuln = _osv_vuln_from_dict(raw)
    assert vuln is not None
    assert vuln.cvss_score == 9.8
    assert _cvss_to_unified_severity(vuln.cvss_score) == Severity.CRITICAL


# --- CVSS score extraction ---------------------------------------------


def test_extract_cvss_not_a_list_returns_zero() -> None:
    assert _extract_highest_cvss_score(None) == 0.0
    assert _extract_highest_cvss_score("7.5") == 0.0
    assert _extract_highest_cvss_score({}) == 0.0


def test_extract_cvss_picks_highest_score() -> None:
    severity = [
        {"type": "CVSS_V2", "score": "5.0"},
        {"type": "CVSS_V3", "score": "9.1"},
        {"type": "CVSS_V3", "score": "7.5"},
    ]
    assert _extract_highest_cvss_score(severity) == 9.1


def test_extract_cvss_ignores_invalid_entries() -> None:
    severity = [
        "not a dict",
        {"type": "CVSS_V3", "score": "7.5"},
        {"no_score": "field"},
    ]
    assert _extract_highest_cvss_score(severity) == 7.5


def test_extract_cvss_empty_list_returns_zero() -> None:
    assert _extract_highest_cvss_score([]) == 0.0


# --- _parse_cvss_score_string ------------------------------------------


def test_parse_cvss_plain_numeric() -> None:
    assert _parse_cvss_score_string("7.5") == 7.5
    assert _parse_cvss_score_string("9.8") == 9.8
    assert _parse_cvss_score_string("0.0") == 0.0


def test_parse_cvss_plain_integer_as_float() -> None:
    assert _parse_cvss_score_string("10") == 10.0


def test_parse_cvss_vector_with_trailing_score() -> None:
    # Mock vector — real OSV vectors do not embed score like this,
    # but some callers pass it this way. We handle the fallback.
    assert _parse_cvss_score_string("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H/7.5") == 7.5


def test_parse_cvss_extracts_number_from_anywhere_in_string() -> None:
    # Fallback regex path: extract any float.
    assert _parse_cvss_score_string("score=4.3 rating=low") == 4.3


def test_parse_cvss_empty_returns_zero() -> None:
    assert _parse_cvss_score_string("") == 0.0
    assert _parse_cvss_score_string("  ") == 0.0


def test_parse_cvss_no_number_returns_zero() -> None:
    assert _parse_cvss_score_string("unknown") == 0.0
    assert _parse_cvss_score_string("N/A") == 0.0


def test_parse_cvss_skips_cvss_version_prefix() -> None:
    """Phase 2 Task 2.10a regression: a vector with no trailing
    score must NOT return the CVSS spec version (e.g., 3.1) as
    the score. Many GHSA advisories ship vectors that encode
    metrics without a numeric score; for those the function must
    correctly return 0.0 so the caller falls back to the text
    severity label.
    """
    # The dogfood-caught case: full vector, no trailing score.
    score = _parse_cvss_score_string("CVSS:3.1/AV:N/AC:H/PR:N/UI:R/S:U/C:H/I:N/A:N")
    assert score == 0.0, f"expected 0.0, got {score} (regression: returning CVSS version)"


def test_parse_cvss_vector_with_trailing_score_after_prefix() -> None:
    """Vector that DOES include a trailing score should still parse it."""
    score = _parse_cvss_score_string("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H/9.8")
    assert score == 9.8


def test_parse_cvss_v2_prefix_also_stripped() -> None:
    """CVSS:2.0 vectors get the same treatment as CVSS:3.x."""
    assert _parse_cvss_score_string("CVSS:2.0/AV:N/AC:L/Au:N/C:P/I:P/A:P") == 0.0


def test_parse_cvss_plain_decimal_without_prefix_unchanged() -> None:
    """Plain decimals should still parse via the float() shortcut."""
    assert _parse_cvss_score_string("9.8") == 9.8
    assert _parse_cvss_score_string("0.1") == 0.1


# --- _text_severity_to_cvss --------------------------------------------


def test_text_severity_to_cvss_critical() -> None:
    """CRITICAL maps to a score in the >= 9.0 band."""
    from tailtest.security.sca.osv import _text_severity_to_cvss

    score = _text_severity_to_cvss("CRITICAL")
    assert score >= 9.0
    assert _cvss_to_unified_severity(score) == Severity.CRITICAL


def test_text_severity_to_cvss_high() -> None:
    from tailtest.security.sca.osv import _text_severity_to_cvss

    score = _text_severity_to_cvss("HIGH")
    assert 7.0 <= score < 9.0
    assert _cvss_to_unified_severity(score) == Severity.HIGH


def test_text_severity_to_cvss_moderate() -> None:
    """GHSA emits MODERATE; we map it to MEDIUM in our enum."""
    from tailtest.security.sca.osv import _text_severity_to_cvss

    score = _text_severity_to_cvss("MODERATE")
    assert 4.0 <= score < 7.0
    assert _cvss_to_unified_severity(score) == Severity.MEDIUM


def test_text_severity_to_cvss_low() -> None:
    from tailtest.security.sca.osv import _text_severity_to_cvss

    score = _text_severity_to_cvss("LOW")
    assert 0.0 < score < 4.0
    assert _cvss_to_unified_severity(score) == Severity.LOW


def test_text_severity_to_cvss_unknown_returns_zero() -> None:
    from tailtest.security.sca.osv import _text_severity_to_cvss

    assert _text_severity_to_cvss("WHATEVER") == 0.0
    assert _text_severity_to_cvss("") == 0.0


def test_text_severity_to_cvss_handles_non_string() -> None:
    from tailtest.security.sca.osv import _text_severity_to_cvss

    assert _text_severity_to_cvss(None) == 0.0
    assert _text_severity_to_cvss(42) == 0.0
    assert _text_severity_to_cvss(["HIGH"]) == 0.0


def test_text_severity_to_cvss_case_insensitive() -> None:
    from tailtest.security.sca.osv import _text_severity_to_cvss

    assert _text_severity_to_cvss("high") == _text_severity_to_cvss("HIGH")
    assert _text_severity_to_cvss("Moderate") == _text_severity_to_cvss("MODERATE")


# --- Severity mapping --------------------------------------------------


def test_severity_mapping_critical() -> None:
    assert _cvss_to_unified_severity(9.0) == Severity.CRITICAL
    assert _cvss_to_unified_severity(9.8) == Severity.CRITICAL
    assert _cvss_to_unified_severity(10.0) == Severity.CRITICAL


def test_severity_mapping_high() -> None:
    assert _cvss_to_unified_severity(7.0) == Severity.HIGH
    assert _cvss_to_unified_severity(8.9) == Severity.HIGH


def test_severity_mapping_medium() -> None:
    assert _cvss_to_unified_severity(4.0) == Severity.MEDIUM
    assert _cvss_to_unified_severity(6.9) == Severity.MEDIUM


def test_severity_mapping_low() -> None:
    assert _cvss_to_unified_severity(0.1) == Severity.LOW
    assert _cvss_to_unified_severity(3.9) == Severity.LOW


def test_severity_mapping_info_for_zero() -> None:
    assert _cvss_to_unified_severity(0.0) == Severity.INFO


# --- _normalize_version_for_osv ----------------------------------------


def test_normalize_version_empty_string() -> None:
    assert _normalize_version_for_osv("") == ""
    assert _normalize_version_for_osv("   ") == ""


def test_normalize_version_strips_operators() -> None:
    assert _normalize_version_for_osv(">=1.2.3") == "1.2.3"
    assert _normalize_version_for_osv("<=1.2.3") == "1.2.3"
    assert _normalize_version_for_osv("==1.2.3") == "1.2.3"
    assert _normalize_version_for_osv("~=1.2.3") == "1.2.3"
    assert _normalize_version_for_osv("^1.2.3") == "1.2.3"
    assert _normalize_version_for_osv("~1.2.3") == "1.2.3"


def test_normalize_version_drops_trailing_constraints() -> None:
    assert _normalize_version_for_osv(">=1.2.3,<2.0") == "1.2.3"
    assert _normalize_version_for_osv("1.0,<2.0") == "1.0"


def test_normalize_version_bare_version_unchanged() -> None:
    assert _normalize_version_for_osv("1.2.3") == "1.2.3"
    assert _normalize_version_for_osv("4.18.2") == "4.18.2"


def test_normalize_version_whitespace_stripped() -> None:
    assert _normalize_version_for_osv("  1.2.3  ") == "1.2.3"
    assert _normalize_version_for_osv(">=  1.2.3") == "1.2.3"


# --- OSVVulnerability dataclass ----------------------------------------


def test_osv_vulnerability_defaults() -> None:
    vuln = OSVVulnerability(
        vuln_id="GHSA-x",
        summary="s",
        details="d",
        cvss_score=5.0,
    )
    assert vuln.references == []
    assert vuln.aliases == []
    assert vuln.affected_package == ""
    assert vuln.affected_version == ""
    assert vuln.fixed_version == ""
    # Phase 2 Task 2.10a: cwe_id defaults to empty string.
    assert vuln.cwe_id == ""


# --- OSVLookup end-to-end with mocked httpx client --------------------


class _MockResponse:
    """Minimal ``httpx.Response`` stand-in.

    Only implements the fields ``OSVLookup`` reads: ``status_code``,
    ``text``, and ``json()``. Keeps the test setup independent of the
    httpx internal API.
    """

    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload) if not isinstance(payload, str) else payload

    def json(self) -> Any:
        if isinstance(self._payload, str):
            return json.loads(self._payload)
        return self._payload


class _MockAsyncClient:
    """Minimal ``httpx.AsyncClient`` stand-in.

    Tracks calls so assertions can verify batch sizes, endpoints,
    and payload shapes. Supports both POST (used for the OSV
    ``/v1/querybatch`` endpoint) and GET (used for the per-vuln
    ``/v1/vulns/<id>`` hydration calls added in Phase 2 Task
    2.10a).
    """

    def __init__(
        self,
        response: _MockResponse | None = None,
        *,
        raise_on_post: Exception | None = None,
        get_responses: dict[str, _MockResponse] | None = None,
        raise_on_get: Exception | None = None,
    ) -> None:
        self.response = response
        self.raise_on_post = raise_on_post
        self.get_responses = get_responses or {}
        self.raise_on_get = raise_on_get
        self.calls: list[tuple[str, Any]] = []
        self.get_calls: list[str] = []

    async def post(self, url: str, *, json: Any = None) -> _MockResponse:  # noqa: A002
        self.calls.append((url, json))
        if self.raise_on_post is not None:
            raise self.raise_on_post
        assert self.response is not None
        return self.response

    async def get(self, url: str) -> _MockResponse:
        self.get_calls.append(url)
        if self.raise_on_get is not None:
            raise self.raise_on_get
        # Match by suffix so callers don't need to know the full
        # ``OSV_VULNS_URL`` prefix.
        for key, resp in self.get_responses.items():
            if url.endswith(key):
                return resp
        # No matching response: return a 404 so the caller falls
        # back to the lean entry.
        return _MockResponse(404, "")

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_osv_empty_diff_returns_empty(tmp_path: Path) -> None:
    lookup = OSVLookup(tmp_path, client=_MockAsyncClient(), enable_cache=False)  # type: ignore[arg-type]
    diff = ManifestDiff(added=[], bumped=[])
    result = await lookup.check_manifest_diff(diff, run_id="r1")
    assert result == []


@pytest.mark.asyncio
async def test_osv_clean_packages_produce_no_findings(tmp_path: Path) -> None:
    client = _MockAsyncClient(response=_MockResponse(200, {"results": [{"vulns": []}]}))
    lookup = OSVLookup(tmp_path, client=client, enable_cache=False)  # type: ignore[arg-type]
    diff = ManifestDiff(added=[_pkg("click", "8.1")], bumped=[])
    findings = await lookup.check_manifest_diff(diff, run_id="r1")
    assert findings == []
    assert len(client.calls) == 1
    url, payload = client.calls[0]
    assert url == OSV_QUERY_BATCH_URL
    assert payload["queries"][0]["package"]["name"] == "click"
    assert payload["queries"][0]["package"]["ecosystem"] == "PyPI"
    assert payload["queries"][0]["version"] == "8.1"


@pytest.mark.asyncio
async def test_osv_vulnerable_package_produces_finding(tmp_path: Path) -> None:
    client = _MockAsyncClient(
        response=_MockResponse(
            200,
            {
                "results": [
                    {
                        "vulns": [
                            {
                                "id": "GHSA-test-1234",
                                "summary": "Critical vuln in requests",
                                "severity": [{"type": "CVSS_V3", "score": "9.5"}],
                                "references": [
                                    {
                                        "url": "https://github.com/psf/requests/advisories/GHSA-test-1234"
                                    }
                                ],
                                "affected": [
                                    {
                                        "package": {
                                            "name": "requests",
                                            "ecosystem": "PyPI",
                                        },
                                        "ranges": [
                                            {
                                                "type": "ECOSYSTEM",
                                                "events": [
                                                    {"introduced": "2.0.0"},
                                                    {"fixed": "2.31.0"},
                                                ],
                                            }
                                        ],
                                    }
                                ],
                            }
                        ]
                    }
                ]
            },
        )
    )
    lookup = OSVLookup(tmp_path, client=client, enable_cache=False)  # type: ignore[arg-type]
    diff = ManifestDiff(added=[_pkg("requests", "2.0.0")], bumped=[])
    findings = await lookup.check_manifest_diff(diff, run_id="r1")
    assert len(findings) == 1
    f = findings[0]
    assert f.kind == FindingKind.SCA
    assert f.severity == Severity.CRITICAL
    assert "requests" in f.message
    assert "GHSA-test-1234" in f.message
    assert f.rule_id == "osv::GHSA-test-1234"
    assert f.doc_link == "https://github.com/psf/requests/advisories/GHSA-test-1234"
    assert f.claude_hint is not None
    assert "CVSS 9.5" in f.claude_hint
    assert str(f.file) == "pyproject.toml"
    # Task 2.4: SCA findings carry full security metadata so
    # reporters don't have to re-parse the message.
    assert f.cvss_score == 9.5
    assert f.package_name == "requests"
    assert f.package_version == "2.0.0"
    assert f.fixed_version == "2.31.0"
    assert f.advisory_url == ("https://github.com/psf/requests/advisories/GHSA-test-1234")


@pytest.mark.asyncio
async def test_osv_npm_package_hint_points_at_package_json(tmp_path: Path) -> None:
    client = _MockAsyncClient(
        response=_MockResponse(
            200,
            {
                "results": [
                    {
                        "vulns": [
                            {
                                "id": "GHSA-npm-1",
                                "summary": "lodash vuln",
                                "severity": [{"type": "CVSS_V3", "score": "7.5"}],
                            }
                        ]
                    }
                ]
            },
        )
    )
    lookup = OSVLookup(tmp_path, client=client, enable_cache=False)  # type: ignore[arg-type]
    diff = ManifestDiff(added=[_pkg("lodash", "4.17.20", "npm")], bumped=[])
    findings = await lookup.check_manifest_diff(diff, run_id="r1")
    assert len(findings) == 1
    assert str(findings[0].file) == "package.json"
    assert findings[0].severity == Severity.HIGH


@pytest.mark.asyncio
async def test_osv_timeout_returns_empty_list(tmp_path: Path) -> None:
    client = _MockAsyncClient(raise_on_post=httpx.TimeoutException("timed out"))
    lookup = OSVLookup(tmp_path, client=client, enable_cache=False)  # type: ignore[arg-type]
    diff = ManifestDiff(added=[_pkg("click", "8.1")], bumped=[])
    findings = await lookup.check_manifest_diff(diff, run_id="r1")
    assert findings == []


@pytest.mark.asyncio
async def test_osv_network_error_returns_empty_list(tmp_path: Path) -> None:
    client = _MockAsyncClient(raise_on_post=httpx.ConnectError("network unreachable"))
    lookup = OSVLookup(tmp_path, client=client, enable_cache=False)  # type: ignore[arg-type]
    diff = ManifestDiff(added=[_pkg("click", "8.1")], bumped=[])
    findings = await lookup.check_manifest_diff(diff, run_id="r1")
    assert findings == []


@pytest.mark.asyncio
async def test_osv_non_200_returns_empty_list(tmp_path: Path) -> None:
    client = _MockAsyncClient(response=_MockResponse(500, "internal server error"))
    lookup = OSVLookup(tmp_path, client=client, enable_cache=False)  # type: ignore[arg-type]
    diff = ManifestDiff(added=[_pkg("click", "8.1")], bumped=[])
    findings = await lookup.check_manifest_diff(diff, run_id="r1")
    assert findings == []


@pytest.mark.asyncio
async def test_osv_normalizes_version_in_payload(tmp_path: Path) -> None:
    """The API payload should carry the cleaned version, not the spec."""
    client = _MockAsyncClient(response=_MockResponse(200, {"results": [{"vulns": []}]}))
    lookup = OSVLookup(tmp_path, client=client, enable_cache=False)  # type: ignore[arg-type]
    diff = ManifestDiff(added=[_pkg("click", ">=8.1,<9.0")], bumped=[])
    await lookup.check_manifest_diff(diff, run_id="r1")
    _, payload = client.calls[0]
    assert payload["queries"][0]["version"] == "8.1"


@pytest.mark.asyncio
async def test_osv_batches_large_ref_lists(tmp_path: Path) -> None:
    """With >100 refs the runner should chunk into multiple API calls."""
    # Build 150 unique refs to cross the _MAX_BATCH_SIZE boundary.
    refs = [_pkg(f"pkg{i}", "1.0") for i in range(150)]
    client = _MockAsyncClient(
        response=_MockResponse(
            200,
            {"results": [{"vulns": []} for _ in range(100)]},
        )
    )
    lookup = OSVLookup(tmp_path, client=client, enable_cache=False)  # type: ignore[arg-type]
    diff = ManifestDiff(added=refs, bumped=[])
    await lookup.check_manifest_diff(diff, run_id="r1")
    # 150 refs split into batches of 100 yields 2 API calls.
    assert len(client.calls) == 2
    first_payload = client.calls[0][1]
    second_payload = client.calls[1][1]
    assert len(first_payload["queries"]) == 100
    assert len(second_payload["queries"]) == 50


# --- Cache path -------------------------------------------------------


@pytest.mark.asyncio
async def test_osv_cache_hit_skips_api_call(tmp_path: Path) -> None:
    """Pre-populated cache entry should avoid the API call entirely."""
    client = _MockAsyncClient()
    lookup = OSVLookup(tmp_path, client=client, enable_cache=True)  # type: ignore[arg-type]
    ref = _pkg("click", "8.1")
    # Pre-populate the cache with an empty vuln list.
    lookup._save_cached(ref, [])
    diff = ManifestDiff(added=[ref], bumped=[])
    findings = await lookup.check_manifest_diff(diff, run_id="r1")
    assert findings == []
    assert len(client.calls) == 0  # cache short-circuited the API


@pytest.mark.asyncio
async def test_osv_cache_miss_hits_api_and_populates_cache(tmp_path: Path) -> None:
    client = _MockAsyncClient(response=_MockResponse(200, {"results": [{"vulns": []}]}))
    lookup = OSVLookup(tmp_path, client=client, enable_cache=True)  # type: ignore[arg-type]
    ref = _pkg("click", "8.1")
    diff = ManifestDiff(added=[ref], bumped=[])
    await lookup.check_manifest_diff(diff, run_id="r1")
    assert len(client.calls) == 1
    # Second call should hit cache, not the API.
    await lookup.check_manifest_diff(diff, run_id="r2")
    assert len(client.calls) == 1  # no additional API call


@pytest.mark.asyncio
async def test_osv_cache_round_trips_vulnerability(tmp_path: Path) -> None:
    """Write vuln to cache, read it back, verify all fields survive."""
    client = _MockAsyncClient()
    lookup = OSVLookup(tmp_path, client=client, enable_cache=True)  # type: ignore[arg-type]
    ref = _pkg("foo", "1.0")
    vuln = OSVVulnerability(
        vuln_id="GHSA-cache-test",
        summary="cached vuln",
        details="more detail",
        cvss_score=7.5,
        references=["https://example.com/a"],
        affected_package="foo",
        affected_version="from 1.0",
        fixed_version="1.2.3",
        aliases=["CVE-2024-9999"],
        cwe_id="CWE-200",
    )
    lookup._save_cached(ref, [vuln])
    loaded = lookup._load_cached(ref)
    assert loaded is not None
    assert len(loaded) == 1
    loaded_vuln = loaded[0]
    assert loaded_vuln.vuln_id == "GHSA-cache-test"
    assert loaded_vuln.summary == "cached vuln"
    assert loaded_vuln.cvss_score == 7.5
    assert loaded_vuln.references == ["https://example.com/a"]
    assert loaded_vuln.aliases == ["CVE-2024-9999"]
    assert loaded_vuln.fixed_version == "1.2.3"
    # Phase 2 Task 2.10a: cwe_id round-trips through the cache.
    assert loaded_vuln.cwe_id == "CWE-200"


def test_osv_cache_miss_when_file_absent(tmp_path: Path) -> None:
    lookup = OSVLookup(tmp_path, enable_cache=True)
    ref = _pkg("never-cached", "1.0")
    assert lookup._load_cached(ref) is None


def test_osv_cache_miss_when_file_is_stale(tmp_path: Path) -> None:
    """A cache file older than the TTL should be treated as a miss."""
    import os

    lookup = OSVLookup(tmp_path, enable_cache=True)
    ref = _pkg("stale", "1.0")
    lookup._save_cached(ref, [])
    cache_file = lookup._cache_file_for(ref)
    assert cache_file.exists()
    # Backdate the mtime by 2 hours.
    old_time = cache_file.stat().st_mtime - (60 * 60 * 2)
    os.utime(cache_file, (old_time, old_time))
    assert lookup._load_cached(ref) is None


def test_osv_cache_file_corrupt_returns_miss(tmp_path: Path) -> None:
    """A corrupt cache file should be treated as a miss, not raise."""
    lookup = OSVLookup(tmp_path, enable_cache=True)
    ref = _pkg("corrupt", "1.0")
    cache_file = lookup._cache_file_for(ref)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text("{not json", encoding="utf-8")
    assert lookup._load_cached(ref) is None


def test_osv_cache_file_wrong_shape_returns_miss(tmp_path: Path) -> None:
    """A cache file with the wrong JSON shape (object, not list) is a miss."""
    lookup = OSVLookup(tmp_path, enable_cache=True)
    ref = _pkg("wrong-shape", "1.0")
    cache_file = lookup._cache_file_for(ref)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text('{"not": "a list"}', encoding="utf-8")
    assert lookup._load_cached(ref) is None


# --- Phase 2 Task 2.10a: per-vuln hydration cache + GET path ---------


def _full_vuln_response(
    vuln_id: str,
    *,
    cvss_score_str: str | None = None,
    text_severity: str = "HIGH",
    cwe_ids: list[str] | None = None,
) -> dict:
    """Build a realistic /v1/vulns/<id> response payload for tests."""
    payload: dict = {
        "id": vuln_id,
        "summary": f"Vulnerability {vuln_id}",
        "details": "Detailed explanation here.",
        "database_specific": {
            "severity": text_severity,
            "cwe_ids": cwe_ids or ["CWE-79"],
        },
        "references": [
            {"type": "ADVISORY", "url": f"https://github.com/advisories/{vuln_id}"},
        ],
    }
    if cvss_score_str is not None:
        payload["severity"] = [{"type": "CVSS_V3", "score": cvss_score_str}]
    return payload


@pytest.mark.asyncio
async def test_hydration_replaces_lean_with_full(tmp_path: Path) -> None:
    """End-to-end: lean batch response + rich /v1/vulns hydration
    yields findings with proper non-INFO severity.
    """
    lean_batch = {"results": [{"vulns": [{"id": "GHSA-hydrated-1", "modified": "2024-01-01"}]}]}
    full_vuln = _full_vuln_response(
        "GHSA-hydrated-1",
        cvss_score_str="9.8",
        text_severity="CRITICAL",
        cwe_ids=["CWE-79"],
    )
    client = _MockAsyncClient(
        response=_MockResponse(200, lean_batch),
        get_responses={"GHSA-hydrated-1": _MockResponse(200, full_vuln)},
    )
    lookup = OSVLookup(tmp_path, client=client, enable_cache=False)  # type: ignore[arg-type]
    diff = ManifestDiff(added=[_pkg("foo", "1.0")], bumped=[])
    findings = await lookup.check_manifest_diff(diff, run_id="r-hydrate")

    assert len(findings) == 1
    f = findings[0]
    assert f.kind == FindingKind.SCA
    assert f.severity == Severity.CRITICAL
    assert f.cvss_score == 9.8
    assert f.cwe_id == "CWE-79"
    # Both POST (batch) and GET (hydration) should have happened.
    assert len(client.calls) == 1
    assert len(client.get_calls) == 1
    assert "GHSA-hydrated-1" in client.get_calls[0]


@pytest.mark.asyncio
async def test_hydration_uses_text_severity_fallback_for_vector_only(
    tmp_path: Path,
) -> None:
    """When the hydrated response has a vector-only CVSS, the text
    label still drives the severity. This is the dogfood-caught
    scenario: GHSA-9hjg-9r4m-mvj7 has CVSS:3.1/... with no
    trailing score plus database_specific.severity HIGH.
    """
    lean_batch = {"results": [{"vulns": [{"id": "GHSA-vector-only", "modified": "2024"}]}]}
    full_vuln = _full_vuln_response(
        "GHSA-vector-only",
        cvss_score_str="CVSS:3.1/AV:N/AC:H/PR:N/UI:R/S:U/C:H/I:N/A:N",
        text_severity="HIGH",
    )
    client = _MockAsyncClient(
        response=_MockResponse(200, lean_batch),
        get_responses={"GHSA-vector-only": _MockResponse(200, full_vuln)},
    )
    lookup = OSVLookup(tmp_path, client=client, enable_cache=False)  # type: ignore[arg-type]
    diff = ManifestDiff(added=[_pkg("foo", "1.0")], bumped=[])
    findings = await lookup.check_manifest_diff(diff, run_id="r-vec")

    assert len(findings) == 1
    assert findings[0].severity == Severity.HIGH


@pytest.mark.asyncio
async def test_hydration_failure_keeps_lean_fallback(tmp_path: Path) -> None:
    """If hydration returns 500 (or anything non-200), the lean
    entry is preserved so the user still sees the finding."""
    lean_with_inline_data = {
        "results": [
            {
                "vulns": [
                    {
                        "id": "GHSA-no-hydrate",
                        "summary": "lean only",
                        "severity": [{"type": "CVSS_V3", "score": "5.5"}],
                    }
                ]
            }
        ]
    }
    client = _MockAsyncClient(
        response=_MockResponse(200, lean_with_inline_data),
        get_responses={"GHSA-no-hydrate": _MockResponse(500, "server error")},
    )
    lookup = OSVLookup(tmp_path, client=client, enable_cache=False)  # type: ignore[arg-type]
    diff = ManifestDiff(added=[_pkg("foo", "1.0")], bumped=[])
    findings = await lookup.check_manifest_diff(diff, run_id="r-fail")

    assert len(findings) == 1
    # Severity from the inline lean entry, not zero.
    assert findings[0].cvss_score == 5.5
    assert findings[0].severity == Severity.MEDIUM
    # GET was attempted but returned 500.
    assert len(client.get_calls) == 1


@pytest.mark.asyncio
async def test_hydration_network_error_keeps_lean_fallback(tmp_path: Path) -> None:
    """A raised exception during hydration must NOT take down the batch."""
    lean_with_inline_data = {
        "results": [
            {
                "vulns": [
                    {
                        "id": "GHSA-net-fail",
                        "severity": [{"type": "CVSS_V3", "score": "6.0"}],
                    }
                ]
            }
        ]
    }
    client = _MockAsyncClient(
        response=_MockResponse(200, lean_with_inline_data),
        raise_on_get=httpx.ConnectError("DNS failure"),
    )
    lookup = OSVLookup(tmp_path, client=client, enable_cache=False)  # type: ignore[arg-type]
    diff = ManifestDiff(added=[_pkg("foo", "1.0")], bumped=[])
    findings = await lookup.check_manifest_diff(diff, run_id="r-net")

    assert len(findings) == 1
    assert findings[0].cvss_score == 6.0


@pytest.mark.asyncio
async def test_hydration_cache_hit_skips_get_call(tmp_path: Path) -> None:
    """Pre-populated per-vuln cache should skip the GET API call."""
    lean_batch = {"results": [{"vulns": [{"id": "GHSA-cached-vuln", "modified": "2024"}]}]}
    client = _MockAsyncClient(response=_MockResponse(200, lean_batch))
    lookup = OSVLookup(tmp_path, client=client, enable_cache=True)  # type: ignore[arg-type]

    # Pre-populate the per-vuln raw cache with a full response.
    full_vuln = _full_vuln_response(
        "GHSA-cached-vuln",
        cvss_score_str="8.8",
        text_severity="HIGH",
    )
    lookup._save_cached_vuln_raw("GHSA-cached-vuln", full_vuln)

    diff = ManifestDiff(added=[_pkg("foo", "1.0")], bumped=[])
    findings = await lookup.check_manifest_diff(diff, run_id="r-cached")

    assert len(findings) == 1
    assert findings[0].cvss_score == 8.8
    # POST happened (batch query), GET (hydration) was SKIPPED
    # because the per-vuln cache hit.
    assert len(client.calls) == 1
    assert len(client.get_calls) == 0


@pytest.mark.asyncio
async def test_hydration_cache_populates_after_first_call(tmp_path: Path) -> None:
    """First call hits the API; second call hits the cache."""
    lean_batch = {"results": [{"vulns": [{"id": "GHSA-populate", "modified": "2024"}]}]}
    full_vuln = _full_vuln_response("GHSA-populate", cvss_score_str="7.0", text_severity="HIGH")
    client = _MockAsyncClient(
        response=_MockResponse(200, lean_batch),
        get_responses={"GHSA-populate": _MockResponse(200, full_vuln)},
    )
    lookup = OSVLookup(tmp_path, client=client, enable_cache=True)  # type: ignore[arg-type]
    diff = ManifestDiff(added=[_pkg("foo", "1.0")], bumped=[])

    # First scan: GET happens.
    await lookup.check_manifest_diff(diff, run_id="r1")
    assert len(client.get_calls) == 1

    # Bust the manifest snapshot so the SECOND scan re-queries
    # the batch endpoint.
    snap = tmp_path / ".tailtest" / "cache" / "manifests"
    if (snap / "pyproject.toml.snap").exists():
        (snap / "pyproject.toml.snap").unlink()

    # Second scan: per-vuln cache hit, no second GET.
    await lookup.check_manifest_diff(diff, run_id="r2")
    assert len(client.get_calls) == 1  # still 1


# --- Phase 2 Task 2.10a: dedup by alias chain ------------------------


def test_dedup_vulns_drops_alias_duplicates() -> None:
    """A PYSEC entry whose id appears in a GHSA's aliases must drop."""
    from tailtest.security.sca.osv import _dedup_vulns_by_alias

    ghsa = OSVVulnerability(
        vuln_id="GHSA-cfj3-7x9c-4p3h",
        summary="GHSA entry",
        details="",
        cvss_score=5.0,
        aliases=["CVE-2014-1829", "PYSEC-2014-13"],
    )
    pysec = OSVVulnerability(
        vuln_id="PYSEC-2014-13",
        summary="PYSEC duplicate",
        details="",
        cvss_score=0.0,
    )
    cve = OSVVulnerability(
        vuln_id="CVE-2014-1829",
        summary="CVE duplicate",
        details="",
        cvss_score=0.0,
    )
    deduped = _dedup_vulns_by_alias([ghsa, pysec, cve])
    assert len(deduped) == 1
    assert deduped[0].vuln_id == "GHSA-cfj3-7x9c-4p3h"


def test_dedup_vulns_first_wins() -> None:
    """First-occurrence wins because OSV puts authoritative entries first."""
    from tailtest.security.sca.osv import _dedup_vulns_by_alias

    a = OSVVulnerability(
        vuln_id="A-1", summary="first", details="", cvss_score=5.0, aliases=["B-1"]
    )
    b = OSVVulnerability(vuln_id="B-1", summary="second", details="", cvss_score=9.0)
    deduped = _dedup_vulns_by_alias([a, b])
    assert len(deduped) == 1
    assert deduped[0].vuln_id == "A-1"
    assert deduped[0].cvss_score == 5.0  # NOT 9.0


def test_dedup_vulns_keeps_distinct_findings() -> None:
    """Two genuinely-different vulns must both survive dedup."""
    from tailtest.security.sca.osv import _dedup_vulns_by_alias

    a = OSVVulnerability(vuln_id="GHSA-aaaa", summary="a", details="", cvss_score=5.0)
    b = OSVVulnerability(vuln_id="GHSA-bbbb", summary="b", details="", cvss_score=7.0)
    deduped = _dedup_vulns_by_alias([a, b])
    assert len(deduped) == 2


def test_dedup_vulns_empty_input() -> None:
    from tailtest.security.sca.osv import _dedup_vulns_by_alias

    assert _dedup_vulns_by_alias([]) == []


def test_dedup_vulns_skips_empty_id() -> None:
    """A vuln with empty vuln_id is dropped silently."""
    from tailtest.security.sca.osv import _dedup_vulns_by_alias

    bad = OSVVulnerability(vuln_id="", summary="x", details="", cvss_score=0.0)
    good = OSVVulnerability(vuln_id="GHSA-x", summary="y", details="", cvss_score=5.0)
    deduped = _dedup_vulns_by_alias([bad, good])
    assert len(deduped) == 1
    assert deduped[0].vuln_id == "GHSA-x"


@pytest.mark.asyncio
async def test_hydration_dedupes_pysec_duplicate_of_ghsa(tmp_path: Path) -> None:
    """End-to-end: a lean batch with both GHSA and its PYSEC alias
    yields ONE finding after hydration + dedup."""
    lean_batch = {
        "results": [
            {
                "vulns": [
                    {"id": "GHSA-cfj3-7x9c-4p3h", "modified": "2024"},
                    {"id": "PYSEC-2014-13", "modified": "2024"},
                ]
            }
        ]
    }
    full_ghsa = _full_vuln_response("GHSA-cfj3-7x9c-4p3h", text_severity="MODERATE")
    full_ghsa["aliases"] = ["CVE-2014-1829", "PYSEC-2014-13"]
    full_pysec = {
        "id": "PYSEC-2014-13",
        "summary": "duplicate",
        "details": "",
        "aliases": ["CVE-2014-1829", "GHSA-cfj3-7x9c-4p3h"],
    }
    client = _MockAsyncClient(
        response=_MockResponse(200, lean_batch),
        get_responses={
            "GHSA-cfj3-7x9c-4p3h": _MockResponse(200, full_ghsa),
            "PYSEC-2014-13": _MockResponse(200, full_pysec),
        },
    )
    lookup = OSVLookup(tmp_path, client=client, enable_cache=False)  # type: ignore[arg-type]
    diff = ManifestDiff(added=[_pkg("requests", "2.0.0")], bumped=[])
    findings = await lookup.check_manifest_diff(diff, run_id="r-dedup")

    # ONE finding (GHSA wins; PYSEC is dropped as alias).
    assert len(findings) == 1
    assert findings[0].rule_id == "osv::GHSA-cfj3-7x9c-4p3h"


@pytest.mark.asyncio
async def test_hydration_dedupes_same_vuln_id_across_packages(tmp_path: Path) -> None:
    """If two packages share a vuln id, only one GET is made."""
    lean_batch = {
        "results": [
            {"vulns": [{"id": "GHSA-shared", "modified": "2024"}]},
            {"vulns": [{"id": "GHSA-shared", "modified": "2024"}]},
        ]
    }
    full_vuln = _full_vuln_response("GHSA-shared", cvss_score_str="7.5", text_severity="HIGH")
    client = _MockAsyncClient(
        response=_MockResponse(200, lean_batch),
        get_responses={"GHSA-shared": _MockResponse(200, full_vuln)},
    )
    lookup = OSVLookup(tmp_path, client=client, enable_cache=False)  # type: ignore[arg-type]
    diff = ManifestDiff(added=[_pkg("foo", "1.0"), _pkg("bar", "2.0")], bumped=[])
    findings = await lookup.check_manifest_diff(diff, run_id="r-dedup")

    # Two findings (one per package).
    assert len(findings) == 2
    # But only ONE GET (hydration deduped by vuln_id).
    assert len(client.get_calls) == 1


def test_load_cached_vuln_raw_missing_returns_none(tmp_path: Path) -> None:
    lookup = OSVLookup(tmp_path, enable_cache=True)
    assert lookup._load_cached_vuln_raw("GHSA-missing") is None


def test_load_cached_vuln_raw_corrupt_returns_none(tmp_path: Path) -> None:
    lookup = OSVLookup(tmp_path, enable_cache=True)
    cache_file = lookup._vuln_cache_file_for("GHSA-corrupt")
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text("{not json", encoding="utf-8")
    assert lookup._load_cached_vuln_raw("GHSA-corrupt") is None


def test_load_cached_vuln_raw_wrong_shape_returns_none(tmp_path: Path) -> None:
    lookup = OSVLookup(tmp_path, enable_cache=True)
    cache_file = lookup._vuln_cache_file_for("GHSA-shape")
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text("[1, 2, 3]", encoding="utf-8")
    assert lookup._load_cached_vuln_raw("GHSA-shape") is None


def test_save_then_load_cached_vuln_raw_round_trips(tmp_path: Path) -> None:
    lookup = OSVLookup(tmp_path, enable_cache=True)
    raw = _full_vuln_response("GHSA-round", cvss_score_str="6.0")
    lookup._save_cached_vuln_raw("GHSA-round", raw)
    loaded = lookup._load_cached_vuln_raw("GHSA-round")
    assert loaded is not None
    assert loaded["id"] == "GHSA-round"
    assert loaded["database_specific"]["cwe_ids"] == ["CWE-79"]


def test_hydration_finding_carries_advisory_url_from_full_response(
    tmp_path: Path,
) -> None:
    """The advisory_url field on the Finding comes from the FIRST
    reference URL in the hydrated response, not the lean entry."""
    import asyncio

    lean_batch = {"results": [{"vulns": [{"id": "GHSA-link", "modified": "2024"}]}]}
    full_vuln = _full_vuln_response("GHSA-link", cvss_score_str="7.5", text_severity="HIGH")
    client = _MockAsyncClient(
        response=_MockResponse(200, lean_batch),
        get_responses={"GHSA-link": _MockResponse(200, full_vuln)},
    )
    lookup = OSVLookup(tmp_path, client=client, enable_cache=False)  # type: ignore[arg-type]
    diff = ManifestDiff(added=[_pkg("foo", "1.0")], bumped=[])
    findings = asyncio.run(lookup.check_manifest_diff(diff, run_id="r-link"))
    assert len(findings) == 1
    assert findings[0].advisory_url == "https://github.com/advisories/GHSA-link"
    assert findings[0].cwe_id == "CWE-79"


# --- Cargo.lock parsing -----------------------------------------------


_CARGO_LOCK_SAMPLE = """\
# This file is automatically @generated by Cargo.
# It is not intended for manual editing.
version = 3

[[package]]
name = "tokio"
version = "1.37.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "abcdef"
dependencies = [
 "bytes",
]

[[package]]
name = "bytes"
version = "1.6.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "123456"

[[package]]
name = "my-crate"
version = "0.1.0"
dependencies = [
 "tokio",
]
"""


def test_parse_cargo_lock_returns_registry_packages() -> None:
    refs = parse_cargo_lock(_CARGO_LOCK_SAMPLE)
    names = {r.name for r in refs}
    # Registry packages are included.
    assert "tokio" in names
    assert "bytes" in names
    # Workspace-local (no source) is excluded.
    assert "my-crate" not in names


def test_parse_cargo_lock_ecosystem_is_crates_io() -> None:
    refs = parse_cargo_lock(_CARGO_LOCK_SAMPLE)
    assert all(r.ecosystem == "crates.io" for r in refs)


def test_parse_cargo_lock_source_spec_is_cargo_lock() -> None:
    refs = parse_cargo_lock(_CARGO_LOCK_SAMPLE)
    assert all(r.source_spec == "Cargo.lock" for r in refs)


def test_parse_cargo_lock_versions_are_correct() -> None:
    refs = parse_cargo_lock(_CARGO_LOCK_SAMPLE)
    by_name = {r.name: r.version for r in refs}
    assert by_name["tokio"] == "1.37.0"
    assert by_name["bytes"] == "1.6.0"


def test_parse_cargo_lock_empty_returns_empty_list() -> None:
    assert parse_cargo_lock("") == []
    assert parse_cargo_lock("   \n") == []


def test_parse_cargo_lock_malformed_returns_empty_list() -> None:
    assert parse_cargo_lock("not = valid = toml = at all [") == []


def test_parse_cargo_lock_no_packages_section() -> None:
    assert parse_cargo_lock("version = 3\n") == []


def test_parse_cargo_lock_skips_git_deps() -> None:
    lock_with_git = """\
version = 3

[[package]]
name = "mygit"
version = "0.1.0"
source = "git+https://github.com/example/mygit?rev=abc123"

[[package]]
name = "normal"
version = "1.0.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "abc"
"""
    refs = parse_cargo_lock(lock_with_git)
    names = {r.name for r in refs}
    assert "normal" in names
    assert "mygit" not in names


def test_diff_manifests_works_with_cargo_lock_refs() -> None:
    """diff_manifests is ecosystem-agnostic; it works for Cargo.lock refs too."""
    old_lock = """\
version = 3
[[package]]
name = "serde"
version = "1.0.100"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "aaa"
"""
    new_lock = """\
version = 3
[[package]]
name = "serde"
version = "1.0.200"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "bbb"

[[package]]
name = "anyhow"
version = "1.0.80"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "ccc"
"""
    diff = diff_manifests(parse_cargo_lock(old_lock), parse_cargo_lock(new_lock))
    assert len(diff.added) == 1
    assert diff.added[0].name == "anyhow"
    assert len(diff.bumped) == 1
    old_ref, new_ref = diff.bumped[0]
    assert old_ref.version == "1.0.100"
    assert new_ref.version == "1.0.200"


# --- cargo audit integration ------------------------------------------


def test_cargo_audit_available_returns_bool() -> None:
    """cargo_audit_available() must return a bool without raising."""
    result = cargo_audit_available()
    assert isinstance(result, bool)


def test_parse_cargo_audit_json_empty_returns_empty_list() -> None:
    assert _parse_cargo_audit_json("", run_id="r1") == []
    assert _parse_cargo_audit_json("   ", run_id="r1") == []


def test_parse_cargo_audit_json_malformed_returns_empty_list() -> None:
    assert _parse_cargo_audit_json("not json", run_id="r1") == []


def test_parse_cargo_audit_json_no_vulnerabilities() -> None:
    data = {"vulnerabilities": {"found": False, "count": 0, "list": []}}
    findings = _parse_cargo_audit_json(json.dumps(data), run_id="r1")
    assert findings == []


def test_parse_cargo_audit_json_one_vuln() -> None:
    data = {
        "vulnerabilities": {
            "found": True,
            "count": 1,
            "list": [
                {
                    "advisory": {
                        "id": "RUSTSEC-2024-0001",
                        "package": "foo",
                        "title": "Use-after-free in foo",
                        "description": "A serious bug.",
                        "severity": "high",
                        "url": "https://rustsec.org/advisories/RUSTSEC-2024-0001.html",
                    },
                    "versions": {"patched": [">=1.2.3"], "unaffected": []},
                    "package": {"name": "foo", "version": "1.0.0"},
                }
            ],
        }
    }
    findings = _parse_cargo_audit_json(json.dumps(data), run_id="r1")
    assert len(findings) == 1
    f = findings[0]
    assert "RUSTSEC-2024-0001" in f.rule_id
    assert "foo" in f.message
    assert "Use-after-free" in f.message
    assert f.severity.value in {"critical", "high", "medium", "low"}
    assert f.fix_suggestion is not None
    assert "1.2.3" in f.fix_suggestion


def test_map_rustsec_severity_all_levels() -> None:
    from tailtest.core.findings.schema import Severity

    assert _map_rustsec_severity("critical") == Severity.CRITICAL
    assert _map_rustsec_severity("high") == Severity.HIGH
    assert _map_rustsec_severity("medium") == Severity.MEDIUM
    assert _map_rustsec_severity("low") == Severity.LOW
    assert _map_rustsec_severity("unknown") == Severity.MEDIUM  # default
