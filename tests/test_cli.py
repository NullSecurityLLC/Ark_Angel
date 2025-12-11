from click.testing import CliRunner

from ark_angel import __version__
from ark_angel.cli import cli

runner = CliRunner()


def test_version_importable() -> None:
    assert __version__ == "0.1.0"


def test_cli_help() -> None:
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Ark Angel" in result.output
