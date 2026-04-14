# Get Running in Two Minutes

## Install

```
claude plugin add avansaber/tailtest
```

Restart Claude Code after install. That's everything -- no config files, no environment variables, no framework setup.

## What happens next

From this point, tailtest is invisible until something breaks.

When Claude writes or edits a source file, tailtest automatically queues it. At your next message, Claude reads the queue, generates scenarios that describe real behavior for what was just built, executes them using your existing test runner, and says nothing if everything passes.

**Pass = silence.** You will not see a green checkmark or a progress bar. The absence of output is the signal.

**Fail = one line.** When something breaks, you see a single finding and a question: "want me to fix this?" If you say yes, Claude fixes it and runs the tests again. If you say no, the failure is noted and not resurfaced unless you edit the file again.

## Your first session: what to watch for

Here is what a real session looks like.

You ask Claude to build a billing service. Claude writes `billing.py`. tailtest queues the file without any output. You send your next message. Before responding to you, Claude sees:

```
tailtest: new file queued -- billing.py (python)
Run scenarios at standard depth.
```

Claude generates scenarios: "Create invoice at $800 against a $1,000 credit limit -- verify it succeeds. Create invoice at $1,200 -- verify it is rejected." Runs them. The credit-limit check has a bug.

You see:
```
Cumulative credit limit check is failing -- want me to fix this?
```

One line. One question. Nothing else.

If everything passed, you would have seen nothing at all.

## Three things tailtest does not do

It does not scan files you are not currently editing. Installing tailtest on an existing project with 50,000 lines of code does not trigger any activity on those files.

It does not produce coverage reports, percentages, or dashboards. There is no output to inspect after a session ends.

It does not run without Claude. tailtest is a Claude Code plugin. It requires an active Claude Code session to do anything.

## Next steps

- [Configuration](configuration.md) -- change scenario depth, silence specific paths
- [Supported languages](languages.md) -- runner detection, framework variants, test file locations
- [Existing projects](existing-projects.md) -- how to add coverage to a codebase that already exists
