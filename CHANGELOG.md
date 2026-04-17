# Changelog

## v3.8.0 -- 2026-04-16
Compatibility update for Claude Opus 4.7. Opus 4.7 follows instructions more literally and uses fewer tool calls by default than Opus 4.6. Two changes to the hook's context note address this: (1) when multiple files are pending, the note now explicitly says "write tests for all of them" -- Opus 4.7 will not silently generalize a write-one-test instruction across N files; (2) the note ending now reads "write test file(s) to disk, run them, report results -- then respond to the user" instead of "Read session.json before responding to the user," which previously let Opus 4.7 read the file and respond without ever writing tests.

## v3.7.1 -- 2026-04-15
Fixes a bug where Python, TypeScript, and JavaScript files were silently skipped when no runner was detected at session start (e.g., a project without a `pyproject.toml` or `package.json`). These languages now queue correctly regardless of manifest presence -- Claude falls back to direct execution or simulation if no runner is configured. Go, Ruby, PHP, Java, and Rust are unchanged: they still require their respective manifest file.

## v3.7.0 -- 2026-04-15
First-install ramp-up scan. When tailtest starts on a project for the very first time, it now automatically queues the most important existing files for an initial coverage pass -- no manual `/t` commands needed. Files are selected by scoring git activity (commit frequency), path signal (services, models, controllers score higher), and size (skips tiny and giant files). The top 7 are queued by default; configure with `ramp_up_limit` in `.tailtest/config.json` (0 to disable, max 15). A sentinel file prevents the scan from re-firing on crash-and-restart. Before running the batch, Claude emits `tailtest: running initial coverage scan on N file(s)...` so you know what is happening.

## v3.6.0 -- 2026-04-14
Adds three vibe-coder-focused features. `/tailtest off` and `/tailtest on` commands let you pause and resume testing mid-session without uninstalling. Session reports are now written automatically to `.tailtest/reports/` at session end -- a permanent, shareable markdown file of what was tested and what failed. Typing `/summary` also saves a report snapshot. Finally, when all scenarios pass, tailtest now emits `tailtest: N scenarios -- all passed.` instead of staying silent, giving non-technical users clear confirmation that their changes are safe.

## v3.5.1 -- 2026-04-14
Adds the `/summary` slash command. Type `/summary` at any point in a session to see which files were tested, where test files were written, and whether any failures were fixed, deferred, or remain unresolved. Also ships the full `docs/` reference (10 pages covering quickstart, architecture, all 8 languages, monorepo, configuration, session state, and troubleshooting) and corrects the install command documentation.

## v3.5.0 -- 2026-04-14
Monorepo support. tailtest now detects pnpm workspaces, Nx, Turborepo, Lerna, and Rush layouts at session start. Files in sub-packages use that package's test runner and test location. Files outside all packages fall back to the root runner.

## v3.4.0 -- 2026-04-14
Existing project ramp-up. Added `/t <file>` slash command to trigger test generation on any file regardless of git status. Explicit CLAUDE.md documentation for legacy-file behavior and progressive coverage strategy.

## v3.3.0 -- 2026-04-14
Multi-file coherence. tailtest now tracks which test file it generated for each source file within a session (`generated_tests` in session.json). When the same source file is edited again, the hook emits "update existing test at {path}" instead of regenerating from scratch.

## v3.2.0 -- 2026-04-14
Style awareness. At session start, tailtest samples the three most recently modified test files and injects a style context snippet into Claude's context. Generated tests now match the project's existing patterns (TestCase subclasses vs bare functions, assertion style, custom helpers).

## v3.1.0 -- 2026-04-14
Framework and language breadth. Added runner detection for Laravel/PHP, Go, Ruby (rspec + minitest), Rust (inline tests), Java (Maven/Gradle/Spring), Django, FastAPI, Next.js, and Nuxt. Framework-specific instructions in CLAUDE.md (mountSuspended for Nuxt, dependency_overrides for FastAPI, @SpringBootTest for Java).

## v3.0.0 -- 2026-04-14
Initial release. PostToolUse + SessionStart hooks. Intelligence filter (skips configs, tests, generated files, boilerplate). Python and TypeScript support. Session state in `.tailtest/session.json`. Escape hatch via `.tailtest-ignore`. Fix-attempt tracking (stops after 3 failed attempts).
