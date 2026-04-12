---
description: Generate a starter test file for a source file. Uses project scan context and AST analysis to produce domain-aware tests. Writes a review-before-committing header and runs a compile check before returning. Never commits the generated file automatically.
argument-hint: <path to source file> [--context "description"]
---

# /tailtest:gen

When the user invokes this skill with a file path, call the `generate_tests` MCP tool to produce a starter test file.

## Argument handling

`$ARGUMENTS` should be a path to a source file, optionally followed by `--context "description"`. Examples:

- `/tailtest:gen src/calc.py` (Python)
- `/tailtest:gen src/widget.ts` (TypeScript)
- `/tailtest:gen src/lib/foo.js` (JavaScript)
- `/tailtest:gen src/billing.py --context "supplier invoice approval for EU entities"`

Parse `--context "..."` from `$ARGUMENTS` if present: extract the quoted string and pass it as the `context` field. The context must be 300 characters or fewer.

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

If the user provided `--context "..."`, include it:

```json
{
  "file": "<path from $ARGUMENTS>",
  "scope": "module",
  "context": "<text from --context flag>"
}
```

Do not pass a `style` override unless the user explicitly requested a specific framework.

## What the tool returns and how to present it

The tool returns a JSON payload with a `status` field:

- `status: "ok"`: the generator wrote the file. Present:
  - The generated file path (`test_path` field)
  - The detected language + framework
  - The compile check result (should be ok)
  - A preview of the first 20 lines of the generated file -- point out line 2 (the detection note) so the user knows what domain tailtest inferred
  - **A clear reminder**: "review the generated test before committing. Tailtest never stages or commits files automatically."
- `status: "skipped"`: the generator decided not to write anything. Show the reason field (target already exists, unsupported language, source is a test file, source does not exist).
- `status: "failed"`: the generator wrote something that did not compile and deleted it. Show the error field so the user knows what broke.

## The detection note (line 2)

Every generated file has a detection note on line 2 that tells the user what domain tailtest inferred:

- `# tailtest detected: InvoiceStatus, CreditLimitExceeded -- review before committing` -- AST signals found
- `# tailtest context: Billing API for multi-tenant SaaS.` -- from project scan summary
- `# tailtest used your description: ...` -- from `--context` flag
- `# tailtest: no domain context available -- review generated tests carefully` -- fallback

If the detection note looks wrong, the user can re-run with `--context` to override it. If you ran `/tailtest scan --deep` first, the generator also uses the project summary automatically.

## What not to do

- Do not run `git add` or `git commit`. The generator's contract is "never commits, ever". This skill preserves that contract.
- Do not overwrite an existing test file. The generator already refuses; if the user wants to replace an existing test, they delete it first and re-run.
- Do not generate tests for entire directories at once. The scope is one file per invocation.

## Related skills

- `/tailtest:scan` to see what frameworks the project uses and run a deep scan for richer context
- `/tailtest:status` for the project summary
