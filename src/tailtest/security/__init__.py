"""tailtest.security, the Phase 2 security scanning layer.

Adds secret scanning (gitleaks), SAST (Semgrep), and SCA (OSV
advisory lookup) on top of the Phase 1 hot loop. Findings flow
through the same unified ``Finding`` schema as test failures, so
reporters, baselines, and the dashboard do not need to
distinguish between test failures and security issues.

Each sub-module under this package integrates one tool:

- ``tailtest.security.secrets.gitleaks`` wraps ``gitleaks detect``
- ``tailtest.security.sast.semgrep`` (Task 2.2) wraps ``semgrep``
- ``tailtest.security.sca.osv`` (Task 2.3) wraps the OSV advisory API

All three are lazy-initialized and degrade gracefully when the
underlying tool is not available on the target machine. A
missing gitleaks binary produces a one-time warning, not a hard
failure.
"""

__all__: list[str] = []
