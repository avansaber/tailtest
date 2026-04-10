"""tailtest.core.config — ``.tailtest/config.yaml`` loader + schema.

Phase 1 ships the minimal config: schema version, depth mode, a runners
section (auto-detect by default), and a security section (stubs for
Phase 2). Phase 2 Task 2.5 flipped security defaults to on and Task
2.9 extended the SAST and SCA blocks into nested types so users can
configure rulesets and EPSS toggles. Config is loaded fresh on every
PostToolUse hook invocation per audit gap #4 so depth and ruleset
changes take effect immediately.
"""

from tailtest.core.config.loader import ConfigLoader
from tailtest.core.config.schema import (
    CONFIG_SCHEMA_VERSION,
    Config,
    DepthMode,
    NexTestPreference,
    RunnersConfig,
    RustRunnerConfig,
    SastConfig,
    ScaConfig,
    SecurityConfig,
    WorkspaceMode,
)

__all__ = [
    "CONFIG_SCHEMA_VERSION",
    "Config",
    "ConfigLoader",
    "DepthMode",
    "NexTestPreference",
    "RunnersConfig",
    "RustRunnerConfig",
    "SastConfig",
    "ScaConfig",
    "SecurityConfig",
    "WorkspaceMode",
]
