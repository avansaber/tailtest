# Findings catalog

Every finding tailtest emits has a `kind` field that identifies what triggered it. This page catalogs all finding kinds, what fires them, what fields they carry, and how to baseline them.

## `test_failure`

**What triggers it:** A test in the impacted test set failed.

**Fields:**
- `test_id` -- the test node ID (e.g. `tests/test_util.py::test_multiply`)
- `message` -- the assertion message or exception text
- `duration_ms` -- how long the test took

**Example output:**
```
tailtest: 11/14 tests passed · 3 failed · 1.9s
  FAIL tests/test_util.py::test_multiply -- AssertionError: expected 6, got 0
```

**How to baseline:** Test failures are not baselined. Fix the test. If the failure is intentional (you are mid-refactor), tailtest auto-baselines it after 3 consecutive failing runs. You can also run `/tailtest:debt` to review and manage auto-baselined failures.

---

## `lint`

**What triggers it:** A lint finding from ruff (Python), ESLint (JS/TS), or clippy (Rust) on a changed file.

**Fields:**
- `tool` -- `ruff`, `eslint`, or `clippy`
- `rule` -- the rule ID (e.g. `E501`, `no-unused-vars`, `clippy::unwrap_used`)
- `file` -- relative path
- `line` / `col`
- `message`

**Example output:**
```
tailtest: lint · ruff E501 src/util.py:42 -- line too long (104 > 88)
```

**How to baseline:** Run `/tailtest:debt` and select the entry, or add an inline suppression (`# noqa: E501`, `// eslint-disable-next-line`) if the finding is intentional.

---

## `secret`

**What triggers it:** gitleaks detected a potential credential or secret in a changed file.

**Fields:**
- `rule_id` -- the gitleaks rule that fired (e.g. `generic-api-key`)
- `file` -- relative path
- `line`
- `description` -- human-readable description of what was found
- `cwe` -- always `CWE-798` (hardcoded credentials)
- `verified` -- `true` if live-verification confirmed the secret is active (future phase)

**Example output:**
```
tailtest: secret · CWE-798 src/client.py:17 -- generic-api-key (gitleaks)
```

**How to baseline:** Only baseline if the finding is a confirmed false positive (e.g., a test fixture with a placeholder key). Run `/tailtest:debt` to review and accept. Do not baseline a real credential -- rotate it instead.

---

## `sast`

**What triggers it:** Semgrep matched a rule from the configured ruleset (`p/default` by default) on a changed file.

**Fields:**
- `rule_id` -- the Semgrep rule ID (e.g. `python.lang.security.use-defusedxml`)
- `file`, `line`, `col`
- `message` -- the Semgrep finding message
- `severity` -- `error`, `warning`, or `info`
- `cwe` -- CWE ID if the rule carries one

**Example output:**
```
tailtest: sast · warning src/parser.py:31 -- python.lang.security.use-defusedxml
```

**How to baseline:** Run `/tailtest:debt`. If Semgrep's inline suppression is more appropriate, add `# nosemgrep: <rule-id>` on the flagged line.

---

## `sca`

**What triggers it:** The OSV.dev API returned an advisory for a package in your manifest (`pyproject.toml` or `package.json`) after a manifest edit.

**Fields:**
- `package` -- package name
- `version` -- the installed version
- `advisory_id` -- OSV advisory ID (e.g. `GHSA-xxxx-xxxx-xxxx` or `PYSEC-2024-xxx`)
- `aliases` -- CVE and other IDs, deduped
- `severity` -- `critical`, `high`, `medium`, `low`, or `info`
- `cwe` -- CWE IDs if the advisory carries them
- `fixed_in` -- the earliest fixed version, if known
- `description` -- short advisory summary

**Example output:**
```
tailtest: sca · high requests@2.28.0 -- GHSA-j8r2-6x86-q33q (fix: 2.31.0)
```

**How to baseline:** Upgrade to the fixed version. If upgrading is not immediately possible, run `/tailtest:debt` to silence it while you track remediation.

---

## `coverage`

**What triggers it:** Delta coverage for lines your edit added fell below threshold (default: new lines must have at least one covering test).

**Fields:**
- `file` -- the edited file
- `uncovered_lines` -- list of line numbers in the edit with no covering test
- `delta_pct` -- coverage percentage for the new lines only

**Example output:**
```
tailtest: coverage · src/util.py -- 3 new lines uncovered (lines 44-46)
```

**How to baseline:** Write a test for the uncovered lines, or use `/tailtest:gen <file>` to have Claude draft a starter. If the code is an intentional stub, run `/tailtest:debt` to silence it.

---

## `redteam`

**What triggers it:** The red-team runner (paranoid depth only, `ai_surface: agent` projects only) found a plausible LLM-specific attack vector in the code.

**Fields:**
- `attack_category` -- one of `prompt_injection`, `data_exfiltration`, `tool_call_forgery`, `context_poisoning`, or `other`
- `file`, `line`
- `reasoning` -- the LLM judge's explanation of the risk
- `confidence` -- `high`, `medium`, or `low`

**Example output:**
```
tailtest: redteam · prompt_injection src/agent.py:88 -- user input reaches system prompt without sanitization (confidence: high)
```

**How to baseline:** Treat red-team findings like security findings: fix the issue or, if it is a false positive, document why and run `/tailtest:debt`. For findings against third-party code, see [redteam-disclosure.md](redteam-disclosure.md).

---

## `validator`

**What triggers it:** The Jiminy Cricket reasoning subagent (thorough+ depth) flagged a logical error, missing edge case, or unsafe assumption in the code Claude just wrote.

**Fields:**
- `file`
- `reasoning` -- the validator's explanation
- `severity` -- `high`, `medium`, or `low`

**Example output:**
```
tailtest: validator · medium src/util.py -- divide() does not guard against zero denominator
```

**How to baseline:** Fix the issue. Validator findings are reasoning-based, not rule-based, so false positives are rare. If you disagree, run `/tailtest:debt` to record the decision.

---

## `recommendation`

**What triggers it:** The `/tailtest` recommendations skill or the PostToolUse hook noticed a setup or config issue worth surfacing (e.g., no tests detected, Semgrep not installed, depth is `off` on a production repo).

**Fields:**
- `message` -- the recommendation text
- `action` -- the suggested command or config change
- `priority` -- `high`, `medium`, or `low`

**Example output:**
```
tailtest: recommendation · No test runner detected. Run /tailtest:setup to configure one.
```

**How to baseline:** Act on the recommendation. Once the underlying issue is resolved, it stops appearing. Recommendations cannot be baselined -- they reflect real project state.
