"""Config schema for ``.tailtest/config.yaml``.

Phase 1 shape. Every field has a default so an empty config file still
parses. ``schema_version`` is mandatory (currently 1) — Phase 2+ bumps
it on any incompatible change.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

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


class SecurityConfig(BaseModel):
    """Security scanning configuration.

    Phase 1 ships with every security check off (security layer lands
    in Phase 2). These fields exist so Phase 1 configs are forward-
    compatible with Phase 2 without a migration.
    """

    model_config = ConfigDict(extra="forbid")

    secrets: bool = False
    sast: bool = False
    sca: bool = False
    block_on_verified_secret: bool = False


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
