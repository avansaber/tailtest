# Changelog

All notable changes to tailtest will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-04-12

### Fixed
- **Terminal says "clean" when pytest times out.** `_format_summary()` in `TerminalReporter` now checks `batch.summary_line` before falling back to the generic "clean" label. When a runner sets a timeout message (e.g. `"tailtest: pytest timed out at 30.0s"`), that message is shown verbatim. Caught by the Ubuntu server smoke test (Task 7.10). Regression test added.
- **"No runners detected" when binary is missing.** `tailtest run` now distinguishes between "no test config found" and "config found but runner binary missing". When a runner sees config (pytest.ini / pyproject.toml `[tool.pytest.ini_options]`) but the `pytest` binary is absent, the error now reads `"tailtest: pytest: pytest not found in project venv or on PATH"` instead of the generic "no runners detected". Implemented via `RunnerRegistry.unavailable_reasons()`. Regression test added.
- **PyPI version shadowing.** v1 of tailtest (tailtester) was at `0.3.1`; pip always resolved to v1. Bumped v2 to `0.4.0` so `pip install tailtester` installs the correct package.

---

## [0.2.0] - 2026-04-12

### Added (Phase 8 -- context-aware test generation)
- **Context-aware test generation.** `/tailtest:gen <file>` now AST-scans the source for domain vocabulary before writing a single test. Enum subclasses, named exception classes, typed function signatures, and auth-pattern decorators are extracted and wired into the generator prompt as explicit instructions ("for `InvoiceStatus`, generate at least one state-transition test").
- **`ast_signals.py`** -- new module at `src/tailtest/core/generator/ast_signals.py`. Exports `DomainSignals` (frozen dataclass), `extract_domain_signals()` (Python AST + JS/TS regex; never executes source), `build_category_hints()` (6 conditions, capped at 5), `build_detection_note()` (4-priority branches), `cluster_domain_from_source_tree()` (vibe-coded path: groups sibling function names by prefix to find the dominant domain cluster).
- **`ProjectContext` dataclass** in `prompts.py`. Carries `llm_summary`, `primary_language`, `runner`, `framework_category`, `likely_vibe_coded`, `tests_dirs` from the deep scanner's `profile.json`. Loaded silently; returns `None` if `profile.json` is absent (zero-config promise preserved).
- **`## Project context` prompt section.** When `profile.json` exists, the generator prompt gains a compact block: domain summary (max 300 chars), language, runner, framework category, and (on vibe-coded projects) the dominant function-name cluster from sibling files.
- **`## Domain signals` prompt section.** Enum names, exception names, key class names, and public function signatures rendered directly in the prompt so Claude names real types in generated tests.
- **`## Existing test style (sample)` prompt section.** First 30 lines of the nearest existing test file (sibling-first, then most-recently-modified in `tests_dirs`), hard-capped at 1,200 chars. Generated tests match the project's parametrize vs. flat-function vs. `pytest.raises` conventions.
- **`## What to generate` prompt section.** Up to 5 targeted instructions derived from domain signals ("for `CreditLimitExceeded`, generate at least one test that asserts it is raised").
- **Detection note on line 2** of every generated file. Four priority branches: user-supplied `--context` description > AST-detected entity names > deep-scan project summary > generic fallback. Examples: `# tailtest detected: InvoiceStatus, CreditLimitExceeded -- review before committing`.
- **`--context` flag** on `/tailtest:gen`. Pass `--context "description"` (max 300 chars) to override automatic domain detection. The description appears in the detection note and in the generator prompt. Enforced in the MCP tool with a 300-char validation error.
- **Token budget guard.** Before sending to Claude, the prompt is measured against a 7,500-char warning threshold. Optional sections are stripped in order (style sample → domain signals + cluster → category hints) until under budget. Mandatory sections (source file, project context header, generation instructions) are never removed.
- **76 new tests** (1168 total, was 1092 at v0.1.1). New test files: `tests/test_ast_signals.py` (51 tests), additions to `tests/test_generator.py` (18 tests), `tests/test_mcp_tools.py` (2 tests), `tests/test_sanity.py` (1 version update).

### Fixed
- **Detection note empty entities.** When a source file had only function signatures (no enums/exceptions/class definitions), `build_detection_note` produced `"# tailtest detected:  -- review before committing"` (empty middle). Fixed: falls back to function names from `public_function_signatures` when the named-entity list is empty. Regression test added.

