---
description: Generate a starter test file for a source file. Writes a review-before-committing header and runs a compile check before returning. Never commits the generated file automatically.
argument-hint: <path to source file>
---

# /tailtest:gen

When the user invokes this skill with a file path, call the `generate_tests` MCP tool to produce a starter test file.

## Argument handling

`$ARGUMENTS` should be a path to a source file. Examples:

- `/tailtest:gen src/calc.py` (Python)
- `/tailtest:gen src/widget.ts` (TypeScript)
- `/tailtest:gen src/lib/foo.js` (JavaScript)

If `$ARGUMENTS` is empty: ask the user which file they want tests for, then stop. Do not guess.

If the path does not exist: say so and stop. Do not create an empty file.

If the path is already a test file (name starts with `test_`, or ends in `.test.ts`, `.test.js`, `.spec.ts`, `.spec.js`): decline with a note that the generator refuses to write tests for test files.

## How to invoke the generator

Call the `generate_tests` MCP tool with arguments:

```json
{
  "file": "<path from $ARGUMENTS>",
  "scope": "module"
}
```

Do not pass a `style` override unless the user explicitly requested a specific framework.

## What the tool returns and how to present it

The tool returns a JSON payload with a `status` field:

- `status: "ok"`: the generator wrote the file. Present:
  - The generated file path (`test_path` field)
  - The detected language + framework
  - The compile check result (should be ok)
  - A preview of the first 20 lines of the generated file
  - **A clear reminder**: "review the generated test before committing. Tailtest never stages or commits files automatically."
- `status: "skipped"`: the generator decided not to write anything. Show the reason field (target already exists, unsupported language, source is a test file, source does not exist).
- `status: "failed"`: the generator wrote something that did not compile and deleted it. Show the error field so the user knows what broke.

## What not to do

- Do not run `git add` or `git commit`. The generator's contract is "never commits, ever". This skill preserves that contract.
- Do not overwrite an existing test file. The generator already refuses; if the user wants to replace an existing test, they delete it first and re-run.
- Do not generate tests for entire directories at once. The scope is one file per invocation.

## Related skills

- `/tailtest:scan` to see what frameworks the project uses
- `/tailtest:status` for the project summary
