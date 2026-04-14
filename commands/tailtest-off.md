Pause tailtest for this session.

Set `paused: true` in `.tailtest/session.json`. Respond exactly: "tailtest paused. Type /tailtest on to resume."

The PostToolUse hook reads this flag and exits without running tests while paused. No other behaviour changes.
