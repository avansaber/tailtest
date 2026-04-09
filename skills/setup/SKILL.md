---
description: Run the tailtest opt-in onboarding interview. Scans the project first, forms a hypothesis, asks 3 to 5 conversational questions, then writes the final config to .tailtest/config.yaml.
---

# /tailtest:setup

When the user invokes this skill, run a short conversational interview to configure tailtest for the current project. This is the ONLY place where tailtest asks the user questions unprompted. The rest of the tool ships with a zero-interview default.

## Flow

1. **Scan first, ask second.** Invoke the `scan_project` MCP tool with `deep: false` and use the result to form a hypothesis about what the user is building. This lets the interview skip questions the repo already answers.

2. **Present the hypothesis back.** In one short paragraph, tell the user what tailtest sees: primary language, detected frameworks, AI surface classification, vibe-coded signals. Ask the user to confirm or correct.

3. **Ask 3 to 5 questions max.** Do not form-fill. Questions should feel like a conversation, and the user should be able to answer each in one line.

   Candidate questions (pick the ones the repo did not answer):

   - What are you building? (web app, AI agent, CLI tool, library, research script, other)
   - What matters most: speed or thoroughness? (This maps to depth mode: quick vs standard vs thorough.)
   - Any tests you want tailtest to skip or prioritize?
   - How do you want to hear about security issues: inline in Claude's next turn, or batched in a report?
   - Is this a project you plan to ship, or a throwaway? (Throwaway defaults to quick depth, shippable to standard.)

   Skip any question whose answer is obvious from the scan.

4. **Write the config.** After the questions, write `.tailtest/config.yaml` with the final values. Preserve any fields the user set before the interview.

5. **Explain what tailtest will do.** One short paragraph: "at `standard` depth, tailtest will run impacted tests on every edit and surface test failures, coverage gaps, and security findings in Claude's next turn. You can change this anytime with `/tailtest:depth`."

## What not to do

- Do not run the interview unprompted. This skill is user-invoked only.
- Do not generate tests as part of the interview. That is `/tailtest:gen`.
- Do not run the full scan at deep mode. Shallow is enough for the interview.
- Do not write any file other than `.tailtest/config.yaml`.
- Do not offer a security scan as part of setup. The security layer ships in a later release.

## Related skills

- `/tailtest:status` to verify the config was written correctly
- `/tailtest:depth` to change depth mode later
- `/tailtest:scan` to re-scan the project profile
