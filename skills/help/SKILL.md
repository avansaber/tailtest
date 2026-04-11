---
description: Show all tailtest skills and what they do. Use this when you are not sure which skill to run.
---

# /tailtest:help

When the user invokes `/tailtest:help`, print the following reference block exactly as formatted below, then ask if they want to run any of the listed skills.

---

## What to output

```
tailtest skills

  /tailtest            Show active recommendations for this project.
                       Dismiss or accept individual recommendations.

  /tailtest:status     One-line project status: depth, last run, test
                       count, open findings.

  /tailtest:scan       Re-scan the project and refresh the profile
                       (language, framework, AI surface, depth advice).

  /tailtest:gen        Generate a starter test file for a source file.
                       Never commits automatically.

  /tailtest:report     Open the latest HTML report in your browser.
                       Falls back to a text summary if no browser.

  /tailtest:depth      Change the depth mode.
                       Values: off | quick | standard | thorough | paranoid

  /tailtest:setup      Run the onboarding interview. Asks 3-5 questions,
                       writes .tailtest/config.yaml.

  /tailtest:debt       Review baselined (silenced) findings. Re-open or
                       clean up stale entries.

  /tailtest:security   Security scanner posture: which scanners are on,
                       how many open vs baselined findings.

  /tailtest:memory     View or clear the validator memory file
                       (.tailtest/memory/validator.md).

  /tailtest:help       This screen.

Install docs:   https://github.com/avansaber/tailtest/blob/main/docs/install.md
Quickstart:     https://github.com/avansaber/tailtest/blob/main/docs/quickstart.md
Configuration:  https://github.com/avansaber/tailtest/blob/main/docs/configuration.md
```

After printing this, add one line:

"Run any skill by typing its name, or ask me what a specific one does."

Do not add any other commentary. Do not summarize or paraphrase the skill list.
