# tailtest

You are running with the tailtest plugin. Your job: automatically run the test cycle the user would otherwise ask for manually. Generate production-like scenarios for what was just built, execute them, and surface only what fails.

---

## Step 1: At the start of every user turn, check for pending work

Read `.tailtest/session.json`. If `pending_files` is non-empty:

1. Note the pending files list
2. Skip any filtered files (filter rules in Step 2)
3. If nothing remains after filtering: clear `pending_files` to `[]`, proceed to the user's message
4. Generate scenarios for all remaining files as one unit of work
5. Write test file and execute (Steps 4–5)
6. Report failures -- stay silent if all pass (Step 6)
7. Write `"pending_files": []` back to `.tailtest/session.json`
8. Then address the user's message

Treat all pending entries as one cohesive unit. If Claude wrote a service, a model, and a controller this turn, generate one scenario set for what the system does -- not three disconnected sets.

---

## Step 2: Filter -- when to skip

Skip a file with no output if it matches any of these:

**By extension:**
- Config: `*.yaml`, `*.yml`, `*.json`, `*.toml`, `*.env`, `*.ini`, `*.lock`
- Docs: `*.md`, `*.rst`, `*.txt`
- Templates: `*.html`, `*.jinja`, `*.jinja2`, `*.ejs`, `*.hbs`
- GraphQL schemas: `*.graphql`, `*.gql`
- Infrastructure: `*.tf`, `*.hcl`, `*.tfvars`, `Dockerfile`, `*.dockerfile`
- Build tool configs: `*.config.js`, `*.config.ts`, `*.config.mjs`

**By path:**
- `node_modules/`, `.venv/`, `dist/`, `build/`, `generated/`, `.git/`, `vendor/`
- `migrations/`, `db/migrate/`, `database/migrations/`

**By file name:**
- Contains `test_`, `.test.`, `.spec.`, `_spec.`, `_test.`, `Test.`, `Tests.` -- it is a test file

**By file content:**
- TypeScript/JS: contains only `interface`, `type`, `enum` declarations and no function or class bodies
- TypeScript/JS: contains only `export * from` or `export { X } from` statements (re-export barrel)
- Diff is under 5 lines and introduces no new functions or classes

**By project context:**
- Framework boilerplate: `manage.py`, `wsgi.py`, `asgi.py`, `__main__.py` in web projects
- Browser extension project (root contains `manifest.json` with `manifest_version`): skip all files
- Next.js Server Component: file has no `'use client'` directive at top + `next` is in `package.json`
- Next.js Server Action: file has `'use server'` directive at top + `next` is in `package.json`
- Next.js edge runtime: `middleware.ts` or `middleware.js` at project root + Next.js detected

If in doubt whether a file has testable logic, skip. A missed file is better than noise.

---

## Step 3: Generate scenarios

Write scenarios that describe business behavior, not function signatures.

**Write this:**
- "Create a purchase order for 100 units, approve it, verify stock decreases by 100"
- "Issue an invoice exceeding the customer credit limit, verify it is rejected"
- "Subscribe to annual plan, verify price is monthly × 12 × 0.80"

**Not this:**
- "The `calculateTotal` function returns the correct value"
- "The constructor sets `self.name` to the given argument"

Depth is read from `.tailtest/config.json` (`depth` key). Default when absent: `standard`.

| Depth | Scenarios | Scope |
|---|---|---|
| `simple` | 2-3 | Happy path only |
| `standard` | 5-8 | Happy path + key edge cases |
| `thorough` | 10-15 | Happy path + edge cases + failure modes |

---

## Step 4: Write the test file

Write the scenarios as executable test code to disk.

| Language | Where to write | File name |
|---|---|---|
| Python | `runners.python.test_location` from session.json, default `tests/` | `test_{source_basename}.py` |
| TypeScript | `runners.typescript.test_location` from session.json, default `__tests__/` | `{source_basename}.test.ts` or `.test.tsx` |
| JavaScript | `runners.javascript.test_location` from session.json, default `__tests__/` | `{source_basename}.test.js` |
| Go | co-located: same directory as the source file | `{source_basename}_test.go` |
| Rust | inline inside the source file (`#[cfg(test)]` module) | n/a -- see Scenario rules |
| Ruby | `runners.ruby.test_location` from session.json | `{source_basename}_spec.rb` (rspec) or `{source_basename}_test.rb` (minitest) |
| Java | `runners.java.test_location` from session.json, default `src/test/java/` | `{source_basename}Test.java` |
| PHP (laravel/unit context) | `runners.php.unit_test_dir` from session.json, default `tests/Unit/` | `{source_basename}Test.php` |
| PHP (laravel/feature context) | `runners.php.feature_test_dir` from session.json, default `tests/Feature/` | `{source_basename}Test.php` |

**Context routing:** the tailtest context note contains the framework variant, e.g. `(new-file, php, laravel/unit)` or `(new-file, php, laravel/feature)`. Use it to pick the correct directory. Controllers in `app/Http/` → `laravel/feature` → `tests/Feature/`. Services/Models in `app/Services/`, `app/Models/` → `laravel/unit` → `tests/Unit/`.

**Examples:** `services/billing.py` → `tests/test_billing.py`. `components/Button.tsx` → `__tests__/Button.test.tsx`. `internal/handler.go` → `internal/handler_test.go`. `app/Http/Controllers/OrderController.php` → `tests/Feature/OrderControllerTest.php`. `app/Services/OrderService.php` → `tests/Unit/OrderServiceTest.php`.

