# Configuration

Tailtest works out of the box with sensible defaults. Configuration is optional. When you do want to customize, drop a `.tailtest/config.yaml` file in your project root.

The fastest way to get a working config is the onboarding interview:

```
/tailtest:setup
```

This walks you through 3-4 questions and writes the file for you.

## Schema

This is the full schema as of `v0.1.0-alpha.2`. Every field has a default; an empty config file is valid and produces the defaults shown below.

```yaml
schema_version: 1                  # required, current version is 1
depth: standard                    # off | quick | standard | thorough | paranoid

runners:
  auto_detect: true                # let the scanner pick runners

security:
  secrets: true                    # gitleaks per-file scan
  sast:
    enabled: true                  # Semgrep batch scan
    ruleset: p/default             # any Semgrep ruleset id
  sca:
    enabled: true                  # OSV.dev dependency scan
    use_epss: false                # opt-in EPSS scoring (off until EPSS.io integration ships)
  block_on_verified_secret: false  # opt-in: block edits on verified live secrets

notifications:
  auto_offer_generation: true      # offer /tailtest:gen suggestions in the hot loop
  recommendations: true            # surface recommendation findings (Phase 3)

interview_completed: false         # set to true after /tailtest:setup runs
```

## Field reference

### `schema_version` (int, required)

Currently `1`. Bumped on any incompatible schema change. Phase 2 keeps schema_version at 1 even though it added the nested SAST + SCA shapes, because the legacy plain-bool form (`sast: true`) still parses via a field validator that coerces it.

### `depth` (string, default `standard`)

Controls how much work tailtest does on every Claude edit. See [`/tailtest:depth`](../skills/depth/SKILL.md) for the live switcher.

| Mode | What runs |
|---|---|
| `off` | Hook short-circuits. Nothing runs on edit. |
| `quick` | Impacted tests + gitleaks only. Fastest hot loop. |
| `standard` | Impacted tests + gitleaks + Semgrep + OSV (if manifest changed). The default. |
| `thorough` | Standard + delta coverage + auto-offer test generation. (Phase 3+ enables more checks.) |
| `paranoid` | Thorough + LLM-judge assertions + red-team checks. (Phase 5+/6+.) |

`thorough` and `paranoid` partially work today; their full feature sets ship in later phases. See [`/tailtest:depth`](../skills/depth/SKILL.md) for which checks are live in your installed version.

### `runners.auto_detect` (bool, default `true`)

When `true`, the scanner walks the project tree and picks runners automatically (PythonRunner for pytest projects, JSRunner for vitest/jest projects). When `false`, the scanner still walks the tree but only registers runners explicitly enabled via per-runner config (Phase 2 ships only `auto_detect`; explicit per-runner config lands in Phase 3 via the custom-runner adapter).

### `security.secrets` (bool, default `true`)

When `true`, gitleaks runs on every changed file at every depth except `off`. When `false`, gitleaks is skipped entirely.

Gitleaks must be on PATH. If it isn't, the scanner logs an INFO line and skips silently. The `is_available()` check is a cheap probe; missing gitleaks doesn't break anything.

There's no nested structure for `secrets` because gitleaks has no per-project configuration to expose at the config layer. The rule set is built into the gitleaks binary.

### `security.sast` (nested, default enabled with `p/default`)

```yaml
security:
  sast:
    enabled: true
    ruleset: p/default
```

`enabled: true` runs Semgrep on the changed files at `standard` depth and higher (`quick` skips SAST to keep the hot loop snappy). `enabled: false` skips Semgrep entirely.

`ruleset` is any Semgrep ruleset identifier. Common choices:

- `p/default` — Semgrep's curated low-FP ruleset, covers the top OWASP + CWE patterns. Default.
- `p/owasp-top-ten` — explicit OWASP Top Ten.
- `p/ci` — broader CI-style ruleset.
- `p/python`, `p/javascript`, `p/typescript` — language-specific.
- `path/to/local.yaml` — local Semgrep ruleset file.

You can also pass a comma-separated list (`p/default,p/secrets`) — Semgrep accepts multiple `--config` arguments.

Semgrep must be on PATH. Same `is_available()` graceful fallback as gitleaks.

### `security.sast` (legacy bool form)

Phase 1 and Phase 2 Task 2.5 shipped `sast` as a plain bool. Configs written against those versions still parse:

```yaml
security:
  sast: true   # legacy form, equivalent to {enabled: true, ruleset: p/default}
```

The loader coerces `sast: true` into `{enabled: true}` and inherits the default ruleset. No migration needed.

### `security.sca` (nested, default enabled)

