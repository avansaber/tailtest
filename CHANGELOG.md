# Changelog

All notable changes to tailtest will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0-alpha.2] - 2026-04-09

### Added
- **Phase 2 security layer.** The brand promise lands: tailtest is now the test + security validator that lives inside Claude Code. Every Claude edit at standard depth runs the impacted tests AND the security scanner trio (gitleaks, Semgrep, OSV) and surfaces both kinds of findings through the same `Finding` schema.
- **GitleaksRunner** at `src/tailtest/security/secrets/gitleaks.py`. Per-file secret scanning via `gitleaks detect --no-git --report-format json`. Graceful fallback when the binary is not installed. Every secret finding maps to `cwe_id="CWE-798"` (Use of Hard-coded Credentials).
- **SemgrepRunner** at `src/tailtest/security/sast/semgrep.py`. Batch SAST scanning across the changed files via `semgrep --config <ruleset> --json`. Default ruleset is `p/default`; configurable per-project via `.tailtest/config.yaml` `security.sast.ruleset`. Severity mapping: ERROR -> HIGH, WARNING -> MEDIUM, INFO -> LOW. CWE extraction from rule metadata.
- **OSVLookup** at `src/tailtest/security/sca/osv.py` + `manifests.py`. Dependency vulnerability scanning via `https://api.osv.dev/v1/querybatch` with per-vuln hydration via `/v1/vulns/<id>`, on-disk caching at `.tailtest/cache/osv/` and `.tailtest/cache/osv-vulns/`, alias-chain dedup so the user does not see the same vulnerability under multiple ids (GHSA / PYSEC / CVE), `database_specific.severity` text-label fallback for advisories that ship a CVSS metric vector with no numeric score, CWE extraction from `database_specific.cwe_ids`.
- **Manifest parsers** for `pyproject.toml` (PEP 621 `[project.dependencies]` + `[project.optional-dependencies]`) and `package.json` (`dependencies`, `devDependencies`, `peerDependencies`, `optionalDependencies`). `diff_manifests(old, new)` returns added + bumped packages keyed on `(ecosystem, name)` so PyPI `foo` and npm `foo` stay distinct. First-run-on-a-manifest treats every dep as added so the user gets immediate CVE feedback.
- **Manifest snapshot cache** at `.tailtest/cache/manifests/<filename>.snap` so subsequent hook runs only surface added or bumped deps, not the full pre-existing dependency set.
- **PostToolUse security phase** in `hook/post_tool_use.py`. Runs after the test phase (failing tests skip security to keep the hot loop test-first), depth-gated (`quick` runs only gitleaks; `standard+` runs the full trio), merges security findings into the same `FindingBatch` as test findings BEFORE baseline filtering so the baseline applies uniformly. Summary line format: `tailtest: 14/14 tests passed · 1 new security issue · 1.8s`.
- **HTMLReporter** at `src/tailtest/core/reporter/html.py`. Self-contained HTML report (inline CSS, no JavaScript, no CDN, no external assets) with sections for tests, delta coverage, findings grouped by kind in a fixed order (test failures -> secrets -> SAST -> SCA -> coverage -> lint -> AI surface -> validator -> red team), severity stripes, baseline summary, footer. Atomic writes to `.tailtest/reports/<iso>.html` + `.tailtest/reports/latest.html`. XSS-safe: every external string runs through `html.escape()` before it lands in the body.
- **`/tailtest:debt` skill** at `skills/debt/SKILL.md`. Read-only review of the `.tailtest/baseline.yaml` accepted-debt ledger.
- **`/tailtest:security` skill** at `skills/security/SKILL.md`. 4-part posture view: scanner posture (which scanners are enabled + ruleset + depth), current findings (new vs suppressed counts), breakdown by kind, follow-ups (action lines based on the current state).
- **Baseline documentation header** prepended to every `baseline.yaml` write so contributors who open the file understand what it is, how entries get added, how to remove them, how to review via `/tailtest:debt`, and the commit-to-git convention.
- **Nested `SastConfig` and `ScaConfig`** in `src/tailtest/core/config/schema.py`. SAST gains a `ruleset: str` field; SCA gains a `use_epss: bool` field. Legacy `sast: true/false` and `sca: true/false` configs from Phase 1 still parse via field validators that coerce bool into the nested form. `__bool__` helpers preserve backward compatibility with call sites that do `if config.security.sast:`. Security defaults flipped from False to True (Phase 1 shipped them off because the scanners had not been built yet).
- **Phase 2 dogfood fixture** at `internal-testing/fixtures/phase2-vuln-fixture/`. Minimal Python project seeded with a Stripe test key + `eval(user_input)` + `requests==2.0.0` for end-to-end scanner validation.
- **Finding schema security metadata** populated by every scanner: `cwe_id`, `cvss_score`, `package_name`, `package_version`, `fixed_version`, `advisory_url`. Schema fields existed from Phase 1's forward-looking design; Phase 2 wires each scanner to populate them.
- **245 new tests** across the security layer (608 total, was 363 at end of Phase 1). Coverage includes pure parsers, mocked subprocess paths, mocked httpx hydration, baseline regression, summary line format, manifest snapshot round-trip, HTML reporter render + atomic writes + XSS escaping, OSV alias dedup, CVSS prefix-stripping regex.

