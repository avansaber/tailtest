# Adding tailtest to an Existing Codebase

## tailtest is reactive, not proactive

Installing tailtest on a project with 50,000 lines of code does not trigger any scanning activity. tailtest does not know about your existing files at all until Claude edits them. The only files it processes are files Claude writes or edits in the current session.

This is intentional. Proactive scanning of an existing codebase would produce noise at best and false positives at worst.

## What happens when you edit an existing file

When Claude edits a file that existed before the session, tailtest marks it as a `legacy-file`. The behavior is different from a new file:

- **If a test file already exists for it:** tailtest runs those existing tests and reports failures. It does not generate new tests or overwrite what is already there.
- **If no test file exists:** tailtest is completely silent. It does not generate tests for legacy files automatically.

This is by design. Generating tests on unfamiliar existing code without context creates noise and often produces tests that describe current behavior rather than correct behavior.

## How git determines "existing" vs "new"

In a git project: if the file is tracked by git (`git ls-files` knows about it), it is `legacy-file`. If it is untracked (just created this session), it is `new-file`.

In a project without a `.git` folder: the first time Claude touches a file in the current session, it is `new-file`. Any subsequent edit to the same file in the same session is `legacy-file`.

## The /tailtest command: generate tests for any file on demand

This is the primary tool for getting coverage on existing files. Run it in Claude like a slash command:

```
/tailtest src/services/billing.py
/tailtest app/Http/Controllers/OrderController.php
/tailtest lib/pricing.ts
```

Pass the path relative to your project root. tailtest treats the file as `new-file` regardless of git status, generates scenarios at your configured depth, writes the test file, runs it, and reports only failures.

After you run `/tailtest` on a file once, the test file exists. Any future edits to that file within the same session find the existing test file and update it rather than regenerating from scratch.

## Progressive coverage strategy

For large existing codebases, the practical approach is not to cover everything at once.

Let tailtest build coverage naturally as Claude touches files in the course of normal development. Each file Claude edits that has an existing test will be exercised. Each new file Claude creates will get fresh scenarios automatically.

For critical paths you want covered immediately -- billing logic, authentication, anything with a bug history -- use `/tailtest` to trigger generation explicitly. Everything else accumulates as development proceeds.

This is generally more useful than a one-time test generation sweep: coverage that grows with active development stays connected to what the code actually does.
