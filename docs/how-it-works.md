# How It Works

## The heartbeat: PostToolUse

Every time Claude writes or edits a source file, a lightweight hook fires in under one second. No LLM call. No test generation. It asks one question: is this a file that should have scenarios?

The filter checks the extension, path, and filename. Config files, documentation, templates, styles, migrations, generated code, and the test files themselves are all skipped immediately. If the file passes the filter, the hook records the file path with its language and `new-file`/`legacy-file` status, appends it to `pending_files` in `.tailtest/session.json`, and exits.

Nothing else happens yet.

## Batching at the turn boundary

tailtest never processes one file at a time. If Claude writes five files in one turn -- a service, a model, a controller, two utilities -- the hook fires five times and accumulates five paths into `pending_files`.

Only when you send your next message does Claude read `pending_files` and treat all five as one unit of work. This means a service + model + controller generates one coherent set of scenarios, not three independent sets. Coverage stays connected to real behavior.

## Three-tier execution

tailtest uses the fastest execution method available:

1. **Runner** -- your project's test runner (pytest, vitest, jest, go test, etc.). Preferred when detected. Accurate, fast, catches import errors and environment mismatches.

2. **Direct execution** -- runs the code directly via the language interpreter when no runner is configured. Works on bare Python and Node projects.

3. **Simulation** -- Claude reasons through the code without executing it. Always available. Always labeled explicitly: "Simulating -- no runner available." Never presented as a real test run.

Runner detection happens once at session start by reading your project's manifest files (`pyproject.toml`, `package.json`, `go.mod`, etc.). The detected runner is stored in `session.json` for the duration of the session.

## The silence contract

Pass means nothing. No green checkmarks, no progress bars, no "all tests passed" message.

A failure produces one line and a question. That is the entire output of a failed run. Example:

```
Cumulative credit limit check is failing -- want me to fix this?
```

If execution takes longer than 5 seconds, a single progress line appears: "Running coverage checks..." Nothing else until the result.

This constraint is intentional. Test output should only surface when action is required.

## Session state

tailtest writes `.tailtest/session.json` at session start and updates it on every file save. It is not a log -- it is live state. The hook reads it to check for existing test mappings, runner config, and fix attempt counts before producing any output.

The file is cleared at session start (each Claude Code session gets a fresh `session_id`). It is not meant to persist across sessions.

Add `.tailtest/` to your project's `.gitignore` to keep it out of version control.

See [session-state.md](session-state.md) for the full schema and field descriptions.

## The fix loop

When a test fails, Claude fixes it and runs the tests again. After three failed attempts on the same file, Claude stops and says so explicitly. The attempt counter resets when the file passes.

If you tell Claude to fix some failures but not others, the skipped ones are recorded in `deferred_failures`. They are not resurfaced unless you edit that file again in the same session.

## Style matching

At session start, tailtest samples the three most recently modified test files in your project. It reads the first 30 lines of each, detects custom helper imports and assertion patterns, and injects a style context into Claude's session context. Generated tests match your project's existing conventions -- `TestCase` subclasses vs bare functions, your assertion library, your custom setup helpers -- without any configuration.

## Context compaction

When Claude Code compacts its context during a long session, tailtest re-injects CLAUDE.md and re-emits the current session state. Pending files queued before the compaction survive and are processed correctly after it. Long sessions behave identically to short ones from tailtest's perspective.
