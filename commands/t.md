Generate or update tests for $ARGUMENTS.

Read the source file at `$ARGUMENTS`. Generate production-like test scenarios covering its public surface -- happy path, key edge cases, and failure modes at the configured depth. Write or update the test file following the tailtest Step 4 rules (correct location, correct name, style-matched to existing tests). Run the tests and report only failures; silence if all pass.

Treat the file as new-file regardless of its git status -- this command explicitly requests generation even for legacy files or files tailtest would normally skip.
