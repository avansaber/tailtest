# Red-team disclosure policy

tailtest's red-team runner performs static analysis of agent code to assess
vulnerability to LLM-specific attack patterns. This document explains how
findings against third-party code should be handled.

## Scope

This policy applies when tailtest's red-team runner is used to analyze code
in projects you do not own or maintain (e.g., open-source repos cloned for
evaluation, client codebases, dependency source trees).

It does NOT apply to your own projects. For those, handle findings however
your organization's security policy specifies.

## Coordinated disclosure process

If the red-team runner finds a plausible vulnerability in third-party code:

1. **Do not publish the finding publicly.** Hold it until the maintainer has
   had a chance to respond.

2. **Report to the maintainer.** Send the finding to the project's security
   contact (usually `SECURITY.md`, `security@<project>.com`, or a private
   issue tracker). Include:
   - The attack category (e.g., prompt injection, data exfiltration)
   - The file and function where the vulnerability was detected
   - The tailtest finding message and reasoning
   - Steps to reproduce if you can construct a test case

3. **Wait for a response.** Allow at least 14 days for an initial
   acknowledgment and 90 days for a fix.

4. **Public disclosure.** After a fix is released, or after 90 days with no
   response (whichever comes first), you may publish the finding publicly.
   Reference the CVE or advisory if one was assigned.

5. **Silent disclosure is not acceptable.** If you find a real vulnerability,
   disclosing it to nobody and continuing to use the affected project puts
   users at risk.

## Contact

For questions about this policy or to report a vulnerability found in
tailtest itself: **security@tailtest.com**

## Timeline summary

| Event | Deadline |
|---|---|
| Initial maintainer contact | Immediately upon finding |
| Maintainer acknowledgment expected | 14 days |
| Fix or public disclosure | 90 days from initial contact |

## Why coordinated disclosure?

LLM agents often handle sensitive user data and have access to tools that
can take real-world actions. A vulnerability in a widely-used agent framework
can affect thousands of downstream deployments. Coordinated disclosure gives
maintainers time to patch before attackers can exploit the finding.

This policy follows standard security research norms established by Google
Project Zero, HackerOne, and the CVE program.
