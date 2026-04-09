"""tailtest.core.config — ``.tailtest/config.yaml`` loader + schema.

Phase 1 ships the minimal config: schema version, depth mode, a runners
section (auto-detect by default), and a security section (stubs for
Phase 2). Config is loaded fresh on every PostToolUse hook invocation
per audit gap #4 so depth changes take effect immediately.
"""

from tailtest.core.config.loader import ConfigLoader
from tailtest.core.config.schema import (
    CONFIG_SCHEMA_VERSION,
    Config,
    DepthMode,
    RunnersConfig,
    SecurityConfig,
)

__all__ = [
    "CONFIG_SCHEMA_VERSION",
    "Config",
    "ConfigLoader",
    "DepthMode",
    "RunnersConfig",
    "SecurityConfig",
]