### Fixed
- **OSV severity-INFO bug** (Task 2.10a, alpha.2 unblocker). The `_parse_cvss_score_string` regex fallback was extracting `3.1` (the CVSS spec version) from vector strings like `CVSS:3.1/AV:N/AC:H/...` and treating it as the score. New `_CVSS_VERSION_PREFIX_RE` strips the prefix before any number-extraction. Combined with the lean-batch-response hydration step + the `database_specific.severity` text-label fallback, SCA findings now surface at proper CRITICAL/HIGH/MEDIUM/LOW severities. Live OSV dogfood validates: 9 lean findings -> 6 unique findings (3 PYSEC duplicates dropped) at MEDIUM/HIGH with CWE IDs populated.
- **PostToolUse hook silent JSON failure** (Task 2.10 from parallel Level 2 dogfood). `_parse_stdin` was returning None on malformed JSON with no log line, making "hook crashed parsing input" and "hook not installed" indistinguishable. Now emits an INFO-level diagnostic per failure mode while keeping empty stdin silent (Claude Code regularly invokes hooks with no payload).

### Infrastructure
- 608 pytest tests passing + 1 skipped (was 363 at end of Phase 1). Ruff clean, pyright standard 0/0/0, gitleaks clean.
- Phase 2 mid-phase audit (Task 2.5a) walked the hygiene checklist, caught zero regressions, kept the plan files in sync with reality.
- Live OSV API integration validated end-to-end against the seeded fixture; first dogfood-caught bug (severity-INFO across the board) found and fixed in-session with 35 new regression tests.

## [0.1.0-alpha.1] - 2026-04-09

### Added
- **JavaScript/TypeScript runner** (`JSRunner`) supporting vitest (preferred) and jest, with auto-selection from package.json devDependencies and config files. Native TIA via `vitest related` / `jest --findRelatedTests --listTests`. Heuristic fallback for when native TIA fails.
- **Test generator** (`TestGenerator`) that produces starter tests via `claude -p` subprocess. Supports Python (pytest), TypeScript (vitest by default, jest override), and JavaScript (vitest). Mandatory "review before committing" header, per-language compile check, never commits automatically.
- **PostToolUse hook** real implementation. Parses Claude Code's edit payload, runs impacted tests via the runner registry, applies baseline filtering, formats findings as `hookSpecificOutput` for the next turn. Includes self-edit exclusion, manifest-rescan trigger, 5KB truncation, and SIGINT handling.
- **SessionStart hook** real implementation. Bootstraps `.tailtest/config.yaml` via defaults, runs the shallow scanner, writes `.tailtest/profile.json`, handles empty projects and scanner failures gracefully, resets the auto-offer session debounce cache for each new session.
- **Delta coverage tracking** (Python path). Diff parser + `coverage.py` JSON parser + computation module. PythonRunner wraps pytest with `coverage run -m pytest` when asked, intersects the coverage result with the edited lines, populates `delta_coverage_pct` and `uncovered_new_lines` in the `FindingBatch`. Reporter renders a delta-coverage line, hook surfaces it to Claude's next turn with up to 3 specific uncovered file:line pointers.
- **Auto-offer test generation** in the PostToolUse hook. AST-based pure-function detection (excludes functions with I/O markers, global state, no return, class methods, nested defs). Has-test heuristic walks `tests/` and colocated `src/<pkg>/tests/`. Per-session debounce via `.tailtest/session-state.json`. 3-suggestion cap per hook run. Gated by `notifications.auto_offer_generation` config flag.
- **Six namespaced skills** under `skills/<name>/SKILL.md`: `/tailtest:status`, `/tailtest:depth`, `/tailtest:scan`, `/tailtest:gen`, `/tailtest:report`, `/tailtest:setup`. Each skill is a markdown file with YAML frontmatter (description, optional argument-hint) and a prose playbook body.
- **Session state module** (`core/session_state.py`) with atomic write via `.tmp` file + rename, session-id-based cache invalidation, JSON serialization.
- **Pure-function heuristics module** (`core/generator/heuristics.py`) for the auto-offer path.
- **Frontmatter validator test** (`tests/test_skills_frontmatter.py`) that asserts every skill has a valid description, argument-taking skills declare argument-hint, no skill has a name field, and every body references at least one tailtest concept.

