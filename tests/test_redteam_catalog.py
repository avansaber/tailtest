"""Phase 6 Task 6.1 -- red-team attack catalog tests.

Covers:
- YAML parse and schema validation
- Attack count (must be 64)
- Category distribution (8 categories, 8 attacks each)
- Required field presence on every attack
- Duplicate ID detection
- load_attacks() cache behaviour
- Invalid catalog error paths (bad schema_version, missing file, duplicate ids,
  unknown category, unknown severity)
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tailtest.security.redteam import Attack, load_attacks
from tailtest.security.redteam.loader import _CATALOG_PATH, _VALID_CATEGORIES, _VALID_SEVERITIES

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EXPECTED_CATEGORIES = {
    "prompt_injection",
    "jailbreak",
    "pii_extraction",
    "data_leakage",
    "tool_misuse",
    "hallucination",
    "scope_violation",
    "dos",
}

EXPECTED_ATTACKS_PER_CATEGORY = 8
EXPECTED_TOTAL = 64


def _make_catalog(attacks: list[dict], *, schema_version: int = 1) -> str:
    """Build a YAML catalog string for use in tmp_path tests."""
    return yaml.dump({"schema_version": schema_version, "attacks": attacks})


def _minimal_attack(attack_id: str, category: str = "prompt_injection") -> dict:
    return {
        "id": attack_id,
        "category": category,
        "title": "Test",
        "description": "A test attack.",
        "payload": "test payload",
        "expected_outcome": "agent breaks",
        "severity_on_success": "high",
    }


# ---------------------------------------------------------------------------
# Catalog file structure
# ---------------------------------------------------------------------------


def test_catalog_file_exists() -> None:
    assert _CATALOG_PATH.exists(), f"attacks.yaml not found at {_CATALOG_PATH}"


def test_catalog_is_valid_yaml() -> None:
    raw = yaml.safe_load(_CATALOG_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)


def test_catalog_schema_version_is_1() -> None:
    raw = yaml.safe_load(_CATALOG_PATH.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 1


def test_catalog_attacks_is_list() -> None:
    raw = yaml.safe_load(_CATALOG_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw["attacks"], list)


# ---------------------------------------------------------------------------
# Attack count and category distribution
# ---------------------------------------------------------------------------


def test_load_attacks_returns_64() -> None:
    attacks = load_attacks()
    assert len(attacks) == EXPECTED_TOTAL


def test_load_attacks_has_eight_categories() -> None:
    attacks = load_attacks()
    categories = {a.category for a in attacks}
    assert categories == EXPECTED_CATEGORIES


def test_each_category_has_eight_attacks() -> None:
    attacks = load_attacks()
    from collections import Counter

    counts = Counter(a.category for a in attacks)
    for category in EXPECTED_CATEGORIES:
        assert counts[category] == EXPECTED_ATTACKS_PER_CATEGORY, (
            f"Category {category!r} has {counts[category]} attacks, expected 8"
        )


def test_all_attack_ids_are_unique() -> None:
    attacks = load_attacks()
    ids = [a.id for a in attacks]
    assert len(ids) == len(set(ids)), "Duplicate attack IDs found"


# ---------------------------------------------------------------------------
# Required fields on every attack
# ---------------------------------------------------------------------------


def test_every_attack_has_id() -> None:
    for attack in load_attacks():
        assert attack.id, f"Attack missing id: {attack}"


def test_every_attack_has_category() -> None:
    for attack in load_attacks():
        assert attack.category in _VALID_CATEGORIES, (
            f"Attack {attack.id!r} has invalid category: {attack.category!r}"
        )


def test_every_attack_has_payload() -> None:
    for attack in load_attacks():
        assert attack.payload.strip(), f"Attack {attack.id!r} has empty payload"


def test_every_attack_has_expected_outcome() -> None:
    for attack in load_attacks():
        assert attack.expected_outcome.strip(), (
            f"Attack {attack.id!r} has empty expected_outcome"
        )


def test_every_attack_has_valid_severity() -> None:
    for attack in load_attacks():
        assert attack.severity_on_success in _VALID_SEVERITIES, (
            f"Attack {attack.id!r} has invalid severity: {attack.severity_on_success!r}"
        )


def test_every_attack_has_remediation_hint() -> None:
    for attack in load_attacks():
        assert attack.remediation_hint and attack.remediation_hint.strip(), (
            f"Attack {attack.id!r} missing remediation_hint"
        )


# ---------------------------------------------------------------------------
# Attack schema (Pydantic model)
# ---------------------------------------------------------------------------


def test_attack_model_validates_minimal_fields() -> None:
    attack = Attack.model_validate(_minimal_attack("test_001"))
    assert attack.id == "test_001"
    assert attack.category == "prompt_injection"
    assert attack.cwe_id is None
    assert attack.applicable_languages == []
    assert attack.multi_turn_prompts == []


def test_attack_model_ignores_extra_fields() -> None:
    data = _minimal_attack("test_002")
    data["unknown_future_field"] = "some_value"
    attack = Attack.model_validate(data)
    assert attack.id == "test_002"


def test_attack_model_accepts_null_cwe() -> None:
    data = _minimal_attack("test_003")
    data["cwe_id"] = None
    attack = Attack.model_validate(data)
    assert attack.cwe_id is None


def test_attack_model_accepts_null_owasp() -> None:
    data = _minimal_attack("test_004")
    data["owasp_llm_category"] = None
    attack = Attack.model_validate(data)
    assert attack.owasp_llm_category is None


# ---------------------------------------------------------------------------
# Loader cache behaviour
# ---------------------------------------------------------------------------


def test_load_attacks_is_cached() -> None:
    attacks1 = load_attacks()
    attacks2 = load_attacks()
    assert attacks1 is attacks2  # same object -- cache hit


def test_load_attacks_with_explicit_path(tmp_path: Path) -> None:
    catalog = _make_catalog([_minimal_attack("pi_001")])
    catalog_file = tmp_path / "attacks.yaml"
    catalog_file.write_text(catalog)
    # load_attacks is lru_cached on the Path argument; pass different path
    attacks = load_attacks(catalog_path=catalog_file)
    assert len(attacks) == 1
    assert attacks[0].id == "pi_001"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_load_attacks_raises_on_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="not found"):
        load_attacks(catalog_path=tmp_path / "nonexistent.yaml")


def test_load_attacks_raises_on_bad_schema_version(tmp_path: Path) -> None:
    catalog = _make_catalog([_minimal_attack("pi_001")], schema_version=99)
    f = tmp_path / "attacks.yaml"
    f.write_text(catalog)
    with pytest.raises(ValueError, match="schema_version"):
        load_attacks(catalog_path=f)


def test_load_attacks_raises_on_empty_attacks(tmp_path: Path) -> None:
    f = tmp_path / "attacks.yaml"
    f.write_text(yaml.dump({"schema_version": 1, "attacks": []}))
    with pytest.raises(ValueError, match="non-empty"):
        load_attacks(catalog_path=f)


def test_load_attacks_raises_on_duplicate_ids(tmp_path: Path) -> None:
    catalog = _make_catalog(
        [_minimal_attack("dup_001"), _minimal_attack("dup_001")]
    )
    f = tmp_path / "attacks.yaml"
    f.write_text(catalog)
    with pytest.raises(ValueError, match="Duplicate attack id"):
        load_attacks(catalog_path=f)


def test_load_attacks_raises_on_unknown_category(tmp_path: Path) -> None:
    bad = _minimal_attack("pi_001")
    bad["category"] = "not_a_category"
    f = tmp_path / "attacks.yaml"
    f.write_text(_make_catalog([bad]))
    with pytest.raises(ValueError, match="unknown category"):
        load_attacks(catalog_path=f)


def test_load_attacks_raises_on_unknown_severity(tmp_path: Path) -> None:
    bad = _minimal_attack("pi_001")
    bad["severity_on_success"] = "extreme"
    f = tmp_path / "attacks.yaml"
    f.write_text(_make_catalog([bad]))
    with pytest.raises(ValueError, match="unknown severity"):
        load_attacks(catalog_path=f)


# ---------------------------------------------------------------------------
# Sanity check: attacks.yaml already tested by test_sanity.py
# (test_attacks_yaml_valid) -- no duplication needed here
# ---------------------------------------------------------------------------
