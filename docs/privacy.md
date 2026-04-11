# Privacy

tailtest collects no telemetry, no analytics, no crash reports, and no usage data.
No data about you, your code, or your project is ever sent to Anthropic or any third
party by tailtest itself.

## Outbound network calls

tailtest makes exactly three categories of outbound network calls. All are either
user-initiated or strictly limited to package metadata.

### 1. `claude -p` -- LLM judge (thorough and paranoid depth only)

- **Destination:** Anthropic's API, via the `claude` CLI already installed by the user.
- **When:** Only when depth is set to `thorough` or `paranoid` and the user's session
  has triggered a hook.
- **What is sent:** The content passed to `claude -p` -- test output, code snippets,
  or red-team probe results, exactly as you would see in your terminal.
- **Whose account:** The call is made under the user's own Anthropic API key and account.
  tailtest has no Anthropic account and receives no copy of the response.
- **Control:** Users who do not want any LLM calls should run at `standard` depth or
  lower. Set `depth: standard` in `.tailtest/config.yaml`.

### 2. OSV API -- dependency vulnerability advisories (SCA)

- **Destination:** `https://api.osv.dev/v1/querybatch` (Google's open OSV service).
- **When:** When tailtest detects a change to a dependency manifest
  (`requirements.txt`, `pyproject.toml`, `Cargo.toml`, `package.json`, etc.).
- **What is sent:** Package names and version strings only. No source code, no file
  paths, no project metadata.
- **Caching:** Results are cached locally in `.tailtest/cache/osv/` for one hour,
  so repeat runs over the same dependency set do not re-query the API.
- **IP address:** Like any HTTP request, your IP address is visible to OSV's servers.
  OSV is a free public service; see Google's privacy policy for their data practices.

### 3. gitleaks and Semgrep -- local only

Both tools run as local subprocesses. No data leaves the machine.

- gitleaks scans git history and staged files for secrets.
- Semgrep runs its bundled ruleset against local source files.
- Neither tool phones home during a tailtest run (Semgrep's optional login/metrics
  are not invoked; tailtest calls the CLI with no network-dependent flags).

## Localhost dashboard

The optional `tailtest dashboard` command starts an HTTP server bound to
`127.0.0.1` only. It refuses connections from any other host. No data leaves
the machine.

## Summary table

| Call | Destination | Data sent | User-controlled |
|------|-------------|-----------|-----------------|
| LLM judge | Anthropic (via user's key) | Test/code context | Yes -- disable via depth |
| OSV advisory lookup | api.osv.dev | Package name + version | Yes -- disable SCA in config |
| gitleaks / Semgrep | localhost only | None | -- |