### Fixed
- **PythonRunner venv mismatch**: `_resolve_pytest_path()` now prefers the target project's `.venv/bin/pytest` over tailtest's own venv. Previously, running tailtest from its own venv against another project used the wrong pytest and every test collection-failed on missing deps.
- **JUnit collection error text preservation**: `_parse_junit` now combines the `message` attribute and the `text` content so the full `ModuleNotFoundError` traceback survives. Previously the generic "collection failure" banner clobbered the body.
- **Positional path argument** added to `tailtest scan`, `tailtest run`, `tailtest doctor` commands. Previously only `--project-root` was supported; the Level 1 instructions referenced positional path syntax that did not exist.
- **JSRunner false-positive discovery** (Checkpoint G fix). Previously, a `tests/` directory with `*.test.ts` files was enough for `discover()` to return True, falling back to vitest as the default. Fails on projects that use `node --test` (Feynman) because vitest is not installed. Now `discover()` requires an explicit framework signal (config file or package.json devDependency).

### Changed
- CLI commands (`run`, `scan`, `doctor`) all accept an optional positional PATH argument in addition to `--project-root`.
- PostToolUse hook split into library (`src/tailtest/hook/post_tool_use.py`) + shim (`hooks/post_tool_use.py`). Library is async, testable without subprocess spawning. Shim handles SIGINT, stdin read, stdout write, exit code.

### Infrastructure
- First public push of the v2 scaffold to `github.com/avansaber/tailtest` (replacing v1 via a planned history flatten).
- Cleanup of pre-v2 artifacts from the public remote: deleted the stale `v2` branch (which ironically pointed at v1 content) plus the `v0.3.0` and `v0.3.1` tags and releases.
- Branch protection rules updated to match v2 CI context names (`Python 3.11 on ubuntu-latest` etc.) instead of the v1 inherited names.
- 363 pytest tests passing (up from 0 in Phase 0). Ruff clean, pyright standard 0/0/0, gitleaks clean, trufflehog clean.

### Known limitations
- **Feynman is not yet a dogfood target.** Feynman uses Node's built-in `node --test` runner, which tailtest does not support. A `NodeTestRunner` is tracked as a follow-up.
- **TypeScript/JavaScript delta coverage** is deferred. The computation layer is language-agnostic; the vitest/jest coverage parser is not yet wired up.
- **TypeScript/JavaScript auto-offer test generation** is deferred. The AST heuristic is Python-only via `ast.parse`.
- **thorough and paranoid depth modes** accept the config setting but the deeper features (LLM-judge assertions, validator subagent, red-team catalog) ship in later phases.

## [0.1.0-alpha.0] — 2026-04-09

### Added
- Initial repository scaffolding (Phase 0 of the v0.1.0 rebuild)
- `pyproject.toml` with minimal dependencies (click, pydantic, httpx, pyyaml) and dev dependencies (pytest, pytest-asyncio, ruff, pyright)
- Claude Code plugin manifest at `.claude-plugin/plugin.json`
- MCP server wiring at `.mcp.json`
- Empty hook scaffolds at `hooks/` (PostToolUse / SessionStart / Stop)
- Empty skill scaffold at `skills/tailtest/SKILL.md`
- Empty MCP server scaffold at `src/tailtest/mcp/server.py`
- LLM transport layer copied from the v1 project (`llm/resolver.py`, `llm/claude_cli.py`) — Claude CLI subprocess wrapper + multi-provider resolver
- Red-team attack catalog placeholder at `data/redteam/` (full extraction deferred to Phase 6)
- Apache 2.0 license
- GitHub Actions CI skeleton (`.github/workflows/ci.yml`) running ruff, pyright, pytest on Python 3.11 and 3.12

### Known limitations
- **Nothing in this release is functional.** Phase 0 is infrastructure-only. The real hot loop lands in Phase 1 (`0.1.0-alpha.1`).
- Hooks are pass-through stubs that do nothing.
- MCP server responds to `initialize` and `tools/list` but has no actual tools.
- The `/tailtest` skill is a placeholder.
- Test generation, project scanning, security scanning, and the dashboard are not implemented.

[Unreleased]: https://github.com/avansaber/tailtest/compare/v0.1.0-alpha.0...HEAD
[0.1.0-alpha.0]: https://github.com/avansaber/tailtest/releases/tag/v0.1.0-alpha.0
