# The /t Command

`/t` is the only explicit user-facing command tailtest adds to Claude. It triggers test generation for any file you specify, bypassing the normal new-file/legacy-file distinction.

## When to use it

tailtest automatically processes files Claude writes in the current session. `/t` is for everything else:

- A file that existed before this session and you want covered right now
- A file tailtest would normally skip (legacy file with no test)
- Any file you want explicitly verified without waiting for the next edit

## How to use it

```
/t src/services/billing.py
/t app/Http/Controllers/OrderController.php
/t lib/pricing.ts
/t internal/handler.go
```

Pass the path relative to your project root. tailtest generates scenarios at your configured depth, writes the test file in the correct location for the language, runs it, and reports only failures.

You can also use natural language variants: "tailtest billing.py" or "run tailtest on billing.py". The `/t` shorthand is the canonical form.

## What "treat as new-file" means

The command overrides the legacy-file distinction. Even if `billing.py` has existed for years and has hundreds of lines, `/t billing.py` generates fresh scenarios as if it were just written.

This is intentional. When you invoke `/t`, you are explicitly requesting coverage. tailtest takes that as a signal to generate scenarios rather than stay silent.

## What happens if a test file already exists

If a test file already exists for the source file, `/t` reads it first, then adds new scenarios or updates existing ones to reflect what changed. It does not replace the existing test file.

## After running /t

Once a test file exists, subsequent edits to the source file within the same session find and update it automatically. `/t` is a one-time bootstrap for a file; after that, tailtest's normal tracking takes over.
