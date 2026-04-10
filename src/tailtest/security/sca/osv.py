"""OSVLookup, SCA via the OSV API (Phase 2 Task 2.3).

Queries `https://api.osv.dev/v1/querybatch` with a list of
dependency refs and returns vulnerability `Finding` objects.

Design notes:

- **Free, public, no API key.** OSV aggregates GitHub Advisory
  Database, PyPI Advisory Database, RustSec, Go vulnDB, and more.
  Per ADR research, OSV is the right choice over Snyk (paid +
  aggressive false positives) or GitHub Advisory directly
  (subset of OSV's data).
- **Batch queries via `/v1/querybatch`.** The batch endpoint
  accepts up to 1000 package queries per call and returns a
  matching list of vulnerability arrays. More efficient than
  per-package queries when a diff touches several dependencies
  at once.
- **Local file cache at `.tailtest/cache/osv/`.** Per package key
  (`<ecosystem>/<name>@<version>`). Cache entries last one hour
  by default; callers can bust the cache by deleting the
  directory. Keeps the hot loop from hammering OSV on repeat
  runs over the same dependency set.
- **Severity mapping** from the OSV `severity` field. OSV emits
  one of `CVSS_V3` or `CVSS_V2` score strings. We map to
  unified severity by the numeric score:
  `>= 9.0` -> CRITICAL
  `>= 7.0` -> HIGH
  `>= 4.0` -> MEDIUM
  `> 0.0`  -> LOW
  `== 0.0` or missing -> INFO
- **Graceful fallback when the API is unreachable** (network
  down, OSV down, etc.). `check_manifest_diff()` returns an
  empty list after logging a warning. The hot loop never breaks
  because SCA could not reach the API.
- **Dependency on httpx** which is already in the tailtest
  runtime dependency set (per pyproject.toml).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from tailtest.core.findings.schema import Finding, FindingKind, Severity
from tailtest.security.sca.manifests import ManifestDiff, PackageRef

logger = logging.getLogger(__name__)


OSV_QUERY_BATCH_URL = "https://api.osv.dev/v1/querybatch"
OSV_VULNS_URL = "https://api.osv.dev/v1/vulns"

# Cache TTL: one hour. Long enough to avoid repeat queries during
# a single development session; short enough that new advisories
# surface within the same day.
_CACHE_TTL_SECONDS = 60 * 60

# Maximum packages per batch query. OSV supports up to 1000 but we
# use 100 to keep individual requests small and cache-friendly.
_MAX_BATCH_SIZE = 100


class OSVNotAvailable(RuntimeError):
    """Raised internally when OSV cannot be reached."""


@dataclass(frozen=True)
class OSVVulnerability:
    """One vulnerability entry from an OSV response.

    Kept separate from ``Finding`` so the API-parsing layer stays
    pure and the ``Finding`` construction can be tested with
    canned inputs. Mirrors ``_GitleaksHit`` and ``_SemgrepHit`` in
    the sibling wrappers.
    """

    vuln_id: str  # e.g. GHSA-xxxx or CVE-2024-...
    summary: str
    details: str
    cvss_score: float  # 0.0 to 10.0, 0.0 when unknown
    references: list[str] = field(default_factory=list)
    affected_package: str = ""
    affected_version: str = ""
    fixed_version: str = ""  # concrete version where the vuln was fixed
    aliases: list[str] = field(default_factory=list)
    cwe_id: str = ""  # Phase 2 Task 2.10a: pulled from database_specific.cwe_ids


class OSVLookup:
    """OSV-backed SCA scanner.

    Parameters
    ----------
    project_root:
        Project root used to locate the on-disk cache at
        ``<project_root>/.tailtest/cache/osv/``.
    client:
        Optional httpx AsyncClient. If None, one is created per
        call. Tests inject a mock client.
    timeout_seconds:
        HTTP timeout for the OSV API call. Default 15 seconds.
    enable_cache:
        When True (default), look up + populate the on-disk cache
        for each package query. When False, every call hits the
        API. Tests disable the cache when they want to assert a
        specific number of API calls.
    """

    def __init__(
        self,
        project_root: Path,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 15.0,
        enable_cache: bool = True,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self._client = client
        self.timeout_seconds = timeout_seconds
        self.enable_cache = enable_cache

    # --- Public API -------------------------------------------------

    async def check_manifest_diff(
        self,
        diff: ManifestDiff,
        *,
        run_id: str,
    ) -> list[Finding]:
        """Query OSV for every changed package in ``diff`` and return Findings.

        Returns an empty list when:
        - The diff has no changed refs.
        - Every queried package is cached as clean.
        - The OSV API is unreachable (logged, not raised).
        - Every cached response and every API response returns
          zero vulnerabilities.
        """
        changed = diff.changed_refs
        if not changed:
            return []

        findings: list[Finding] = []
        uncached_refs: list[PackageRef] = []

        # First pass: resolve cache hits.
        for ref in changed:
            cached = self._load_cached(ref) if self.enable_cache else None
            if cached is None:
                uncached_refs.append(ref)
                continue
            for vuln in cached:
                findings.append(self._vuln_to_finding(vuln, ref=ref, run_id=run_id))

        if not uncached_refs:
            return findings

        # Second pass: hit OSV for uncached refs.
        try:
            api_results = await self._query_batch(uncached_refs)
        except OSVNotAvailable as exc:
            logger.info("OSV lookup skipped: %s", exc)
            return findings
        except Exception as exc:  # noqa: BLE001, defensive
            logger.warning("OSV query failed: %s", exc)
            return findings

        for ref, vulns in zip(uncached_refs, api_results, strict=False):
            if self.enable_cache:
                self._save_cached(ref, vulns)
            for vuln in vulns:
                findings.append(self._vuln_to_finding(vuln, ref=ref, run_id=run_id))

        return findings

    async def check_imports(
        self,
        root: str | Path,
        *,
        run_id: str,
    ) -> list[Finding]:
        """Scan third-party imports in *root* for known vulnerabilities.

        Uses import-based discovery (no manifest required). Only checks
        packages with a known PyPI mapping to avoid false positives. Returns
        findings in the same format as ``check_manifest_diff``.

        Returns an empty list when:
        - No known third-party imports are found.
        - Every queried package is cached as clean.
        - The OSV API is unreachable (logged, not raised).
        """
        from .imports import discover_imports

        import_map = discover_imports(root)
        if not import_map:
            return []

        # Build PackageRef list from imports. Version is unknown ("") for
        # import-only projects -- OSV returns all known vulns for unversioned
        # queries, which is conservative but correct for manifest-free projects.
        packages = [
            PackageRef(name=pypi_name, version="", ecosystem="PyPI")
            for pypi_name in import_map.values()
        ]

        findings: list[Finding] = []
        uncached_refs: list[PackageRef] = []

        # First pass: resolve cache hits.
        for ref in packages:
            cached = self._load_cached(ref) if self.enable_cache else None
            if cached is None:
                uncached_refs.append(ref)
                continue
            for vuln in cached:
                findings.append(self._vuln_to_finding(vuln, ref=ref, run_id=run_id))

        if not uncached_refs:
            return findings

        # Second pass: hit OSV for uncached refs.
        try:
            api_results = await self._query_batch(uncached_refs)
        except OSVNotAvailable as exc:
            logger.info("OSV import lookup skipped: %s", exc)
            return findings
        except Exception as exc:  # noqa: BLE001, defensive
            logger.warning("OSV import query failed: %s", exc)
            return findings

        for ref, vulns in zip(uncached_refs, api_results, strict=False):
            if self.enable_cache:
                self._save_cached(ref, vulns)
            for vuln in vulns:
                findings.append(self._vuln_to_finding(vuln, ref=ref, run_id=run_id))

        return findings

    # --- API call ---------------------------------------------------

    async def _query_batch(self, refs: list[PackageRef]) -> list[list[OSVVulnerability]]:
        """Send a batched query to OSV and return parallel vuln lists.

        Returns one list of vulnerabilities per input ref in the
        same order. When a single request times out, raises
        OSVNotAvailable so the caller can log-and-continue.
        """
        if not refs:
            return []

        # Chunk into batches of at most _MAX_BATCH_SIZE queries each.
        all_results: list[list[OSVVulnerability]] = []
        for start in range(0, len(refs), _MAX_BATCH_SIZE):
            batch = refs[start : start + _MAX_BATCH_SIZE]
            batch_results = await self._send_one_batch(batch)
            all_results.extend(batch_results)
        return all_results

    async def _send_one_batch(self, batch: list[PackageRef]) -> list[list[OSVVulnerability]]:
        """Send a single OSV /v1/querybatch request and parse the response.

        Phase 2 Task 2.10a: after the lean batch parse, walks each
        unique vuln id through ``_hydrate_vuln`` to fetch the full
        details (severity, cwe_ids, references, affected ranges)
        from ``/v1/vulns/<id>``. The client is held open across
        the hydration loop so we don't pay an extra connection
        setup per vuln.
        """
        queries = [
            {
                "package": {"name": ref.name, "ecosystem": ref.ecosystem},
                "version": _normalize_version_for_osv(ref.version),
            }
            for ref in batch
        ]
        payload = {"queries": queries}

        client = self._client
        owned_client = False
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout_seconds)
            owned_client = True

        try:
            try:
                response = await client.post(OSV_QUERY_BATCH_URL, json=payload)
            except httpx.TimeoutException as exc:
                raise OSVNotAvailable(f"OSV request timed out: {exc}") from exc
            except httpx.HTTPError as exc:
                raise OSVNotAvailable(f"OSV HTTP error: {exc}") from exc

            if response.status_code != 200:
                raise OSVNotAvailable(
                    f"OSV returned status {response.status_code}: {response.text[:200]}"
                )

            try:
                data = response.json()
            except json.JSONDecodeError as exc:
                raise OSVNotAvailable(f"OSV returned non-JSON response: {exc}") from exc

            lean_results = parse_osv_batch_response(data, batch_size=len(batch))

            # Hydrate each unique vuln so the SCA findings carry
            # severity, cwe_id, and the full reference list. Without
            # this step every finding would land at INFO because the
            # batch endpoint returns lean entries.
            return await self._hydrate_results(lean_results, client)
        finally:
            if owned_client:
                await client.aclose()

    # --- Hydration (Phase 2 Task 2.10a) -----------------------------

    async def _hydrate_results(
        self,
        lean_results: list[list[OSVVulnerability]],
        client: httpx.AsyncClient,
    ) -> list[list[OSVVulnerability]]:
        """Replace lean vulns with full details from ``/v1/vulns/<id>``.

        The OSV ``/v1/querybatch`` endpoint returns lean entries
        (id + modified only). To get severity, cwe_ids, the full
        reference list, and the affected ranges with both
        introduced and fixed events, we follow up with one
        ``/v1/vulns/<id>`` call per unique vuln. Cached on disk
        at ``.tailtest/cache/osv-vulns/<sha>.json`` so repeat
        scans skip the API entirely.

        After hydration, runs each per-batch list through
        :func:`_dedup_vulns_by_alias` so the user does not see
        the same vulnerability under multiple ids (e.g.,
        ``GHSA-cfj3-7x9c-4p3h`` AND ``PYSEC-2014-13`` for
        CVE-2014-1829). The OSV batch endpoint typically returns
        more authoritative entries (GHSA, with severity + CWE)
        ahead of less authoritative ones (PYSEC), so a "first
        wins" dedup keeps the richer entry.

        On any per-vuln failure (network error, non-200, parse
        error) we keep the lean entry as a fallback so the user
        still sees the finding even when hydration is degraded.
        Hydration is best-effort: it should never break the hot
        loop.
        """
        # Collect unique vuln ids in stable order so the test
        # asserts on call shape are deterministic.
        unique_ids: dict[str, OSVVulnerability] = {}
        for vuln_list in lean_results:
            for vuln in vuln_list:
                if vuln.vuln_id and vuln.vuln_id not in unique_ids:
                    unique_ids[vuln.vuln_id] = vuln

        if not unique_ids:
            return lean_results

        hydrated: dict[str, OSVVulnerability] = {}
        for vuln_id, lean in unique_ids.items():
            full = await self._hydrate_vuln(vuln_id, client)
            hydrated[vuln_id] = full if full is not None else lean

        # Substitute hydrated entries back into the per-batch
        # structure so the caller's order matches the original
        # ref list, then dedup by alias chain.
        final: list[list[OSVVulnerability]] = []
        for vuln_list in lean_results:
            replaced = [hydrated.get(v.vuln_id, v) for v in vuln_list]
            final.append(_dedup_vulns_by_alias(replaced))
        return final

    async def _hydrate_vuln(
        self,
        vuln_id: str,
        client: httpx.AsyncClient,
    ) -> OSVVulnerability | None:
        """Fetch ``/v1/vulns/<id>`` and parse into a full ``OSVVulnerability``.

        Cache hit returns immediately. Cache miss hits the API,
        saves the raw response, and parses it. Returns None on
        any failure so the caller falls back to the lean entry.

        We cache the RAW API response (not the deserialized
        ``OSVVulnerability``) on purpose: a future improvement to
        ``_osv_vuln_from_dict`` (e.g., extracting more fields)
        will automatically apply to existing cache entries on
        next read, with no migration step.
        """
        if self.enable_cache:
            cached = self._load_cached_vuln_raw(vuln_id)
            if cached is not None:
                return _osv_vuln_from_dict(cached)

        url = f"{OSV_VULNS_URL}/{vuln_id}"
        try:
            response = await client.get(url)
        except Exception as exc:  # noqa: BLE001
            # Hydration is best-effort. ANY exception (httpx
            # network error, mock client missing get(), etc.)
            # falls back to the lean entry so the hot loop never
            # breaks because the per-vuln lookup failed.
            logger.debug("OSV hydrate failed for %s: %s", vuln_id, exc)
            return None

        if response.status_code != 200:
            logger.debug(
                "OSV hydrate non-200 for %s: %d",
                vuln_id,
                response.status_code,
            )
            return None

        try:
            data = response.json()
        except json.JSONDecodeError:
            logger.debug("OSV hydrate non-JSON for %s", vuln_id)
            return None

        if not isinstance(data, dict):
            return None

        if self.enable_cache:
            self._save_cached_vuln_raw(vuln_id, data)

        return _osv_vuln_from_dict(data)

    def _vuln_cache_dir(self) -> Path:
        return self.project_root / ".tailtest" / "cache" / "osv-vulns"

    def _vuln_cache_file_for(self, vuln_id: str) -> Path:
        digest = hashlib.sha256(vuln_id.encode("utf-8")).hexdigest()[:32]
        return self._vuln_cache_dir() / f"{digest}.json"

    def _load_cached_vuln_raw(self, vuln_id: str) -> dict[str, Any] | None:
        """Return the raw cached OSV API response or None on miss."""
        path = self._vuln_cache_file_for(vuln_id)
        if not path.exists():
            return None
        try:
            stat = path.stat()
        except OSError:
            return None
        if (time.time() - stat.st_mtime) > _CACHE_TTL_SECONDS:
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        return data

    def _save_cached_vuln_raw(self, vuln_id: str, raw: dict[str, Any]) -> None:
        """Persist the raw OSV API response. Swallow filesystem errors."""
        try:
            cache_dir = self._vuln_cache_dir()
            cache_dir.mkdir(parents=True, exist_ok=True)
            path = self._vuln_cache_file_for(vuln_id)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            logger.debug("OSV vuln cache write failed: %s", exc)

    # --- Cache ------------------------------------------------------

    def _cache_dir(self) -> Path:
        return self.project_root / ".tailtest" / "cache" / "osv"

    def _cache_file_for(self, ref: PackageRef) -> Path:
        # Hash-based filename keeps package names with unusual
        # characters (slashes, scoped npm packages, etc.) safe on
        # disk. Key shape: "<ecosystem>/<name>@<version>".
        key = f"{ref.ecosystem}/{ref.name}@{ref.version}"
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self._cache_dir() / f"{digest}.json"

    def _load_cached(self, ref: PackageRef) -> list[OSVVulnerability] | None:
        """Return cached vulnerabilities or None on cache miss."""
        path = self._cache_file_for(ref)
        if not path.exists():
            return None
        try:
            stat = path.stat()
        except OSError:
            return None
        if (time.time() - stat.st_mtime) > _CACHE_TTL_SECONDS:
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, list):
            return None
        vulns: list[OSVVulnerability] = []
        for item in data:
            if isinstance(item, dict):
                vuln = _osv_vuln_from_cache_dict(item)
                if vuln is not None:
                    vulns.append(vuln)
        return vulns

    def _save_cached(self, ref: PackageRef, vulns: list[OSVVulnerability]) -> None:
        """Write a cache entry for ``ref``. Swallow filesystem errors."""
        try:
            cache_dir = self._cache_dir()
            cache_dir.mkdir(parents=True, exist_ok=True)
            path = self._cache_file_for(ref)
            payload = [_osv_vuln_to_dict(v) for v in vulns]
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(payload, sort_keys=True),
                encoding="utf-8",
            )
            tmp.replace(path)
        except OSError as exc:
            logger.debug("OSV cache write failed: %s", exc)

    # --- Finding conversion ----------------------------------------

    def _vuln_to_finding(self, vuln: OSVVulnerability, *, ref: PackageRef, run_id: str) -> Finding:
        """Turn an ``OSVVulnerability`` into a unified ``Finding``."""
        severity = _cvss_to_unified_severity(vuln.cvss_score)

        parts: list[str] = [
            f"{ref.name} {ref.version or '(unpinned)'}",
            vuln.vuln_id,
        ]
        if vuln.summary:
            parts.append(vuln.summary)
        message = _summarize(" : ".join(parts))

        doc_link = vuln.references[0] if vuln.references else None

        hint_bits: list[str] = []
        if vuln.cvss_score > 0:
            hint_bits.append(f"CVSS {vuln.cvss_score:.1f}")
        if vuln.vuln_id:
            hint_bits.append(vuln.vuln_id)
        if vuln.affected_version:
            hint_bits.append(f"affects {vuln.affected_version}")
        if not hint_bits:
            hint_bits.append("Review the advisory and upgrade or drop the dependency.")
        claude_hint = " | ".join(hint_bits)[:200]

        # SCA findings are project-level, not file-level. We point
        # at the manifest file as the "source" for baseline stability
        # since that is where the user fixes them.
        manifest_hint = "pyproject.toml" if ref.ecosystem == "PyPI" else "package.json"

        # Populate the security metadata fields so reporters and the
        # baseline manager can surface CVSS, package version, fix
        # version, and advisory URL without re-parsing the message.
        cvss_value = vuln.cvss_score if vuln.cvss_score > 0 else None

        return Finding.create(
            kind=FindingKind.SCA,
            severity=severity,
            file=Path(manifest_hint),
            line=0,
            message=message,
            run_id=run_id,
            rule_id=f"osv::{vuln.vuln_id}",
            doc_link=doc_link,
            claude_hint=claude_hint,
            cvss_score=cvss_value,
            cwe_id=vuln.cwe_id or None,
            package_name=ref.name,
            package_version=ref.version or None,
            fixed_version=vuln.fixed_version or None,
            advisory_url=doc_link,
        )


# --- Pure parser (testable without the network) ------------------------


def parse_osv_batch_response(data: Any, *, batch_size: int) -> list[list[OSVVulnerability]]:
    """Parse an OSV /v1/querybatch response into parallel vuln lists.

    The OSV batch response shape is:

        {
          "results": [
            {"vulns": [{"id": "GHSA-...", "modified": "..."}]},
            {"vulns": []},
            ...
          ]
        }

    The batch endpoint's result objects are LEAN: they only carry
    the vuln id, not the full details. A production implementation
    would follow up with a /v1/vulns/<id> query to get the full
    details. For Phase 2 alpha, we synthesize a minimal
    OSVVulnerability from the lean shape and rely on the `doc_link`
    to direct users to the full advisory. Future revisions can
    hydrate the lean results with a second API call.

    Returns an empty list when the response shape is unexpected.
    """
    if not isinstance(data, dict):
        logger.warning("OSV batch response was not an object")
        return [[] for _ in range(batch_size)]

    results = data.get("results")
    if not isinstance(results, list):
        return [[] for _ in range(batch_size)]

    parsed: list[list[OSVVulnerability]] = []
    for entry in results:
        if not isinstance(entry, dict):
            parsed.append([])
            continue
        raw_vulns = entry.get("vulns")
        if not isinstance(raw_vulns, list):
            parsed.append([])
            continue
        vulns: list[OSVVulnerability] = []
        for raw in raw_vulns:
            if not isinstance(raw, dict):
                continue
            vuln = _osv_vuln_from_dict(raw)
            if vuln is not None:
                vulns.append(vuln)
        parsed.append(vulns)

    # Pad with empty lists if the response had fewer results than
    # the batch size (spec says it should not, but be defensive).
    while len(parsed) < batch_size:
        parsed.append([])
    return parsed[:batch_size]


def _osv_vuln_from_dict(raw: dict[str, Any]) -> OSVVulnerability | None:
    """Build an ``OSVVulnerability`` from a raw OSV JSON dict.

    Phase 2 Task 2.10a: when the top-level ``severity`` field is
    missing or carries only a vector with no parseable numeric
    score, fall back to the GHSA ``database_specific.severity``
    text label and pull the canonical CWE from
    ``database_specific.cwe_ids``. The dogfood against
    ``requests==2.0.0`` proved that without these fallbacks every
    SCA finding mapped to severity INFO and sorted to the bottom.
    """
    vuln_id = raw.get("id")
    if not isinstance(vuln_id, str) or not vuln_id:
        return None

    summary = str(raw.get("summary") or "")
    details = str(raw.get("details") or "")

    cvss_score = _extract_highest_cvss_score(raw.get("severity"))

    # Phase 2 Task 2.10a: GHSA text-label fallback. Many advisories
    # carry a CVSS metric vector with no numeric score, or no CVSS
    # at all, but always include database_specific.severity as
    # LOW/MODERATE/HIGH/CRITICAL.
    database_specific = raw.get("database_specific")
    if not isinstance(database_specific, dict):
        database_specific = {}
    if cvss_score == 0.0:
        cvss_score = _text_severity_to_cvss(database_specific.get("severity"))

    # Pull the canonical CWE from database_specific.cwe_ids when
    # present. GHSA reliably populates this for vulnerabilities
    # tracked against a CWE; we keep only the first match so the
    # downstream Finding's cwe_id stays a single canonical value.
    cwe_id = ""
    raw_cwe_ids = database_specific.get("cwe_ids")
    if isinstance(raw_cwe_ids, list):
        for entry in raw_cwe_ids:
            if isinstance(entry, str) and entry.startswith("CWE-"):
                cwe_id = entry
                break

    references: list[str] = []
    raw_refs = raw.get("references")
    if isinstance(raw_refs, list):
        for item in raw_refs:
            if isinstance(item, dict):
                url = item.get("url")
                if isinstance(url, str) and url:
                    references.append(url)
            elif isinstance(item, str):
                references.append(item)

    aliases: list[str] = []
    raw_aliases = raw.get("aliases")
    if isinstance(raw_aliases, list):
        aliases = [str(a) for a in raw_aliases if isinstance(a, str)]

    affected_pkg = ""
    affected_version = ""
    fixed_version = ""
    raw_affected = raw.get("affected")
    if isinstance(raw_affected, list) and raw_affected:
        first = raw_affected[0]
        if isinstance(first, dict):
            pkg = first.get("package")
            if isinstance(pkg, dict):
                name = pkg.get("name")
                if isinstance(name, str):
                    affected_pkg = name
            ranges = first.get("ranges")
            if isinstance(ranges, list) and ranges:
                first_range = ranges[0]
                if isinstance(first_range, dict):
                    events = first_range.get("events")
                    if isinstance(events, list):
                        for event in events:
                            if not isinstance(event, dict):
                                continue
                            # OSV range events come as separate dicts:
                            # one `{"introduced": "..."}`, one
                            # `{"fixed": "..."}`. Walk the whole list
                            # so we pick up both.
                            if "introduced" in event and not affected_version:
                                affected_version = f"from {event['introduced']}"
                            if "fixed" in event and not fixed_version:
                                fixed_version = str(event["fixed"])

    return OSVVulnerability(
        vuln_id=vuln_id,
        summary=summary,
        details=details,
        cvss_score=cvss_score,
        references=references,
        affected_package=affected_pkg,
        affected_version=affected_version,
        fixed_version=fixed_version,
        aliases=aliases,
        cwe_id=cwe_id,
    )


def _osv_vuln_to_dict(vuln: OSVVulnerability) -> dict[str, Any]:
    """Serialize a vulnerability for the on-disk response cache.

    Note: this serializer is for the manifest-snapshot-keyed
    response cache (``.tailtest/cache/osv/<sha>.json``), which
    holds the deserialized OSVVulnerability shape. The per-vuln
    hydration cache (``.tailtest/cache/osv-vulns/<sha>.json``)
    stores the raw OSV API response instead, so a future parser
    improvement automatically applies to existing cache entries.
    """
    return {
        "id": vuln.vuln_id,
        "summary": vuln.summary,
        "details": vuln.details,
        "cvss_score": vuln.cvss_score,
        "references": list(vuln.references),
        "affected_package": vuln.affected_package,
        "affected_version": vuln.affected_version,
        "fixed_version": vuln.fixed_version,
        "aliases": list(vuln.aliases),
        "cwe_id": vuln.cwe_id,
    }


def _osv_vuln_from_cache_dict(raw: dict[str, Any]) -> OSVVulnerability | None:
    """Deserialize a vulnerability from the on-disk cache format.

    The cache format is the mirror of ``_osv_vuln_to_dict``: fields
    are stored with their native names (``cvss_score``, not a nested
    ``severity`` list). This is distinct from ``_osv_vuln_from_dict``
    which parses the live OSV API response shape.
    """
    vuln_id = raw.get("id")
    if not isinstance(vuln_id, str) or not vuln_id:
        return None

    references: list[str] = []
    raw_refs = raw.get("references")
    if isinstance(raw_refs, list):
        references = [r for r in raw_refs if isinstance(r, str)]

    aliases: list[str] = []
    raw_aliases = raw.get("aliases")
    if isinstance(raw_aliases, list):
        aliases = [a for a in raw_aliases if isinstance(a, str)]

    cvss_raw = raw.get("cvss_score")
    cvss_score = 0.0
    if isinstance(cvss_raw, (int, float)):
        cvss_score = float(cvss_raw)

    return OSVVulnerability(
        vuln_id=vuln_id,
        summary=str(raw.get("summary") or ""),
        details=str(raw.get("details") or ""),
        cvss_score=cvss_score,
        references=references,
        affected_package=str(raw.get("affected_package") or ""),
        affected_version=str(raw.get("affected_version") or ""),
        fixed_version=str(raw.get("fixed_version") or ""),
        aliases=aliases,
        cwe_id=str(raw.get("cwe_id") or ""),
    )


def _extract_highest_cvss_score(severity_field: Any) -> float:
    """Pull the highest CVSS score out of the OSV severity field.

    OSV's severity field shape:

        [
          {"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/.../7.5"},
          {"type": "CVSS_V2", "score": "..."}
        ]

    The score can be either a full vector string (which includes
    the numeric score at the end in various formats) or a plain
    numeric string. We conservatively extract any floating-point
    number in the string and return the highest one found.
    """
    if not isinstance(severity_field, list):
        return 0.0

    highest = 0.0
    for entry in severity_field:
        if not isinstance(entry, dict):
            continue
        score_str = entry.get("score")
        if not isinstance(score_str, str):
            continue
        score = _parse_cvss_score_string(score_str)
        if score > highest:
            highest = score
    return highest


# Phase 2 Task 2.10a: pre-compiled to skip the leading `CVSS:N.N/`
# version prefix when scanning a vector string for the score. Without
# this, the regex fallback below would mistake the CVSS spec version
# (e.g., 3.1) for the actual score and produce a wildly wrong
# severity. The dogfood fixture caught the bug end-to-end.
_CVSS_VERSION_PREFIX_RE = re.compile(r"^CVSS:\d+\.\d+/")
_CVSS_FLOAT_RE = re.compile(r"(\d+\.\d+)")


def _parse_cvss_score_string(score_str: str) -> float:
    """Pull a floating-point score out of a CVSS string.

    Handles both plain numeric strings (``"7.5"``) and vector
    strings (``"CVSS:3.1/AV:N/AC:L/.../7.5"``). Returns 0.0 when
    no parseable score is present.

    Phase 2 Task 2.10a: the leading ``CVSS:N.N/`` version prefix
    is stripped before any number-extraction so the regex
    fallback does not mistake the CVSS spec version (e.g.,
    ``3.1``) for the score. Many GHSA advisories carry only the
    metric vector with no trailing numeric score; for those the
    function correctly returns 0.0 and the caller falls back to
    the ``database_specific.severity`` text label via
    :func:`_text_severity_to_cvss`.
    """
    text = score_str.strip()
    if not text:
        return 0.0

    # Plain numeric string shortcut.
    try:
        return float(text)
    except ValueError:
        pass

    # Strip the `CVSS:N.N/` prefix so we don't return the version.
    prefix_match = _CVSS_VERSION_PREFIX_RE.match(text)
    text_without_prefix = text[prefix_match.end() :] if prefix_match else text

    # Vector string with trailing score: try to parse what
    # follows the last `/` as a float.
    if "/" in text_without_prefix:
        tail = text_without_prefix.rsplit("/", 1)[-1]
        try:
            return float(tail)
        except ValueError:
            pass

    # Last-resort fallback: extract any decimal number from the
    # prefix-less string and return the maximum.
    matches = _CVSS_FLOAT_RE.findall(text_without_prefix)
    if matches:
        try:
            return max(float(m) for m in matches)
        except ValueError:
            return 0.0
    return 0.0


def _dedup_vulns_by_alias(vulns: list[OSVVulnerability]) -> list[OSVVulnerability]:
    """Drop later vulns whose id appears in an earlier vuln's aliases.

    OSV often returns the same vulnerability under multiple ids:
    ``GHSA-cfj3-7x9c-4p3h``, ``PYSEC-2014-13``, and
    ``CVE-2014-1829`` all describe the same finding. Reporting
    all three to the user is noise.

    First-occurrence wins because the OSV batch endpoint
    typically lists more authoritative entries (GHSA, with
    severity + CWE) ahead of less authoritative ones (PYSEC,
    which typically lacks both). The dedup walks the list once
    in input order: each vuln gets added to the result, and
    every id in its aliases is recorded so a later duplicate
    can be skipped.

    Phase 2 Task 2.10a added this because the dogfood against
    ``requests==2.0.0`` produced 9 findings for what is really
    6 unique vulnerabilities (3 PYSEC duplicates of GHSAs).
    Without dedup the user sees the same advisory three times.
    """
    seen: set[str] = set()
    deduped: list[OSVVulnerability] = []
    for vuln in vulns:
        if not vuln.vuln_id:
            continue
        if vuln.vuln_id in seen:
            continue
        deduped.append(vuln)
        seen.add(vuln.vuln_id)
        for alias in vuln.aliases:
            if alias:
                seen.add(alias)
    return deduped


def _text_severity_to_cvss(text: Any) -> float:
    """Map a GHSA ``database_specific.severity`` label to a CVSS score.

    GHSA advisories include a text severity label
    (``LOW``/``MODERATE``/``HIGH``/``CRITICAL``) on every entry,
    even when the OSV ``severity`` field is missing or carries a
    metric-vector-without-numeric-score. We map each label to a
    score in the middle of its band so the existing
    :func:`_cvss_to_unified_severity` thresholds map to the
    correct unified severity:

    - ``CRITICAL`` -> 9.5  (``>= 9.0`` -> CRITICAL)
    - ``HIGH``     -> 7.5  (``>= 7.0`` -> HIGH)
    - ``MODERATE`` -> 5.0  (``>= 4.0`` -> MEDIUM)
    - ``LOW``      -> 2.0  (``> 0.0``  -> LOW)
    - anything else -> 0.0 (-> INFO)

    Phase 2 Task 2.10a added this fallback because the dogfood
    against ``requests==2.0.0`` showed that every GHSA advisory
    in the response either had no ``severity`` field at all or a
    vector string with no numeric score. Without the text-label
    fallback every SCA finding would map to severity INFO and
    sort to the bottom of every report.
    """
    if not isinstance(text, str):
        return 0.0
    upper = text.strip().upper()
    return {
        "CRITICAL": 9.5,
        "HIGH": 7.5,
        "MODERATE": 5.0,
        "MEDIUM": 5.0,  # alternate spelling some tools use
        "LOW": 2.0,
    }.get(upper, 0.0)


def _cvss_to_unified_severity(score: float) -> Severity:
    """Map a CVSS numeric score to our unified severity enum."""
    if score >= 9.0:
        return Severity.CRITICAL
    if score >= 7.0:
        return Severity.HIGH
    if score >= 4.0:
        return Severity.MEDIUM
    if score > 0.0:
        return Severity.LOW
    return Severity.INFO


def _normalize_version_for_osv(version: str) -> str:
    """Strip comparison operators so the OSV version lookup works.

    OSV's query endpoint expects a concrete version string like
    `"1.2.3"`, not a PEP 508 specifier like `">=1.2.3,<2.0"`.
    This helper strips leading `>=`, `<=`, `==`, `~=`, `^`, `~`,
    and any trailing comma-separated constraints.

    The normalization is LOSSY: `>=1.2.3,<2.0` becomes `1.2.3`,
    which may not match the actual installed version. The right
    long-term fix is to hydrate the lockfile so we know the
    resolved version; this is tracked as a Phase 2 follow-up.
    """
    text = version.strip()
    if not text:
        return ""
    # Strip the first comparison operator prefix
    for op_prefix in (">=", "<=", "==", "!=", "~=", "^", "~", ">", "<"):
        if text.startswith(op_prefix):
            text = text[len(op_prefix) :].strip()
            break
    # Drop any trailing comma-separated constraints
    if "," in text:
        text = text.split(",", 1)[0].strip()
    return text


def _summarize(text: str, *, max_chars: int = 200) -> str:
    """Trim an OSV message to one compact line."""
    stripped = text.strip().replace("\n", " ")
    if len(stripped) <= max_chars:
        return stripped
    return stripped[: max_chars - 3] + "..."
