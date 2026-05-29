# Contributing to tailtest

Thanks for contributing to tailtest. Here is how to get a change in.

## Code of conduct

Participation in this project is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).

## Filing issues

Use the [issue tracker](https://github.com/avansaber/tailtest/issues). When filing a bug, please include:

- tailtest version (`claude plugin list` or the version badge in `README.md`)
- Claude Code version
- Operating system and version (macOS or Linux)
- Repro steps, expected behavior, and actual behavior
- Relevant excerpts from `.tailtest/reports/latest.json` if applicable

For feature requests, describe the workflow you want and the gap in the current behavior.

## Submitting changes

1. Fork the repo and create a topic branch off `main`.
2. Make your change. Keep commits focused and the diff small where possible.
3. Run the test suite (see below) and confirm it is green.
4. Open a pull request against `main` with a clear description of what changed and why.

Contributor email is not required, and the project does not collect Co-Authored-By signing data.

## Running tests

The plugin is a Python project using pytest.

```bash
pytest -q
```

To run a single test file or test:

```bash
pytest tests/test_post_tool_use.py -q
pytest tests/test_post_tool_use.py::test_specific_case -q
```

## Adding a new R rule

The R1-R15 rule layer is documented in `CLAUDE.md` at the repo root. New rules should extend that document with the same shape used by existing rules (intent, inputs, output shape, examples). Code that drives the rule layer lives under `hooks/lib/`.

## Adding a new language baseline or framework template

Language and framework detection drives R2 and R3 behavior. The runner and scanner logic lives in `hooks/lib/runners.py` and `hooks/lib/scanner.py`. Add detection for the new runner there, then add tests under `tests/` that mirror the structure of existing runner tests.

## Release process

Releases are tagged from `main`. Update `CHANGELOG.md` in the same PR as the user-visible change, following the existing per-version section shape. Maintainers handle the GitHub Release upload after a tag lands.

## License of contributions

By submitting changes you agree they are MIT-licensed under the project [LICENSE](LICENSE).
