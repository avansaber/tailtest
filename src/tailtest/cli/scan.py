# Copyright 2026 AvanSaber Inc.
# SPDX-License-Identifier: Apache-2.0

"""``tailtest scan`` CLI command — invokes the project scanner and prints the profile.

Phase 1: shallow scan only. Phase 3 adds ``--deep`` (LLM summary). Output
defaults to a one-line summary; ``--show`` prints the full JSON profile.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from tailtest.core.scan import ProjectScanner


@click.command("scan")
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    required=False,
    default=None,
)
@click.option(
    "--show",
    is_flag=True,
    default=False,
    help="Print the full JSON profile, not just the summary line.",
)
@click.option(
    "--deep",
    is_flag=True,
    default=False,
    help="Run deep scan (Phase 3 only — falls back to shallow in Phase 1).",
)
@click.option(
    "--save",
    is_flag=True,
    default=False,
    help="Save the profile to .tailtest/profile.json (default: don't persist).",
)
@click.option(
    "--project-root",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default=None,
    help="Project root. Overridden by the positional PATH argument when both are given.",
)
def scan_cmd(
    path: str | None,
    show: bool,
    deep: bool,
    save: bool,
    project_root: str | None,
) -> None:
    """Scan the project and print what tailtest sees.

    PATH is an optional positional shorthand for ``--project-root``. When
    both are provided, PATH wins. Either form may be omitted to scan the
    current working directory.

    Examples:

      tailtest scan                    # one-line summary of cwd
      tailtest scan .                  # same, positional form
      tailtest scan /path/to/repo
      tailtest scan --show             # full JSON profile
      tailtest scan --save             # also write to .tailtest/profile.json
      tailtest scan --project-root /path/to/repo
    """
    chosen = path or project_root
    root = Path(chosen) if chosen else Path.cwd()
    root = root.resolve()

    scanner = ProjectScanner(root)
    profile = scanner.scan_shallow()

    if save:
        scanner.save_profile(profile)

    if profile.scan_status.value == "failed":
        click.echo(profile.summary_line(), err=True)
        sys.exit(1)

    if show:
        click.echo(profile.to_json(indent=2))
    else:
        click.echo(profile.summary_line())
        # A few key bullets that fit in 4 lines max
        if profile.frameworks_detected:
            names = ", ".join(f.name for f in profile.frameworks_detected[:5])
            click.echo(f"  frameworks: {names}")
        if profile.plan_files_detected:
            kinds = ", ".join(p.kind.value for p in profile.plan_files_detected[:5])
            click.echo(f"  plan files: {kinds}")
        if profile.ai_signals:
            click.echo(f"  ai signals: {', '.join(profile.ai_signals[:5])}")

    sys.exit(0)
