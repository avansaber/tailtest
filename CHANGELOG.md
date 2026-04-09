# Changelog

All notable changes to tailtest will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0-alpha.0] — 2026-04-09

### Added
- Initial repository scaffolding (Phase 0 of the v0.1.0 rebuild)
- `pyproject.toml` with minimal dependencies (click, pydantic, httpx, pyyaml) and dev dependencies (pytest, pytest-asyncio, ruff, pyright)
- Claude Code plugin manifest at `.claude-plugin/plugin.json`
- MCP server wiring at `.mcp.json`
- Empty hook scaffolds at `hooks/` (PostToolUse / SessionStart / Stop)
- Empty skill scaffold at `skills/tailtest/SKILL.md`
- Empty MCP server scaffold at `src/tailtest/mcp/server.py`
- LLM transport layer copied from the v1 project (`llm/resolver.py`, `llm/claude_cli.py`) — Claude CLI subprocess wrapper + multi-provider resolver
- Red-team attack catalog placeholder at `data/redteam/` (full extraction deferred to Phase 6)
- Apache 2.0 license
- GitHub Actions CI skeleton (`.github/workflows/ci.yml`) running ruff, pyright, pytest on Python 3.11 and 3.12

### Known limitations
- **Nothing in this release is functional.** Phase 0 is infrastructure-only. The real hot loop lands in Phase 1 (`0.1.0-alpha.1`).
- Hooks are pass-through stubs that do nothing.
- MCP server responds to `initialize` and `tools/list` but has no actual tools.
- The `/tailtest` skill is a placeholder.
- Test generation, project scanning, security scanning, and the dashboard are not implemented.

[Unreleased]: https://github.com/avansaber/tailtest/compare/v0.1.0-alpha.0...HEAD
[0.1.0-alpha.0]: https://github.com/avansaber/tailtest/releases/tag/v0.1.0-alpha.0