---

## [0.1.1] - 2026-04-11

### Added (Phase 7 launch polish)
- **Plugin self-contained install.** `hooks/_bootstrap.py` now tries the plugin's own `src/` directory before falling back to the PATH-based re-exec. `pip install tailtester` is no longer required for the hot loop -- `claude plugin install` alone is sufficient. `pip` remains useful for the standalone CLI (`tailtest run / doctor / scan`).
- **`/tailtest:help` skill.** Lists all 11 skills with one-line descriptions and links to install, quickstart, and configuration docs.
- **Self-installable marketplace manifest** (`marketplace.json` version bumped to rc.2). `claude plugin marketplace add avansaber/tailtest && claude plugin install tailtest@avansaber` works from any machine without waiting for the official marketplace listing.
- **Full documentation suite** at `docs/`. New files: `index.md` (navigation landing), `depth-modes.md` (all 5 depth modes with cost/latency callouts), `findings-catalog.md` (all 9 finding kinds), `faq.md` (10 common questions), `privacy.md` (zero telemetry confirmed, 2 legitimate outbound calls documented).

## [Unreleased]

## [0.1.0-rc.2] - 2026-04-11

### Added
- **64-attack red-team catalog** at `data/redteam/attacks.yaml`. 8 categories covering the OWASP LLM Top 10: prompt injection, jailbreak, PII extraction, data leakage, tool misuse, hallucination induction, scope violation, denial of service. Each attack has a payload, expected outcome, severity, CWE mapping, OWASP category, remediation hint, and applicable language tags.
- **RedTeamRunner** at `src/tailtest/security/redteam/runner.py`. Fires only at `paranoid` depth on `ai_surface: agent` projects with `ai_checks_enabled` not false. Reads agent entry-point code and submits it to `claude -p` for static vulnerability assessment -- one call per attack category, 8 concurrent via `asyncio.gather`. Rate-limits terminal output to 5 findings by severity; writes full results to `.tailtest/reports/redteam-<timestamp>.html`.
- **Agent entry-point detection** in `src/tailtest/core/scan/detectors.py`. Detects agent entry points for Python (`@agent_test`, `main()` in `agents/`, `invoke()`, Anthropic/OpenAI client boost), TypeScript (default exports, Vercel AI SDK, `UserMessage` parameter), and Rust (`pub async fn` with LLM client import). Config override via `.tailtest/config.yaml` `agent.entry_points` takes precedence over auto-detection for custom frameworks. Detected entry points stored as `agent_entry_points: list[EntryPoint]` on `ProjectProfile`.
- **Depth-mode dispatch wiring** in `post_tool_use.py`. Red-team fires AFTER the validator in the paranoid turn. `_should_invoke_redteam()` guards: paranoid + ai_surface:agent + ai_checks_enabled not False. All other depths: no-op.
- **Red-team baseline behavior** via `BaselineManager`. Red-team findings added to `_IMMEDIATE_BASELINE_KINDS` -- they baseline on first detection like SAST/SCA. `list_redteam_entries()` returns all baselined red-team findings for review. Hook applies+updates baseline on the red-team batch using the same manager instance as the test/security pass.
- **Red-team rendering** in `HTMLReporter`. `kind: redteam` findings render in the "Red team" section with reasoning, confidence badge, CWE, and severity stripe. `?kind=redteam` dashboard filter works via the existing generic kind filter.
- **Coordinated disclosure policy** at `docs/redteam-disclosure.md`. 14-day ack, 90-day fix-or-publish timeline, `support@avansaber.com` contact. Linked from the red-team HTML report footer.
- **96 new tests** (1087 total, was 991 at rc.1). Covers: catalog load + schema + 8 categories (26 tests), runner applicable gate + parse + judge + rate-limit + HTML (26 tests), entry-point detection for Python/TS/Rust + config override + profile field (18 tests), depth-mode dispatch including every depth x ai_surface x ai_checks_enabled combination (11 tests), red-team HTML rendering (8 tests), red-team baseline behavior (8 tests).

### Fixed
- **`_read_agent_code` crash on relative project_root.** When project root was passed as a relative path but entry point paths were absolute, `Path.relative_to()` raised `ValueError`. Fixed by resolving both to absolute before calling `relative_to`, with a fallback to the raw path on cross-device edge cases. Caught during Task 6.7 dogfood on CoreCoder.

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
