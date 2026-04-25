Run an adversarial pass on `$ARGUMENTS` -- explicitly try to break the source code.

Read the source file at `$ARGUMENTS`. Generate 8-12 adversarial test scenarios drawn from the R15 categories in CLAUDE.md (boundary inputs, format / injection, type confusion, concurrent state, time / locale edges, error handling under partial failures, resource exhaustion, off-by-one logic). Pick categories that genuinely apply to this file; skip any that do not (and note the skip).

This command bypasses the project's configured depth and forces an adversarial-biased pass on the named file regardless of `depth` setting in `.tailtest/config.json`.

**Where to write the test file:** write to a SEPARATE hunt test file, not the regular test file for this source. Naming convention:

| Source file | Hunt test file |
|---|---|
| `services/billing.py` | `tests/test_billing_hunt.py` |
| `app/Http/Controllers/OrderController.php` | `tests/Feature/OrderControllerHuntTest.php` |
| `internal/handler.go` | `internal/handler_hunt_test.go` |

The hunt file is intentionally separate so it does not contaminate the main test suite. The user decides after review whether to keep, merge into the main test file, or discard.

**Step-by-step behavior:**

1. Read the source file at `$ARGUMENTS`
2. Output a SCENARIO PLAN with 8-12 adversarial scenarios, each labeled `[adversarial: <category>]`. State which categories were skipped and why.
3. Write the test file at the hunt path (see table above)
4. Run the hunt test file using the configured runner (`pytest -q tests/test_<basename>_hunt.py` etc.)
5. For any failure, apply R12 classification (real_bug / environment / test_bug). Report each failing scenario with category and classification: `[adversarial: type-confusion] real_bug -- function returns None on int input where str expected.`
6. If all pass: `tailtest hunt: {N} adversarial scenarios on {file}, all passed.`

**Do not auto-fix.** Always ask before fixing any real_bug found by hunt.

**No update-existing-tests behavior.** Hunt always writes to the separate hunt test file. If the hunt file already exists, replace its contents (the user is asking for a fresh hunt).

Treat the file as new-file regardless of git status -- hunt explicitly requests generation even on legacy files or files tailtest would normally skip.
