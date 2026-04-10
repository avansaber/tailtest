---
description: Show active tailtest recommendations for this project, or dismiss/accept a specific recommendation. Invoke with /tailtest to see all recommendations, /tailtest dismiss <id> to snooze one for 7 days, or /tailtest accept <id> to act on one.
---

# /tailtest

When the user invokes `/tailtest` (with no subcommand or arguments), run the recommendations check below and present the results. When the user invokes `/tailtest dismiss <id>`, run the dismissal flow. When the user invokes `/tailtest accept <id>`, run the acceptance flow. For any other subcommand, delegate to the appropriate `/tailtest:<subcommand>` skill if one exists, or explain that the subcommand is not recognized.

---

## /tailtest (no arguments) -- show active recommendations

Run the following Python snippet from the project root. Use the Bash tool to execute it:

```bash
python3 -c "
import json, sys
sys.path.insert(0, 'src')
try:
    from tailtest.core.scan.scanner import ProjectScanner
    from tailtest.core.recommender.engine import RecommendationEngine
    from tailtest.core.recommendations.store import DismissalStore
except ImportError as e:
    print(f'import-error: {e}')
    sys.exit(1)

scanner = ProjectScanner('.')
profile = scanner.load_profile()
if profile is None:
    print('no-profile')
    sys.exit(0)

engine = RecommendationEngine()
recs = engine.compute(profile)
store = DismissalStore('.')
recs = store.apply(recs)
active = [r for r in recs if not r.is_dismissed]
if not active:
    print('no-recs')
    sys.exit(0)

print(f'count:{len(active)}')
for r in active:
    print(f'---REC---')
    print(f'priority:{r.priority}')
    print(f'title:{r.title}')
    print(f'why:{r.why}')
    print(f'next_step:{r.next_step}')
    print(f'id:{r.id}')
"
```

Then format the output for the user as follows:

- If the output contains `no-profile`: tell the user "No profile found -- run `/tailtest scan .` first to scan your project."
- If the output contains `import-error`: tell the user "tailtest is not installed in the current environment. Make sure you are in the tailtest project root and the src/ directory is on the Python path."
- If the output is `no-recs`: tell the user "tailtest: no recommendations at this time. Run `/tailtest:scan` with `--deep` for a full analysis."
- Otherwise, parse the REC blocks and present them in this format:

```
tailtest recommendations (N active)

[priority] Title
  Why: <why text>
  Next step: <next_step text>
  ID: <id> | /tailtest dismiss <id> to snooze for 7 days
```

Show all active recommendations, high-priority first (they come pre-sorted from the engine). Add a blank line between each recommendation.

After showing the recommendations, add this footer:
"Run `/tailtest dismiss <id>` to snooze a recommendation for 7 days. Run `/tailtest accept <id>` to act on it."

Do NOT dump raw script output. Parse and format it as shown above.

---

## /tailtest dismiss <id> -- snooze a recommendation for 7 days

When the user runs `/tailtest dismiss <id>`, replace `<id>` with the actual ID value and run:

```bash
python3 -c "
import sys
sys.path.insert(0, 'src')
try:
    from tailtest.core.recommendations.store import DismissalStore
    from datetime import datetime, timezone, timedelta
except ImportError as e:
    print(f'import-error: {e}')
    sys.exit(1)

store = DismissalStore('.')
until = datetime.now(tz=timezone.utc) + timedelta(days=7)
store.dismiss('<ID>', until)
print(f'dismissed-until:{until.strftime(\"%Y-%m-%d\")}')
"
```

replacing `<ID>` with the actual recommendation ID the user provided.

Then confirm to the user: "Recommendation `<id>` dismissed for 7 days. It will resurface after `<date>`." Use the date from `dismissed-until:` in the output.

If output contains `import-error`, explain that tailtest is not installed in this environment.

---

## /tailtest accept <id> -- act on a recommendation

When the user runs `/tailtest accept <id>`, first show the current recommendations (same snippet as above, but filtered to the specific ID) to find the recommendation's `kind`. Then take the appropriate action based on `kind`:

- **`install_tool`**: Do NOT run any install command. Instead, read the `next_step` text from the recommendation and present it to the user as: "To accept this recommendation, run the following in your terminal:\n\n`<next_step command>`\n\ntailtest never installs tools on your behalf -- you control what gets installed."

- **`add_test`**: Parse the `next_step` text to find the file path. Invoke `/tailtest:gen <file>` to generate a starter test file. Tell the user what was generated.

- **`enable_depth`** or **`enable_ai_checks`**: Read `.tailtest/config.yaml`, apply the relevant change (e.g., set `depth: thorough` or `ai_checks_enabled: true`), and write the file back. Confirm to the user: "Config updated. The change takes effect on the next file edit."

- **`configure_runner`**: Read the `next_step` text, explain what configuration change will be made to `.tailtest/config.yaml`, ask the user to confirm, then apply it.

For any `kind`, after completing the action, dismiss the recommendation using the same dismiss flow as above (it has been acted on, no need to resurface it).

---

## /tailtest accept-ai-checks -- enable AI-specific checks

When the user runs `/tailtest accept-ai-checks`, run:

```bash
python3 -c "
import sys
sys.path.insert(0, 'src')
from tailtest.core.config.loader import ConfigLoader
from pathlib import Path
loader = ConfigLoader(Path('.') / '.tailtest')
config = loader.load()
config.ai_checks_enabled = True
loader.save(config)
print('AI checks enabled. They will run when scan_mode is thorough or above.')
"
```

Then confirm to the user: "AI-specific checks enabled. Run \`/tailtest config set scan_mode thorough\` to activate them."

---

## /tailtest dismiss-ai-checks -- skip AI-specific checks

When the user runs `/tailtest dismiss-ai-checks`, run:

```bash
python3 -c "
import sys
sys.path.insert(0, 'src')
from tailtest.core.config.loader import ConfigLoader
from pathlib import Path
loader = ConfigLoader(Path('.') / '.tailtest')
config = loader.load()
config.ai_checks_enabled = False
loader.save(config)
print('AI checks dismissed.')
"
```

Then confirm to the user: "AI checks dismissed. tailtest will not ask again. You can re-enable later with \`/tailtest accept-ai-checks\`."

---

## What not to do

- Do not run `pip install`, `npm install`, or any package-manager install command on the user's behalf. The `install_tool` flow always gives the user a command to copy; tailtest never silently installs tools.
- Do not modify `.tailtest/profile.json` directly.
- Do not invoke the MCP `scan_project` tool as a replacement for the Python snippet -- use the snippet shown above.
- Do not truncate or paraphrase recommendation text. Show it in full.

---

## Related skills

- `/tailtest:scan` -- re-scan the project and refresh recommendations
- `/tailtest:status` -- compact one-line project status
- `/tailtest:debt` -- review baselined (accepted) findings
- `/tailtest:security` -- security scanner posture
