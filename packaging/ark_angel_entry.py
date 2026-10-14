"""Entry point for the standalone executables built by PyInstaller."""
from ark_angel.cli import cli

if __name__ == "__main__":
    cli(prog_name="ark-angel")
