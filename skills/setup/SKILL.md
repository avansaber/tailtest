---
description: "Run the tailtest onboarding interview. Reads the existing scan profile first, presents findings back to the user for confirmation, then asks only the 3 questions the scan could not answer. Writes interview_completed: true to .tailtest/config.yaml on completion."
---

# /tailtest:setup

When the user invokes this skill, run a short conversational interview to configure tailtest for the current project. This is the ONLY place where tailtest asks the user questions unprompted. The rest of the tool ships with a zero-interview default.

---

## Step 1 -- Read the existing scan profile

Before asking any questions, run the following Python snippet from the project root using the Bash tool:

```bash
python3 -c "
import sys, json
sys.path.insert(0, 'src')
try:
    from tailtest.core.scan.scanner import ProjectScanner
    s = ProjectScanner('.')
    p = s.load_profile()
    if p:
        print(json.dumps({
            'language': p.primary_language,
            'frameworks': list(f.name for f in (p.frameworks_detected or [])),
            'ai_surface': str(p.ai_surface),
            'likely_vibe_coded': p.likely_vibe_coded,
            'test_runner': [r.name for r in (p.runners_detected or [])],
        }, indent=2))
    else:
        print('{}')
except Exception as e:
    print('{}')
"
```

Capture the JSON output. If the output is `{}` (empty dict or parse failure), skip to Step 3 (full interview -- no profile available).

---

## Step 2 -- Present findings back to the user

Using the JSON from Step 1, present the scan findings as a confirmation. Use this format:

```
I see this is a [language] project using [frameworks]. [One-sentence description based on ai_surface and test_runner].
Does that sound right? (yes / no / corrections)
```

Fill in the brackets:
- `[language]`: use the `language` value from the JSON, or "unknown language" if null.
- `[frameworks]`: list the detected frameworks separated by commas, or "no detected frameworks" if the list is empty.
- One-sentence description: use `ai_surface` to characterize the project (e.g., "It looks like an agent project with LLM tool use." for `agent`, "It uses an LLM API for specific tasks." for `utility`, "No AI surface detected." for `none`). If `test_runner` is non-empty, add: "Detected test runner: [runner names]."

If `likely_vibe_coded` is `true`, append this sentence on its own line:
"This looks like a project built with AI assistance -- tailtest will be especially useful here."

Wait for the user to respond. If they say corrections, incorporate them into the rest of the interview. Then continue to Step 3.

---

## Step 3 -- Ask only the questions the scan could not answer

Ask at most 3 questions, in order. Skip any question if the scan already answered it (see the skip conditions below).

**Question 1 -- test runner** (skip if `test_runner` in the scan JSON is non-empty):
> "What test runner do you use? (e.g. pytest, jest, vitest, go test, cargo test)"

**Question 2 -- main entry point** (always ask -- the scan cannot know intent):
> "What is the main entry point or most important file? (e.g. src/main.py, app/page.tsx, cmd/server/main.go)"

**Question 3 -- ignore patterns** (always ask -- the scan cannot know intent):
> "Any files or directories tailtest should ignore? (e.g. vendor/, generated/, scripts/seed.py -- or press Enter to skip)"

Ask each question one at a time and wait for the answer before asking the next.

---

## Step 4 -- Write the config

After collecting answers, run the following Python snippet from the project root using the Bash tool. Substitute the placeholders with the actual values collected:

```bash
python3 -c "
import sys
sys.path.insert(0, 'src')
from tailtest.core.config.loader import ConfigLoader
from pathlib import Path

loader = ConfigLoader(Path('.') / '.tailtest')
config = loader.load()
config.interview_completed = True
loader.save(config)
print('config-written')
"
```

If the output contains `config-written`, the write succeeded.

If the user provided a test runner answer in Step 3 and the scan did not detect one, note it to the user as something they can add manually to `.tailtest/config.yaml` under `runners`. (The Config schema does not yet have a free-form runner name field; flag it as a known limitation.)

If the user provided ignore patterns, note them to the user for now as paths they can exclude from coverage analysis. (The Config schema does not yet have an ignore-patterns field; flag it as a known limitation and confirm tailtest will still function without it.)

---

## Step 5 -- Confirm to the user

Tell the user what was written and what tailtest will do going forward. Use this format:

```
tailtest is set up. Here is what was recorded:

- Interview completed: yes
- Depth mode: standard (change anytime with /tailtest:depth)
- Security scanners: secrets, SAST, and SCA enabled by default

On the next file edit, tailtest will run impacted tests, surface test
failures and coverage gaps, and show security findings in Claude's next
turn. Run /tailtest to see active recommendations.
```

If the user is on an AI agent project (`ai_surface` was `agent` or `utility`), add:
"For AI agent projects, run `/tailtest accept-ai-checks` to enable LLM-judge assertions."

---

## What not to do

- Do not run the interview unprompted. This skill is user-invoked only.
- Do not generate tests as part of the interview. That is `/tailtest:gen`.
- Do not run the full scan at deep mode. Reading the cached profile is enough.
- Do not write any file other than `.tailtest/config.yaml`.
- Do not force the user to configure security scanners during setup. The defaults enable gitleaks, Semgrep, and OSV with sensible values; the user can inspect the posture via `/tailtest:security` and adjust via the config file.
- Do not dump raw script output. Parse it and respond conversationally.
- Do not use the MCP `scan_project` tool as a replacement for reading the cached profile -- use the Python snippet in Step 1.

---

## Related skills

- `/tailtest:status` to verify the config was written correctly
- `/tailtest:depth` to change depth mode later
- `/tailtest:scan` to re-scan the project profile
- `/tailtest:security` to review the current security scanner posture
- `/tailtest:debt` to review baselined findings after the first run