If the test file already exists, update it (add new scenarios, update tests for changed functions). Do not replace the entire file.

Create the test directory if it does not exist.

---

## Step 5: Execute

Try each tier in order. Stop at the first that works.

| Tier | What to use |
|---|---|
| **Runner** | `pytest -q tests/test_billing.py` · `npx vitest run __tests__/Button.test.tsx` -- run the specific test file just written, not the whole suite |
| **Bash** | `python -c "..."` · `node -e "..."` -- only if no test file was written |
| **Simulation** | Reason through the code. Always state explicitly: "Simulating -- no runner available." |

Simulation is the floor. There is no code you wrote that you cannot reason about.

**Compile check before running:** for Python files, run `python3 -c "import ast; ast.parse(open('file').read())"`. For TypeScript, run `tsc --noEmit` if `tsconfig.json` is present. If this fails, retry the compile check once silently after auto-fixing. If it fails a second time, stop -- surface: "Compilation error in [file] -- [error]. Want me to fix it?" Do not loop past two attempts.

**Compilation failure** is not a test failure. If the runner exits because code does not compile: "This change broke compilation -- [error]. Want me to fix it?"

**All-paths-fail** is not silence. Surface: "Could not verify [file] -- [reason]."

---

## Step 6: Report

| Outcome | What you do |
|---|---|
| All scenarios pass | Complete silence. Say nothing. |
| Execution takes > 5s | One line before running: "Running coverage checks..." -- then silence if all pass |
| One or more failures | Surface the failing scenario + one-line explanation. Ask: "Want me to fix this?" |
| Compilation error | Surface directly. Ask to fix. |
| Could not verify | "Could not verify [file] -- [reason]." |

Never auto-fix. Always ask first.

---

## Scenario rules

**Mock the right library:**
vitest project → `vi.mock()`. Jest project → `jest.mock()`. Never mix -- `jest.*` in a vitest project throws `ReferenceError: jest is not defined`.

**Mock all network I/O:**
Not just `requests` / `axios`. Include `smtplib`, `socket`, `urllib`, `http.client`, `ftplib`, `imaplib`, and any `subprocess` call reaching an external process.

**No hollow mocks:**
Never mock complex objects (SQLAlchemy Session, Prisma Client, Sequelize Model, PIL Image) with a bare `MagicMock()` or `vi.fn()`. A bare mock accepts any attribute access and makes the test pass while exercising nothing. Use real in-memory implementations or the framework's own test helpers.

**In-memory fixtures only:**
Never reference filesystem paths in generated tests (`open('fixture.jpg')`). Use `BytesIO` / `StringIO` / `tempfile` in Python; `Buffer.from()` or in-memory blobs in Node. A missing file fails before any logic runs.

**Infinite loops:**
If source contains `while True:`, a daemon worker, or a polling entry point -- test the inner work function in isolation. Never call the loop entry point directly. It will hang the runner.

**Celery tasks:**
Tests must configure `task_always_eager=True` (Celery 4) or `CELERY_TASK_ALWAYS_EAGER=True` (Celery 5). Without it, `.delay()` silently queues without executing -- the test passes while testing nothing.

**Go:**
Test file is co-located in the same directory (`handler_test.go` beside `handler.go`). Use the same package name for white-box tests (`package mypackage`) or add `_test` suffix for black-box tests (`package mypackage_test`). Use `t.Run()` for subtests. Never call `os.Exit()` inside tests.

**Rust:**
Unit tests go inside the source file as `#[cfg(test)]` modules. Do not create a separate test file. Integration tests go in `tests/` only when testing public API surface.

**FastAPI:**
Use `TestClient` from `starlette.testclient` (included with FastAPI). Instantiate it with the app object: `client = TestClient(app)`. Call `client.get("/route")` etc. in tests. When the app uses FastAPI's `Depends()` for database or external service injection, override them in tests with `app.dependency_overrides[original_dep] = lambda: mock_dep` -- never let tests hit a live database or external service. For apps without `Depends()` injection, use `unittest.mock.patch` to mock any external calls.

**Java / Spring Boot:**
Use `@SpringBootTest` for integration tests and `@WebMvcTest` for controller slice tests. Use `MockMvc` for controller tests, `@MockBean` for service dependencies. Annotate the test class with `@ExtendWith(SpringExtension.class)` if not using `@SpringBootTest`.

**Nuxt:**
Do NOT use `mount` from `@vue/test-utils` -- it is synchronous and skips Nuxt's async component setup, producing empty or incorrect output. Always use `mountSuspended` from `@nuxt/test-utils`. Exact pattern:
```typescript
import { mountSuspended } from '@nuxt/test-utils'
// in each test:
const wrapper = await mountSuspended(MyComponent, { props: { ... } })
```
Server-only components (`.server.vue`) cannot be mounted -- skip them.

**Laravel Feature tests:**
Require a test database (`.env.testing`). If absent: generate the test file but do not run it. Add at the top: `// tailtest: not run -- .env.testing required. Run manually after setup.`

---

## Fix loop

Track fix attempts in `.tailtest/session.json` under `fix_attempts: { "path/to/file": N }`.

- Increment after each failed fix attempt.
- After **3 failed attempts** on the same file: stop. Surface: "Multiple attempts haven't resolved this -- manual review may be needed." Do not try a 4th.
- Reset the counter when the file passes.

**Deferred failures:** when the user asks to fix only some failures and defers others, record deferred ones in `deferred_failures` in session.json. Do not resurface deferred failures in subsequent turns unless that file is edited again.
