"""``tailtest dashboard`` CLI command.

Starts the live dashboard HTTP server and opens it in the browser.

Usage::

    tailtest dashboard
    tailtest dashboard --port 8080
    tailtest dashboard --no-open
    tailtest dashboard --dev
"""

from __future__ import annotations

import asyncio
import contextlib
import webbrowser
from pathlib import Path

import click


@click.command("dashboard")
@click.option(
    "--port",
    type=int,
    default=7777,
    show_default=True,
    help="Port to bind to. If taken, the next free port is used automatically.",
)
@click.option(
    "--no-open",
    "no_open",
    is_flag=True,
    default=False,
    help="Skip opening the browser after the server starts.",
)
@click.option(
    "--dev",
    "dev_mode",
    is_flag=True,
    default=False,
    help="Dev mode. Prints a reminder to reload the browser after editing dashboard files.",
)
@click.option(
    "--project-root",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default=None,
    help="Project root. Defaults to the current working directory.",
)
def dashboard(
    port: int,
    no_open: bool,
    dev_mode: bool,
    project_root: str | None,
) -> None:
    """Start the live tailtest dashboard in the browser.

    Serves the dashboard at http://127.0.0.1:PORT and streams test results
    in real time. Press Ctrl+C to stop.

    Examples:

      tailtest dashboard                  # start on default port 7777
      tailtest dashboard --port 8080      # use a custom port
      tailtest dashboard --no-open        # don't open the browser
      tailtest dashboard --dev            # dev mode reminder
    """
    from tailtest.dashboard.server import DashboardServer, find_free_port

    if dev_mode:
        click.echo("dev mode: reload the browser after editing dashboard files")

    root = Path(project_root) if project_root else Path.cwd()
    root = root.resolve()
    tailtest_dir = root / ".tailtest"

    actual_port = find_free_port(port)
    url = f"http://127.0.0.1:{actual_port}"

    async def _serve() -> None:
        server = DashboardServer(tailtest_dir)
        await server.start(host="127.0.0.1", port=actual_port)

        click.echo(f"tailtest dashboard running at {url}")
        click.echo("  Ctrl+C to stop · logs at .tailtest/dashboard.log")

        if not no_open:
            webbrowser.open(url)

        try:
            # Block until interrupted.
            stop = asyncio.Event()
            await stop.wait()
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            await server.stop()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_serve())

    click.echo("tailtest dashboard stopped.")
