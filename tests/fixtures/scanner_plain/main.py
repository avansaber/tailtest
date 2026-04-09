"""Plain Python fixture — no AI, no plan files, no likely_vibe_coded flag."""

import click


@click.command()
def main() -> None:
    click.echo("hello")
