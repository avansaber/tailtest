# Security Policy

## Supported Versions

Only the latest released version is supported with security updates.

| Version  | Supported |
|----------|-----------|
| `0.1.1` (current) | ✅ Supported |
| Older `0.1.x` | ❌ Upgrade to latest |
| v0.2.x+ | ✅ Latest patch only |

## Reporting a Vulnerability

If you believe you have found a security vulnerability in tailtest, **please do not open a public GitHub issue**. Instead, contact the maintainer directly via email.

**Email:** `support@avansaber.com` — use subject line `[TAILTEST SECURITY]`

### What to include

When reporting, please include:

1. A description of the vulnerability
2. Steps to reproduce (minimal test case ideal)
3. The affected version(s) of tailtest
4. The potential impact (what an attacker could do)
5. Any suggested fix or mitigation

### What to expect

- **Acknowledgment:** within 72 hours of report
- **Initial assessment:** within 7 days
- **Fix timeline:** depends on severity — critical issues within 14 days, high within 30, medium within 60
- **Disclosure:** coordinated with the reporter; public disclosure only after a fix is available and users have had time to upgrade

We follow standard security research norms: we credit reporters (with permission), we don't threaten legal action for good-faith research, and we don't sit on vulnerabilities.

## Scope

In scope:

- Code in the `tailtest/` public repository
- The Python package `tailtester` on PyPI
- The Claude Code plugin `tailtest@avansaber`
- The MCP server shipped via `tailtest mcp-serve`

Out of scope:

- Third-party tools tailtest integrates with (gitleaks, Semgrep, OSV, Playwright, etc.) — report those upstream
- Vulnerabilities in user code that tailtest tested but failed to detect (that's a product limitation, not a security issue)
- Denial of service via oversized input (tailtest is a local tool; not a service)
- Social engineering / phishing targeting the maintainers

## Project security practices

tailtest eats its own dogfood. Every release is gated by a pre-public repo hygiene audit that includes:

- Full git history secret scan (gitleaks + trufflehog on all commits)
- PII and sensitive data scan
- Dependency license and vulnerability audit
- Naming + metadata consistency check
- Manual review of every file in the release

If a release doesn't pass the audit, it doesn't ship.
