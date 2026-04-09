---
description: Change tailtest's depth mode for the current project. Valid values are off, quick, standard, thorough, paranoid. Soft-warns when selecting modes whose full feature set ships in a later release.
argument-hint: off | quick | standard | thorough | paranoid
---

# /tailtest:depth

When the user invokes this skill with an argument like `/tailtest:depth quick`, change the depth mode in `.tailtest/config.yaml` for the current project.

## Argument handling

`$ARGUMENTS` should be one of: `off`, `quick`, `standard`, `thorough`, `paranoid`.

- If `$ARGUMENTS` is empty: present the 5 valid modes with a one-line description of each, then ask the user which one they want.
- If `$ARGUMENTS` is not in the valid set: tell the user the valid modes and stop. Do not guess.

## What to do for each mode

1. Read the current `.tailtest/config.yaml` if it exists. If not, create a new default config and then set the depth field.
2. Update the `depth` field to the requested value.
3. Write the file back using the same YAML shape (preserve other fields untouched).
4. Tell the user what changed and what the new mode does.

## Soft-warn for unshipped modes

Some depth modes have features that ship in later releases. When the user selects one of these, set the depth anyway (the config value is valid) but warn them that the deeper features will not fire yet:

- `thorough`: the LLM-judge assertion pipeline ships in the Phase 3 opportunity-detection release
- `paranoid`: the validator subagent ships in the Phase 5 validator release, and the red-team attack catalog ships in the Phase 6 red-team merge release

A `thorough` depth setting today produces the same runtime behavior as `standard` with the warning: "thorough depth is accepted; the LLM-judge features it unlocks ship in a later release."

## What not to do

- Do not run tests. Depth mode is a config change, not a run trigger.
- Do not touch any file other than `.tailtest/config.yaml`.
- Do not offer to "also re-run the last test suite" automatically. If the user wants that, they invoke `/tailtest:status` or `tailtest run` themselves.

## Related skills

- `/tailtest:status` to see the current depth mode
- `/tailtest:setup` to set depth mode as part of the onboarding interview
