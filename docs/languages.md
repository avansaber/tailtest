# Supported Languages and Frameworks

## Detection: automatic at session start

tailtest reads your project's manifest files -- not your source files -- to detect what language and test runner you use. This happens once per session in under 2 seconds. Detected runners are stored in `.tailtest/session.json` for the duration of the session.

## Language reference

| Language | Detected from | Runner command | Test file location | Framework variants |
|---|---|---|---|---|
| Python | `pyproject.toml` | `pytest -q` | `tests/test_{name}.py` | Django, FastAPI |
| TypeScript | `package.json` + `tsconfig.json` | `vitest run` or `jest --passWithNoTests` | `__tests__/{name}.test.ts` | Next.js, Nuxt |
| JavaScript | `package.json` (no tsconfig) | `vitest run` or `jest --passWithNoTests` | `__tests__/{name}.test.js` | Next.js, Nuxt |
| Go | `go.mod` | `go test ./...` | co-located `{name}_test.go` | -- |
| Ruby | `Gemfile` (rspec or minitest) | `bundle exec rspec` or `bundle exec rake test` | `spec/{name}_spec.rb` or `test/{name}_test.rb` | Rails |
| PHP | `composer.json` + `phpunit.xml` | `./vendor/bin/phpunit` | `tests/Feature/` or `tests/Unit/` | Laravel |
| Java | `pom.xml` or `build.gradle` | `./mvnw test` or `./gradlew test` | `src/test/java/{Name}Test.java` | Spring Boot |
| Rust | `Cargo.toml` | `cargo test` | inline `#[cfg(test)]` in source file | -- |

## Per-language notes

### Go
Test files are always co-located with the source file, never in a separate `tests/` directory. `internal/handler.go` produces `internal/handler_test.go` in the same directory.

Go tests use either the same package (`package handler`) for white-box tests, or the external test package (`package handler_test`) for black-box tests. tailtest defaults to white-box (`package handler`) unless the file is at a public API boundary.

### Rust
No separate test file is created. Tests are written as a `#[cfg(test)]` module appended to the source file. Rust is the only language where the test lives inside the source file.

### PHP / Laravel
The test location depends on what the file is:
- `app/Http/Controllers/` → `tests/Feature/`
- `app/Services/` and `app/Models/` → `tests/Unit/`

This routing is automatic. If your Laravel project does not have `.env.testing` configured, tailtest writes the Feature test file but adds a comment noting it could not be executed and must be run manually after database setup.

### Next.js
Server Components (no `'use client'` directive) and Server Actions (`'use server'` directive) are skipped. Files in `middleware.ts` or `middleware.js` are skipped. Client Components are tested normally.

### Nuxt
tailtest uses `mountSuspended` from `@nuxt/test-utils` for Vue component tests. Using `mount` from `@vue/test-utils` in a Nuxt project will fail to properly resolve Nuxt composables and auto-imports. If `@nuxt/test-utils` is not installed, install it before tailtest can run Nuxt component tests.

Server-only `.server.vue` components are skipped.

### Python: Django and FastAPI
Both are detected as framework variants within Python. The detection reads `pyproject.toml`, `requirements.txt`, or `setup.py` for the relevant package names.

FastAPI tests use `TestClient` with dependency overrides rather than hitting live services:
```python
app.dependency_overrides[get_db] = override_get_db
```

Django tests use the standard `TestCase` with the Django test client.

### TypeScript / JavaScript: vitest vs jest
When both vitest and jest are installed, vitest is preferred. When only jest is present, jest is used.

For vitest, the environment is selected automatically: React, Next.js, Vue, and React Native projects get `jsdom`; pure backend projects get `node`. Using the wrong environment breaks Node.js built-ins (`process`, `Buffer`, etc.) in backend projects, so this selection matters.

### Vue and Svelte
`.vue` and `.svelte` files are treated as JavaScript (or TypeScript when a `tsconfig.json` is present). Test files use `.test.js` or `.test.ts` accordingly. They are not skipped by default.

## Languages that require a configured runner

Go, Ruby, PHP, Java, and Rust require their manifest file to be present. If none is found, tailtest is completely silent for files in those languages -- it does not generate tests, show errors, or surface any output. This is intentional: these languages require an existing project setup to run tests at all.

Python and TypeScript/JavaScript are bootstrapped if no runner is configured (see below).

## Runner bootstrapping

For Python projects where no test runner is found, tailtest silently adds `pytest` to `pyproject.toml` and creates a minimal config if needed. For TypeScript/JavaScript projects, tailtest adds `vitest` and creates a minimal `vitest.config.ts`. This happens before the first test is generated and requires no action from you.

## Jupyter notebooks

`.ipynb` files are not currently supported. The `NotebookEdit` tool fires the PostToolUse hook, but notebook files are not mapped to a language and are silently skipped.
