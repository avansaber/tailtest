# Monorepo Support

## Automatic detection

tailtest detects monorepo layouts at session start by checking the project root for any of these marker files:

- `pnpm-workspace.yaml`
- `nx.json`
- `turbo.json`
- `lerna.json`
- `rush.json`

If none of these are present, tailtest also checks by structure: if two or more immediate subdirectories each have their own `package.json`, `pyproject.toml`, or `composer.json`, the project is treated as a monorepo.

## Per-package runner resolution

For each detected package, tailtest runs the same runner detection it would run for a standalone project. `packages/api/` with a `pyproject.toml` gets pytest. `packages/web/` with a `package.json` and vitest config gets vitest. Each package's runner is stored independently in the `packages` field of `.tailtest/session.json`:

```json
"packages": {
  "packages/api": {
    "python": {
      "command": "pytest",
      "args": ["-q"],
      "test_location": "packages/api/tests/"
    }
  },
  "packages/web": {
    "typescript": {
      "command": "vitest",
      "args": ["run"],
      "test_location": "packages/web/__tests__/"
    }
  }
}
```

## File routing

When tailtest processes a file, it finds which package the file belongs to by matching the longest prefix. `packages/api/services/billing.py` routes to `packages/api`, uses that package's pytest runner, and writes its test to `packages/api/tests/test_billing.py`.

A file that does not belong to any package (for example, a shared utility at the repo root) falls back to the root-level runner if one is configured.

## Scan depth

tailtest scans up to two directory levels deep for packages. Both `packages/api/` (depth 1) and `packages/api/core/` (depth 2) are detected as packages if they have their own manifest files. Directories named `node_modules`, `.venv`, `dist`, `build`, `vendor`, `.next`, `.nuxt`, and `.svelte-kit` are skipped.

## Mixed-language monorepos

A Python API package and a TypeScript frontend package can coexist in the same monorepo. Each file is matched to its package, which carries its own language-specific runner. A Python file in `packages/api/` gets pytest; a TypeScript file in `packages/web/` gets vitest. No configuration is needed for this.
