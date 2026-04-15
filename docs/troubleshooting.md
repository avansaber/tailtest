# Troubleshooting

## tailtest is not generating tests for a file I just wrote

Work through this checklist in order:

1. **Is the file's extension skipped?** Check [filter-reference.md](filter-reference.md) for the full list. `.yaml`, `.json`, `.css`, `.html`, and many others are skipped by default.

2. **Is the file in a skipped directory?** Paths containing `node_modules/`, `generated/`, `migrations/`, `dist/`, `build/`, `.next/`, and others are skipped. See the full path list in [filter-reference.md](filter-reference.md).

3. **Is the file matched by `.tailtest-ignore`?** Check if a `.tailtest-ignore` file exists and whether any pattern matches the file path.

4. **Does the language require a configured runner?** Go, Ruby, PHP, Java, and Rust require their respective manifest file (`go.mod`, `Gemfile`, `composer.json`, `pom.xml`/`build.gradle`, `Cargo.toml`). If none is found, tailtest is completely silent for those languages. Python, TypeScript, and JavaScript always queue -- if no runner is detected, Claude falls back to direct execution or simulation. Check `.tailtest/session.json` → `runners` to see what was detected.

5. **Is the file a framework boilerplate entry point?** `manage.py`, `wsgi.py`, `asgi.py`, `__main__.py`, `middleware.ts`, `middleware.js` are skipped.

6. **Is the diff very small?** A change under 5 lines that introduces no new functions or classes is considered a minor edit and skipped.

## tailtest is silent about an existing file I edited

This is expected behavior. When Claude edits a file that already existed before the session (tracked by git), tailtest marks it as `legacy-file`. If no test file exists for it, tailtest is completely silent.

Use `/t filename` to generate tests for any existing file on demand. See [existing-projects.md](existing-projects.md) and [slash-command.md](slash-command.md).

## The runner was not detected

For Python: is `pyproject.toml` present at the project root or a parent directory? For Node: is `package.json` present? For PHP/Go/Ruby/Java/Rust: is the respective manifest present?

Confirm by reading `.tailtest/session.json` → `runners`. If it is `{}`, no manifest was found.

**Python, TypeScript, and JavaScript still work without a detected runner.** If `runners` is `{}` for a Python or JS/TS project, tailtest will still queue files and Claude will execute tests directly (`python -m pytest`, `npx vitest`, or simulation). Adding a manifest file (`pyproject.toml`, `package.json`) lets session_start detect your runner and test location, which produces more accurate output -- but it is not required.

**Go, Ruby, PHP, Java, and Rust do not work without a detected runner.** Files in these languages are silently skipped if the manifest is absent.

For monorepos: check `packages` in `session.json`. If your package's directory is absent, the package was not detected -- confirm the package directory has its own manifest file.

## Tests are generating but failing immediately with import errors

The test runner may not be installed. tailtest bootstraps pytest and vitest silently when they are absent, but this requires write access to the project directory and a working package manager (`pip` or `npm`).

Check that pytest (Python) or vitest (Node) can be invoked in your project. If vitest was just added to `package.json`, run `npm install` before the next session.

## tailtest is resurfacing the same failure on every turn

Check `deferred_failures` in `session.json`. If the failure is listed there, it was deferred but the file was edited again, which reactivated it.

If `fix_attempts` shows 3 for that file, tailtest has stopped attempting fixes and is reporting the failure for your attention.

## I'm in a monorepo and the wrong runner is being used

Check `packages` in `session.json`. Confirm that the package directory for the file you edited appears as a key. If it is absent, the package was not detected -- verify the package directory has its own manifest file.

If the package appears but the runner is wrong, check which manifest file was found (the `command` field will tell you what runner was detected).

## Session.json shows files as legacy-file when they should be new-file

In a git project: tailtest uses `git ls-files` to determine status. If a file is committed, it is `legacy-file`. To generate fresh tests for a committed file, use `/t`.

In a no-git project: the first edit in the session is `new-file`. A second edit in the same session is `legacy-file`. Each new Claude Code session resets this tracking.

## Quick diagnostic

```bash
cat .tailtest/session.json
```

The three most useful fields:
- `runners` -- confirms what was detected at session start
- `pending_files` -- confirms files queued for the current turn
- `fix_attempts` -- confirms how many failed fix attempts have occurred per file

If `pending_files` is empty after a file save, the file was filtered before reaching the session state. Check the filter conditions above.
