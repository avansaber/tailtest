# Changelog

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
