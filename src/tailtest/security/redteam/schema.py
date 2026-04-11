"""Attack schema -- Pydantic model for a single red-team attack entry."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Attack(BaseModel):
    """A single red-team attack from the catalog.

    Fields mirror the YAML schema defined in ``data/redteam/attacks.schema.yaml``.
    """

    id: str
    category: str
    title: str
    description: str
    payload: str
    expected_outcome: str
    severity_on_success: str
    cwe_id: str | None = None
    owasp_llm_category: str | None = None
    remediation_hint: str | None = None
    applicable_languages: list[str] = Field(default_factory=list)
    multi_turn_prompts: list[str] = Field(default_factory=list)

    model_config = {"extra": "ignore"}
