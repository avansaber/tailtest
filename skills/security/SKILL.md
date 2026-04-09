---
description: Show the current security posture for this project. Reports which scanners are enabled, how many new and baselined security findings exist, and which ruleset/depth the hot loop is using. Read-only view over .tailtest/config.yaml, .tailtest/baseline.yaml, and .tailtest/reports/latest.json.
---

# /tailtest:security

When the user invokes this skill, produce a status snapshot of the tailtest security layer. The skill is read-only: it synthesizes information from three files and never modifies configuration, baselines, or reports.

## What to read

Read these files from the current project root, in order:

1. `.tailtest/config.yaml` — project configuration. Relevant sections:
   - `depth` (off | quick | standard | thorough | paranoid)
   - `security.secrets` (bool)
   - `security.sast` (bool)
   - `security.sca` (bool)
   - `security.block_on_verified_secret` (bool)

2. `.tailtest/baseline.yaml` — accepted debt ledger. Count entries by kind. Relevant kinds for this skill: secret, sast, sca.

3. `.tailtest/reports/latest.json` — the most recent run. Use:
   - `findings` list filtered by `kind in {secret, sast, sca}` and `in_baseline == False` for the "new" count
   - `summary_line` for the banner
   - `run_id` for the report pointer

Missing files are fine. Use sensible defaults:
- Missing config.yaml → report "using defaults: depth standard, all scanners enabled"
- Missing baseline.yaml → report "no baselined security findings"
- Missing latest.json → report "no recent run"

## What to present

A 4-part view:

### Part 1: Scanner posture

One line per scanner:
```
Scanner posture (depth: standard):
- Secrets (gitleaks):  enabled
- SAST (Semgrep):      enabled (ruleset: p/default)
- SCA (OSV):           enabled
- Block on verified secret: no
```

The ruleset line for SAST is only shown when `security.sast.ruleset` is present in the config (Phase 2 Task 2.9 will add that field; until then omit it). The "Block on verified secret" line echoes `security.block_on_verified_secret`.

If depth is `quick`, add a note: "note: quick depth runs gitleaks only; SAST and SCA are skipped in the hot loop until depth is standard or higher."

### Part 2: Current findings

Two numbers:
```
Findings in last run:
- New (not in baseline): <N>
- Suppressed by baseline: <M>
```

"New" is the count of findings in `latest.json` with `kind in {secret, sast, sca}` and `in_baseline == False`. "Suppressed" is the count of entries in `baseline.yaml` with kind in that same set.

### Part 3: Breakdown by kind

For each of the three kinds (secret, sast, sca), show `new: N, baselined: M` when either number is non-zero. Skip kinds with zero both. Example:
```
Breakdown:
- secret: 2 new, 1 baselined
- sast: 0 new, 5 baselined
- sca: 1 new, 0 baselined
```

### Part 4: Follow-ups

End with 2-3 actionable lines based on the state:
- **New findings present** → "Run `/tailtest:report` to see the full details, or open `.tailtest/reports/latest.html` for a richer view."
- **Only baselined findings** → "No new security issues. Run `/tailtest:debt` to review what is being suppressed."
- **No findings at all** → "Security layer is clean. Security runs on every edit at standard+ depth; tailtest will flag new findings as they appear."
- **Scanners disabled** → if any of `security.secrets/sast/sca` is False, note it and suggest re-enabling: "Secrets scanning is disabled in `.tailtest/config.yaml`. Re-enable with `security.secrets: true` to surface new leaks."
- **depth: off** → "The hot loop is OFF (`depth: off` in config). Security scanners do not run on edits. Set depth to `quick` or `standard` to re-enable."

## What not to do

- Do not modify config, baseline, or report files.
- Do not re-run the scanners.
- Do not dump raw YAML or JSON.
- Do not call MCP tools.
- Do not suggest setting `block_on_verified_secret: true` unless the user asks about blocking behavior explicitly; it is off by default for a reason (Phase 2 does not yet verify secrets against live APIs).

## Related skills

- `/tailtest:report` — full detail of the last run, including tests and delta coverage
- `/tailtest:debt` — review all baselined findings, not just security
- `/tailtest:scan` — re-scan the project from scratch (full depth, not just the hot loop)
- `/tailtest:status` — compact project-wide status (tests + security + coverage in one line)
