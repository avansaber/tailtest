# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""tailtest.security.redteam -- Phase 6 red-team attack catalog loader.

Loads the 64-attack YAML catalog into typed ``Attack`` objects.
The runner (Phase 6 Task 6.2) consumes these at ``paranoid`` depth on
``ai_surface: agent`` projects.
"""

from __future__ import annotations

from tailtest.security.redteam.loader import load_attacks
from tailtest.security.redteam.runner import RedTeamRunner
from tailtest.security.redteam.schema import Attack

__all__ = ["Attack", "RedTeamRunner", "load_attacks"]
