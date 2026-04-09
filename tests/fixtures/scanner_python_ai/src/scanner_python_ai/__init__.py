"""Fake AI agent used as a scanner fixture.

The scanner should detect:
- language: python (primary)
- frameworks: anthropic-sdk (category=agent), fastapi (category=web), pydantic (not in signatures, skipped)
- runners: pytest (via pyproject.toml pytest config)
- infrastructure: docker
- plan_files: CLAUDE.md, AGENTS.md, README.md
- ai_surface: agent (anthropic SDK + system prompt below + imports)
- likely_vibe_coded: True
"""

from anthropic import Anthropic

SYSTEM_PROMPT = """You are a helpful research assistant. Your job is to search arxiv
for relevant papers and summarize them. You have access to a web search tool.
Use tools when you need current information."""


def run(query: str) -> str:
    client = Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": query}],
    )
    return response.content[0].text
