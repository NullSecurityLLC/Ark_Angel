"""Command-line interface for Ark Angel."""
from importlib.metadata import version

import click


@click.group(help="Ark Angel command-line interface")
@click.version_option(message="%(version)s")
def cli() -> None:
    """Base command group for Ark Angel."""


@cli.command("version")
def show_version() -> None:
    """Show the current Ark Angel version."""
    try:
        click.echo(version("ark-angel"))
    except Exception:
        from ark_angel import __version__

        click.echo(__version__)


if __name__ == "__main__":
    cli()
