"""Loader for the red-team attack catalog (data/redteam/attacks.yaml)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from tailtest.security.redteam.schema import Attack

_CATALOG_PATH = Path(__file__).resolve().parents[4] / "data" / "redteam" / "attacks.yaml"

_VALID_CATEGORIES = frozenset(
    {
        "prompt_injection",
        "jailbreak",
        "pii_extraction",
        "data_leakage",
        "tool_misuse",
        "hallucination",
        "scope_violation",
        "dos",
    }
)

_VALID_SEVERITIES = frozenset({"low", "medium", "high", "critical"})


@lru_cache(maxsize=1)
def load_attacks(catalog_path: Path | None = None) -> list[Attack]:
    """Load and validate the attack catalog.

    Returns a list of ``Attack`` objects. Results are cached -- calling this
    function multiple times with the same path is free.

    Args:
        catalog_path: Override the default catalog location. Primarily for
            testing. Pass ``None`` to use the bundled ``attacks.yaml``.

    Raises:
        FileNotFoundError: if the catalog file does not exist.
        ValueError: if the catalog fails schema validation.
    """
    path = catalog_path if catalog_path is not None else _CATALOG_PATH
    if not path.exists():
        raise FileNotFoundError(f"Attack catalog not found: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    if raw.get("schema_version") != 1:
        raise ValueError(f"Unsupported catalog schema_version: {raw.get('schema_version')!r}")

    raw_attacks = raw.get("attacks", [])
    if not isinstance(raw_attacks, list) or not raw_attacks:
        raise ValueError("Catalog 'attacks' must be a non-empty list")

    attacks: list[Attack] = []
    seen_ids: set[str] = set()
    for entry in raw_attacks:
        attack = Attack.model_validate(entry)

        if attack.id in seen_ids:
            raise ValueError(f"Duplicate attack id: {attack.id!r}")
        seen_ids.add(attack.id)

        if attack.category not in _VALID_CATEGORIES:
            raise ValueError(f"Attack {attack.id!r} has unknown category: {attack.category!r}")
        if attack.severity_on_success not in _VALID_SEVERITIES:
            raise ValueError(
                f"Attack {attack.id!r} has unknown severity: {attack.severity_on_success!r}"
            )

        attacks.append(attack)

    return attacks
