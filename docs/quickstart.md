# Get Running in Two Minutes

## Install

```
claude plugin marketplace add avansaber/tailtest
claude plugin install tailtest@avansaber-tailtest
```

Restart Claude Code after install. That's everything -- no config files, no environment variables, no framework setup.

## What happens next

From this point, tailtest is invisible until something breaks.

When Claude writes or edits a source file, tailtest automatically queues it. At your next message, Claude reads the queue, generates scenarios that describe real behavior for what was just built, executes them using your existing test runner, and emits a brief confirmation if everything passes.

**Pass = one line.** You see `tailtest: N scenarios -- all passed.` No dashboard, no progress bar. One quiet line, then you keep building.

**Fail = one line.** When something breaks, you see a specific finding and a question: "want me to fix this?" If you say yes, Claude fixes it and runs the tests again. If you say no, the failure is noted and not resurfaced unless you edit the file again.

## Your first session: what to watch for

Here is what a real session looks like.

You ask Claude to build a billing service. Claude writes `billing.py`. tailtest runs in the background. You send your next message. Before Claude responds to you, it runs the tests.

The credit-limit check has a bug. You see:

```
Cumulative credit limit check is failing -- want me to fix this?
```

One line. One question. Nothing else.

If everything passed, you would have seen: `tailtest: 8 scenarios -- all passed.`

At the end of a session, type `/summary` to see everything tailtest did -- which files were tested, which passed, which needed fixing, and which failures you deferred.

## Three things tailtest does not do

It does not scan files you are not currently editing. Installing tailtest on an existing project with 50,000 lines of code does not trigger any activity on those files.

It does not produce coverage percentages or dashboards. At the end of each session, tailtest writes a plain markdown report to `.tailtest/reports/` summarising what was tested. You can also generate it mid-session with `/summary`.

It does not run without Claude. tailtest is a Claude Code plugin. It requires an active Claude Code session to do anything.

## Next steps

- [Commands](slash-command.md) -- `/tailtest <file>` to test any file on demand, `/summary` to see what tailtest did, `/tailtest off` to pause
- [Configuration](configuration.md) -- change scenario depth, silence specific paths
- [Supported languages](languages.md) -- runner detection, framework variants, test file locations
- [Existing projects](existing-projects.md) -- how to add coverage to a codebase that already exists
