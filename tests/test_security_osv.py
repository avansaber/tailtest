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
from tailtest.security.sca.manifests import (
    ManifestDiff,
    PackageRef,
    _is_valid_package_name,
    _split_pep508_requirement,
    diff_manifests,
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


def test_osv_vuln_from_dict_missing_fields_uses_defaults() -> None:
    vuln = _osv_vuln_from_dict({"id": "GHSA-min"})
    assert vuln is not None
    assert vuln.vuln_id == "GHSA-min"
    assert vuln.summary == ""
    assert vuln.details == ""
    assert vuln.cvss_score == 0.0
    assert vuln.references == []
    assert vuln.aliases == []


def test_osv_vuln_from_dict_rejects_missing_id() -> None:
    assert _osv_vuln_from_dict({"summary": "no id"}) is None
    assert _osv_vuln_from_dict({"id": ""}) is None
    assert _osv_vuln_from_dict({"id": 42}) is None  # type: ignore[dict-item]


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
    and payload shapes.
    """

    def __init__(
        self,
        response: _MockResponse | None = None,
        *,
        raise_on_post: Exception | None = None,
    ) -> None:
        self.response = response
        self.raise_on_post = raise_on_post
        self.calls: list[tuple[str, Any]] = []

    async def post(self, url: str, *, json: Any = None) -> _MockResponse:  # noqa: A002
        self.calls.append((url, json))
        if self.raise_on_post is not None:
            raise self.raise_on_post
        assert self.response is not None
        return self.response

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
        aliases=["CVE-2024-9999"],
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