```yaml
security:
  sca:
    enabled: true
    use_epss: false
```

`enabled: true` runs OSV.dev queries on every manifest edit (`pyproject.toml`, `package.json`) at `standard` depth and higher. The hot loop only fires OSV when a manifest is in the changed file list; non-manifest edits skip OSV entirely.

`use_epss` is a Phase 6 opt-in for EPSS-based severity adjustment. EPSS (Exploit Prediction Scoring System) estimates the probability that a vuln will be exploited in the wild within 30 days. Phase 2 keeps it `false` by default because EPSS.io integration hasn't shipped yet; flipping it to `true` is a no-op until then.

`security.sca` also accepts the legacy bool form (`sca: true`).

### `security.block_on_verified_secret` (bool, default `false`)

The one opt-in BLOCK in tailtest. When `true`, an edit that introduces a secret WHICH HAS BEEN VERIFIED ACTIVE against a live API will block the next Claude turn until you remove or rotate the secret.

This stays `false` by default for two reasons:

1. Phase 2 ships gitleaks but not yet TruffleHog-style live verification. Until verification ships, flipping this flag would block on every gitleaks finding (false positive risk).
2. The "never block" promise is the user-facing default. The block flag is the one opt-in exception, deliberately.

When secret verification ships (future phase), this flag becomes meaningful. Until then, leave it `false`.

### `notifications.auto_offer_generation` (bool, default `true`)

When `true`, the PostToolUse hook walks newly-edited Python files for pure functions that have no test, and surfaces a one-line `consider running /tailtest:gen X` suggestion in Claude's next-turn context. Per-session debounce via `.tailtest/session-state.json` so the same `(file, symbol)` pair is offered at most once per session.

When `false`, the auto-offer path is skipped entirely.

### `notifications.recommendations` (bool, default `true`)

When `true`, the PostToolUse hook surfaces Phase 3 recommendation findings in Claude's next-turn context. Phase 2 ships the schema field but no recommendation engine yet; the flag is forward-looking. When the engine lands in Phase 3, this flag is the user-facing opt-out.

### `interview_completed` (bool, default `false`)

Set to `true` by `/tailtest:setup` after the onboarding interview runs. Used as a one-time flag so the setup skill doesn't re-prompt on every invocation.

## Examples

### Minimal config: change depth only

```yaml
schema_version: 1
depth: quick
```

Everything else inherits the defaults. The hot loop runs only impacted tests + gitleaks.

### Custom Semgrep ruleset

```yaml
schema_version: 1
security:
  sast:
    enabled: true
    ruleset: p/owasp-top-ten
```

Semgrep runs on the OWASP Top Ten ruleset instead of `p/default`.

### Disable SCA for an air-gapped environment

```yaml
schema_version: 1
security:
  sca:
    enabled: false
```

OSV is skipped entirely. Useful in air-gapped CI where the OSV API is unreachable. The hot loop still runs gitleaks + Semgrep + the impacted tests.

### Turn the hot loop off temporarily

```yaml
schema_version: 1
depth: off
```

Easier than uninstalling the plugin if you want to disable tailtest for a session. Toggle back to `standard` when you're ready.

## Where the config lives

The config file lives at `<project_root>/.tailtest/config.yaml`. The `.tailtest/` directory is project-local; it's where tailtest writes its baseline, profile, reports, and caches. You can commit `.tailtest/config.yaml` and `.tailtest/baseline.yaml` to git so every contributor sees the same setup. The other files in `.tailtest/` (reports, caches, session-state) are session-local and should be gitignored:

```
# .gitignore
.tailtest/cache/
.tailtest/reports/
.tailtest/profile.json
.tailtest/session-state.json
```

`.tailtest/config.yaml` and `.tailtest/baseline.yaml` should be tracked.

## Validation

Tailtest validates the config on every load. A malformed config produces a clear error message and falls back to defaults rather than crashing. Common validation errors:

- Unknown field: `extra="forbid"` is set on every config model. Typos surface as `pydantic.ValidationError`.
- Invalid `depth`: must be one of the five enum values (`off`, `quick`, `standard`, `thorough`, `paranoid`). YAML parses `off` as boolean `False`, so quote it: `depth: "off"`.
- Wrong type for a nested field: `sast.ruleset` must be a string; `sast.enabled` must be a bool.

## Schema versioning

`schema_version: 1` is required. Future incompatible changes will bump it. Phase 2 keeps it at 1 because the Task 2.9 nested SAST + SCA additions are backward compatible (legacy bool form still parses via field validators).

When schema_version bumps, the loader will auto-migrate older configs in place where safe. Watch the changelog for migration notes.
