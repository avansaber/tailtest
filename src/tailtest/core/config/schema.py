"""Config schema for ``.tailtest/config.yaml``.

Phase 1 shape with Phase 2 Task 2.9 extensions. Every field has a
default so an empty config file still parses. ``schema_version``
is mandatory (currently 1); Phase 2 keeps schema_version at 1 on
purpose because the Task 2.9 additions are backward compatible:
the legacy ``sast: true/false`` and ``sca: true/false`` bool
shapes still parse via field validators that coerce them into the
new nested ``SastConfig`` / ``ScaConfig`` shapes with defaults.
A Phase 3+ incompatible change would bump schema_version.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

CONFIG_SCHEMA_VERSION = 1


class DepthMode(StrEnum):
    """Controls how much work tailtest does on every Claude edit.

    See ADR 0002 for the rationale. Phase 1 actually uses `quick` and
    `standard`; `thorough` and `paranoid` are accepted with a soft-warn
    per audit gap #9 until Phase 3/5/6 fully implement them.
    """

    OFF = "off"
    QUICK = "quick"
    STANDARD = "standard"
    THOROUGH = "thorough"
    PARANOID = "paranoid"


class RunnersConfig(BaseModel):
    """Per-language runner configuration.

    Phase 1 supports `auto` (the default) — the scanner decides which
    runners apply based on what it finds. Explicit runner config lands
    in Phase 2 via the custom-runner adapter (ADR 0011).
    """

    model_config = ConfigDict(extra="forbid")

    auto_detect: bool = True


class SastConfig(BaseModel):
    """Nested SAST configuration (Phase 2 Task 2.9).

    The ``ruleset`` field maps to Semgrep's ``--config`` argument.
    Defaults to ``p/default`` which is Semgrep's curated low-FP
    ruleset covering the top OWASP + CWE patterns. Users can
    override with any Semgrep ruleset identifier, e.g.
    ``p/owasp-top-ten``, ``p/ci``, or a path to a local YAML file.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    ruleset: str = "p/default"

    def __bool__(self) -> bool:
        """Truthy iff the scanner is enabled.

        Preserves backward compatibility with call sites that do
        ``if config.security.sast:`` from Phase 2 Task 2.5 before
        this nested type existed. New call sites should prefer
        the explicit ``.enabled`` attribute.
        """
        return self.enabled


class ScaConfig(BaseModel):
    """Nested SCA configuration (Phase 2 Task 2.9).

    ``use_epss`` is the opt-in toggle for EPSS-based severity
    adjustment. EPSS is the "Exploit Prediction Scoring System"
    which estimates the probability a vuln will be exploited in
    the wild within 30 days. Phase 2 keeps it off by default
    because EPSS.io integration has not yet shipped; when it
    does (future revision) a user can flip this to ``true`` to
    boost severity on findings with high EPSS scores.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    use_epss: bool = False

    def __bool__(self) -> bool:
        return self.enabled


class SecurityConfig(BaseModel):
    """Security scanning configuration.

    Phase 2 Task 2.5 flipped the ``secrets``/``sast``/``sca``
    defaults from ``False`` to ``True`` because the scanner trio
    ships with the hot loop integration. Each scanner has a
    graceful ``is_available()`` fallback, so if the underlying
    binary (``gitleaks``, ``semgrep``) is not on PATH the hook
    still runs cleanly and just logs an INFO line.

    Phase 2 Task 2.9 extended ``sast`` and ``sca`` from plain
    bools into nested config types (``SastConfig`` / ``ScaConfig``)
    so users can configure the Semgrep ruleset and the EPSS
    toggle per-project. Legacy configs written against Phase 1
    with ``sast: true``/``sast: false`` (plain bool) still parse
    correctly via the ``_coerce_legacy_bool`` validator below; the
    coerced value inherits the new defaults for any missing
    nested fields. This preserves forward/backward compatibility
    without bumping ``CONFIG_SCHEMA_VERSION``.

    ``secrets`` stays as a plain bool because gitleaks has no
    per-project configuration to expose at the config layer (the
    rule set is built into the gitleaks binary). Adding a nested
    ``SecretsConfig`` without real fields would just be noise.

    ``block_on_verified_secret`` remains off until secret
    verification against live APIs ships in a later revision.
    """

    model_config = ConfigDict(extra="forbid")

    secrets: bool = True
    sast: SastConfig = Field(default_factory=SastConfig)
    sca: ScaConfig = Field(default_factory=ScaConfig)
    block_on_verified_secret: bool = False

    @field_validator("sast", mode="before")
    @classmethod
    def _coerce_legacy_sast(cls, value: Any) -> Any:
        """Accept ``sast: true``/``sast: false`` for Phase 1 configs.

        Returns the dict form ``{"enabled": <bool>}`` which pydantic
        will then validate into a ``SastConfig`` with the default
        ruleset. New configs should use the nested form directly.
        """
        if isinstance(value, bool):
            return {"enabled": value}
        return value

    @field_validator("sca", mode="before")
    @classmethod
    def _coerce_legacy_sca(cls, value: Any) -> Any:
        """Accept ``sca: true``/``sca: false`` for Phase 1 configs."""
        if isinstance(value, bool):
            return {"enabled": value}
        return value


class NotificationsConfig(BaseModel):
    """Which tailtest surfaces can emit nudges.

    auto_offer_generation is the opt-out knob for audit gap #6 (Task 1.5a
    auto-offer test generation suggestions in the PostToolUse hook).
    """

    model_config = ConfigDict(extra="forbid")

    auto_offer_generation: bool = True
    recommendations: bool = True


class Config(BaseModel):
    """Top-level tailtest project configuration.

    Loaded from ``.tailtest/config.yaml`` by :class:`ConfigLoader`. Every
    field has a sensible default so a missing or empty config still
    produces a usable Config.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=CONFIG_SCHEMA_VERSION)
    depth: DepthMode = DepthMode.STANDARD
    runners: RunnersConfig = Field(default_factory=RunnersConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)

    # Comments live here so the YAML file can carry a header when we
    # write a fresh default. Not serialized back during roundtrips.
    interview_completed: bool = False
