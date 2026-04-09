"""tailtest CLI — Click-based entry point.

Phase 1: ``version``, ``mcp-serve``, ``run``, ``doctor``, ``scan``.
Phase 2+ adds more (``gen``, ``dashboard``).

Invoked via ``tailtest`` on the command line (configured in pyproject.toml
[project.scripts]) or ``python -m tailtest``.
"""

from __future__ import annotations

import click

from tailtest import __version__
from tailtest.cli.doctor import doctor_cmd
from tailtest.cli.run import run_cmd
from tailtest.cli.scan import scan_cmd


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(version=__version__, prog_name="tailtest")
def main() -> None:
    """tailtest — the test + security validator for Claude Code.

    Run `tailtest doctor` first to verify your setup, then `tailtest scan`
    to see what tailtest detects in your project, then `tailtest run` to
    execute tests.
    """


@main.command("version")
def version_cmd() -> None:
    """Print the tailtest version."""
    click.echo(f"tailtest {__version__}")


@main.command("mcp-serve")
def mcp_serve_cmd() -> None:
    """Start the tailtest MCP server on stdio.

    Reads JSON-RPC 2.0 requests from stdin, writes responses to stdout.
    All diagnostic logging goes to stderr so the protocol channel stays clean.
    Phase 1 ships 6 tools: scan_project, impacted_tests, run_tests,
    generate_tests (stub), get_baseline, tailtest_status.
    """
    from tailtest.mcp.server import main as run_server

    run_server()


# Register the Phase 1 commands
main.add_command(run_cmd)
main.add_command(doctor_cmd)
main.add_command(scan_cmd)


if __name__ == "__main__":
    main()
