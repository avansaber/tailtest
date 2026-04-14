# Session State

## What session.json is

`.tailtest/session.json` is live state for the current Claude Code session. Not a log. Written fresh at session start, updated on every file save. Each Claude Code session gets a unique `session_id` and a clean state.

Location: `.tailtest/session.json` at your project root. Add `.tailtest/` to your project's `.gitignore` to keep it out of version control.

Schema: [`hooks/session.schema.json`](../hooks/session.schema.json)

## Fields

**`session_id`**
Format: `2026-04-13T10-30-00-a1b2c3`. Unique per Claude Code session.

**`started_at`**
ISO 8601 timestamp when the session began.

**`project_root`**
Absolute path to the project root, as detected by the hook.

**`runners`**
What tailtest detected at session start. One entry per language, each containing `command`, `args`, `test_location`, and optionally `framework`, `style`, `unit_test_dir`, `feature_test_dir`. Empty object (`{}`) means no manifest files were found.

**`depth`**
`simple`, `standard`, or `thorough`. Read from `.tailtest/config.json` at session start. Defaults to `standard`.

**`pending_files`**
Files Claude wrote this turn that have not been processed yet. Each entry has `path`, `language`, and `status` (`new-file` or `legacy-file`). Cleared by Claude at the start of each user turn. This is the field to check when debugging why tailtest did not process a file.

**`touched_files`**
All files edited in this session. Used in no-git projects to determine `new-file` vs `legacy-file` status: first touch in the session = `new-file`, any subsequent touch = `legacy-file`.

**`fix_attempts`**
Per-file count of failed fix attempts. Keyed by file path. Resets to 0 when a file passes. When any file reaches 3, Claude stops attempting fixes for that file and reports it explicitly.

**`generated_tests`**
Maps source file paths to the test files generated for them in this session. When the same source file is edited again, the hook reads this map and emits "update existing test at {path}" instead of regenerating from scratch. Prevents duplicate test files and redundant scenario generation.

**`packages`**
Per-package runner configuration for monorepo projects. Keyed by package directory path (relative to project root). Each entry has the same structure as `runners`. Empty object for flat (non-monorepo) projects. See [monorepo.md](monorepo.md).

**`deferred_failures`**
Failures the user explicitly chose not to fix. Keyed by file path. Not resurfaced unless that file is edited again in the same session.

## A realistic example

A Laravel project mid-session, two files pending, one fix attempt in progress:

```json
{
  "session_id": "2026-04-14T09-15-42-f3e2a1",
  "started_at": "2026-04-14T09:15:42Z",
  "project_root": "/home/user/myproject",
  "runners": {
    "php": {
      "command": "./vendor/bin/phpunit",
      "args": [],
      "framework": "laravel",
      "unit_test_dir": "tests/Unit",
      "feature_test_dir": "tests/Feature"
    }
  },
  "depth": "standard",
  "pending_files": [
    {
      "path": "app/Services/InvoiceService.php",
      "language": "php",
      "status": "new-file"
    },
    {
      "path": "app/Models/Invoice.php",
      "language": "php",
      "status": "new-file"
    }
  ],
  "touched_files": [
    "app/Services/InvoiceService.php",
    "app/Models/Invoice.php"
  ],
  "fix_attempts": {
    "app/Services/InvoiceService.php": 1
  },
  "generated_tests": {
    "app/Services/BillingService.php": "tests/Unit/BillingServiceTest.php"
  },
  "packages": {},
  "deferred_failures": {}
}
```

## Diagnostic use

When tailtest is silent about a file you expected it to process:

1. Check `runners` -- if it is `{}`, no manifest was found and tailtest has nothing to work with
2. Check `pending_files` after a file save -- if the file is absent, it was filtered out before reaching the session
3. Check `fix_attempts` for the file path -- if it is at 3, tailtest has stopped attempting fixes

```bash
cat .tailtest/session.json
```

The three most useful fields for debugging are `runners`, `pending_files`, and `fix_attempts`.
