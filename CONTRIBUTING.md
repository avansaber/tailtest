# Contributing to tailtest

> **Not yet accepting external contributions.**
> This file is a placeholder until the project reaches its public `0.1.0` release.

tailtest is currently in **Phase 0** (foundation scaffolding) of its v0.1.0 rebuild. The project is maintained by a single team and is not yet set up to accept pull requests from the broader community.

## When will contributions be welcome?

External contributions will open once the project reaches `0.1.0` (public release). At that point, this file will be updated with:

- The development setup and local-install instructions
- The test running + linting workflow
- The code of conduct
- The pull-request review process
- The issue triage policy
- The Contributor License Agreement (if any)

## What you can do today

- **Star the repository** if you're interested in the project — it helps gauge early interest.
- **Open issues** describing bugs, feature requests, or use cases you'd like tailtest to cover. Even if we're not accepting PRs yet, issue threads help shape the roadmap.
- **Follow the project** on GitHub for release notifications.

## How the project is built

For transparency, tailtest is being built in 8 phases:

- **Phase 0** — Foundation (repo scaffolding, CI, plugin manifest)
- **Phase 1** — MVP hot loop (PostToolUse hook + impacted tests + test generation)
- **Phase 2** — Security layer (gitleaks + Semgrep + OSV)
- **Phase 3** — Opportunity detection + AI-agent integration
- **Phase 4** — Live dashboard
- **Phase 4.5** — Rust runner
- **Phase 5** — Validator subagent
- **Phase 6** — Red-team merge
- **Phase 7** — Launch

Each phase has validation criteria and ships as a pre-release tag (`alpha.1`, `alpha.2`, `beta.1`, …). No phase starts until the previous one is `validated`.

Semantic versioning applies: breaking changes bump the minor version in the `0.x` range, bug fixes bump the patch version.

## Contact

For now, the maintainer contact is via GitHub issues on the repository once it's public. A proper security contact email is in `SECURITY.md`.
