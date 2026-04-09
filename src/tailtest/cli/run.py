"""``tailtest run`` CLI command.

The hot loop in command-line form. Used by:
- The PostToolUse hook — invokes ``tailtest run --changed <file>`` via
  subprocess after every Claude edit
- Users directly, for ad-hoc runs from a terminal

Exit code is **always 0** even when tests fail. Tailtest is report-only;
the user (or Claude) decides what to do with the findings. The only
non-zero exit codes are for genuine engine errors (config can't load,
no runner available at all, etc.).
"""

from __future__ import annotations

import asyncio
import json as json_lib
import sys
from pathlib import Path
from uuid import uuid4

import click

# Self-register Python runner.
import tailtest.core.runner.python  # noqa: F401, E402
from tailtest.core.baseline import BaselineManager
from tailtest.core.config import ConfigLoader, DepthMode
from tailtest.core.findings.schema import FindingBatch
from tailtest.core.reporter import TerminalReporter
from tailtest.core.runner import get_default_registry

# Format options. Phase 1 ships terminal + json. Phase 2 adds html.
_FORMAT_TERMINAL = "terminal"
_FORMAT_JSON = "json"


@click.command("run")
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    required=False,
    default=None,
)
@click.option(
    "--changed",
    "changed_files",
    multiple=True,
    type=click.Path(),
    help="Files that changed (typically what Claude just edited). Repeatable.",
)
@click.option(
    "--depth",
    type=click.Choice(["off", "quick", "standard", "thorough", "paranoid"]),
    default=None,
    help="Override the configured depth mode for this run.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice([_FORMAT_TERMINAL, _FORMAT_JSON]),
    default=_FORMAT_TERMINAL,
    help="Output format.",
)
@click.option(
    "--timeout",
    type=int,
    default=30,
    help="Per-test-run timeout in seconds.",
)
@click.option(
    "--show-baseline",
    is_flag=True,
    default=False,
    help="Include baselined findings in the output (debt view).",
)
@click.option(
    "--project-root",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default=None,
    help="Project root. Overridden by the positional PATH argument when both are given.",
)
def run_cmd(
    path: str | None,
    changed_files: tuple[str, ...],
    depth: str | None,
    output_format: str,
    timeout: int,
    show_baseline: bool,
    project_root: str | None,
) -> None:
    """Run impacted tests for changed files and report findings.

    PATH is an optional positional shorthand for ``--project-root``. When
    both are provided, PATH wins. Either form may be omitted to run against
    the current working directory.

    Examples:

      tailtest run                                    # run all tests in cwd
      tailtest run .                                  # same, positional form
      tailtest run /path/to/repo                      # run all tests in target
      tailtest run --changed src/foo.py               # run impacted tests for one file
      tailtest run --changed src/a.py --changed src/b.py
      tailtest run --depth quick                      # override depth for one run
      tailtest run --format json                      # JSON output (used by hooks)
    """
    chosen = path or project_root
    root = Path(chosen) if chosen else Path.cwd()
    root = root.resolve()

    # Load config (fresh per audit gap #4)
    tailtest_dir = root / ".tailtest"
    config = ConfigLoader(tailtest_dir).load()

    # Apply depth override if provided. Soft-warn on unshipped depths
    # (Phase 1 audit gap #9).
    effective_depth = config.depth
    if depth is not None:
        effective_depth = DepthMode(depth)
        if effective_depth in (DepthMode.THOROUGH, DepthMode.PARANOID):
            click.echo(
                f"tailtest: depth set to '{effective_depth.value}', but the "
                f"{effective_depth.value}-depth features (LLM-judge + validator "
                f"subagent + red-team) ship in v0.1.0-beta.1 / rc.1 / rc.2 "
                f"respectively. Running at standard for now.",
                err=True,
            )

    # Find a runner
    registry = get_default_registry()
    runners = registry.all_for_project(root)
    if not runners:
        if output_format == _FORMAT_JSON:
            click.echo(
                json_lib.dumps(
                    {
                        "run_id": str(uuid4()),
                        "depth": effective_depth.value,
                        "findings": [],
                        "tests_passed": 0,
                        "tests_failed": 0,
                        "summary_line": "tailtest: no runners detected for this project",
                    },
                    indent=2,
                )
            )
        else:
            click.echo(
                "tailtest: no runners detected for this project. Run `tailtest doctor` to debug."
            )
        sys.exit(0)

    runner = runners[0]
    run_id = str(uuid4())

    # Collect changed file paths
    files_list = [Path(f) for f in changed_files]

    # If --changed was passed, use TIA. If not, run all tests.
    test_ids: list[str]
    if files_list:
        try:
            test_ids = asyncio.run(runner.impacted(files_list, diff=None))
        except Exception as exc:  # noqa: BLE001
            click.echo(f"tailtest: impacted_tests failed: {exc}", err=True)
            sys.exit(0)
    else:
        test_ids = []  # empty == run all tests

    # Run the tests
    try:
        batch = asyncio.run(runner.run(test_ids, run_id=run_id, timeout_seconds=float(timeout)))
    except TimeoutError:
        click.echo(f"tailtest: test run exceeded {timeout}s timeout", err=True)
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        click.echo(f"tailtest: runner failed: {exc}", err=True)
        sys.exit(0)

    # Apply baseline + stamp depth
    manager = BaselineManager(tailtest_dir)
    batch = manager.apply_to(batch)
    batch = batch.model_copy(update={"depth": effective_depth.value})

    # Persist to disk for the report skill + dashboard + future hook reads
    _persist_latest(batch, tailtest_dir)

    # Format and emit
    if output_format == _FORMAT_JSON:
        click.echo(batch.model_dump_json(indent=2))
    else:
        reporter = TerminalReporter()
        click.echo(reporter.format(batch, show_baseline=show_baseline))

    # Always exit 0 (report-only per ADR 0004)
    sys.exit(0)


def _persist_latest(batch: FindingBatch, tailtest_dir: Path) -> None:
    """Write the batch to .tailtest/reports/latest.json (best effort, never raises)."""
    try:
        reports_dir = tailtest_dir / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        (reports_dir / "latest.json").write_text(batch.model_dump_json(indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
