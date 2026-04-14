# What tailtest Skips and Why

The PostToolUse hook fires on every file Claude writes or edits. Without a filter, config file updates, template changes, and migration tweaks would all trigger test generation. The filter is what makes tailtest quiet in practice.

If tailtest was silent about a file you expected it to process, this page explains why.

## Skipped file extensions

**Config and data**
`.yaml`, `.yml`, `.json`, `.toml`, `.env`, `.ini`, `.lock`, `.cfg`, `.conf`, `.properties`, `.plist`, `.xml`, `.xsd`, `.wsdl`, `.csv`, `.tsv`, `.proto`, `.thrift`, `.avsc`

**Documentation**
`.md`, `.rst`, `.txt`, `.adoc`, `.asciidoc`

**Templates and markup**
`.html`, `.htm`, `.jinja`, `.jinja2`, `.ejs`, `.hbs`, `.njk`, `.twig`, `.mustache`, `.erb`, `.haml`

**Styles**
`.css`, `.scss`, `.sass`, `.less`, `.styl`

**Infrastructure-as-code**
`.tf`, `.hcl`, `.tfvars`

**Media**
`.svg`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.ico`, `.webp`, `.mp4`, `.mp3`, `.wav`, `.pdf`

**Shell scripts**
`.sh`, `.bash`, `.zsh`, `.fish`, `.ps1`, `.bat`, `.cmd`

**SQL**
`.sql`

**GraphQL**
`.graphql`, `.gql`

**Build configs**
Any file whose name ends with `.config.js`, `.config.ts`, `.config.mjs`, `.config.cjs`, `.config.jsx`, `.config.tsx`. Note: `vitest.config.ts` is skipped. `Button.config.ts` is also skipped. `ButtonConfig.ts` is not skipped (does not match the suffix pattern).

## Skipped paths

`node_modules/`, `.venv/`, `venv/`, `.env/`, `env/`, `dist/`, `build/`, `generated/`, `.git/`, `vendor/`, `migrations/`, `db/migrate/`, `database/migrations/`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `target/`, `.cargo/`, `coverage/`, `.nyc_output/`, `.next/`, `.nuxt/`, `.svelte-kit/`, `k8s/`, `deploy/`, `infra/`

## Skipped by filename

**Test files** (any file whose name contains):
`test_`, `_test.`, `.test.`, `.spec.`, `_spec.`, `Test.`, `Tests.`, `IT.`

tailtest does not generate tests for test files.

**Framework boilerplate entry points:**
`manage.py`, `wsgi.py`, `asgi.py`, `__main__.py`, `middleware.ts`, `middleware.js`

## Skipped by content (Claude's responsibility)

The hook does not read file content. These checks happen in Claude after a file is queued:

- TypeScript/JS with only `interface`, `type`, `enum` declarations and no function or class bodies
- Re-export barrels: files that only contain `export * from` or `export { X } from` statements
- Very small diffs (under 5 lines introducing no new functions or classes)
- Next.js Server Components (no `'use client'` directive + Next.js in package.json)
- Next.js Server Actions (`'use server'` directive + Next.js in package.json)
- Browser extension projects (root `manifest.json` with `manifest_version`)

## Generated file patterns

**Go:** files starting with `mock_` or ending with `_mock.go`, `_gen.go`, `.pb.go`

**TypeScript/JavaScript:** files ending with `.generated.ts`, `.graphql.ts`

## The .tailtest-ignore escape hatch

Any path matching a pattern in `.tailtest-ignore` is skipped before all other filter checks. See [configuration.md](configuration.md) for syntax.

## What is NOT skipped by default

These file types are processed normally:

- `.vue` and `.svelte` (treated as JavaScript or TypeScript depending on tsconfig presence)
- `.jsx` and `.tsx`
- `.mjs`, `.cjs`, `.mts`, `.cts`
- `.pyx` (Cython) and `.pyi` (Python stubs) -- treated as Python

If you need to silence any of these for a specific path, use `.tailtest-ignore`.
