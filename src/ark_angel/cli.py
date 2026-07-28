"""Command-line interface for Ark Angel."""
from importlib.metadata import version

import click

from ark_angel.analysis.rules import RuleBasedAnalyzer
from ark_angel.cases.store import FileCaseStore
from ark_angel.ingest.file_source import FileIngestionSource
from ark_angel.models import Case, Evidence

DEFAULT_DATA_DIR = ".ark_angel/cases"


@click.group(help="Ark Angel command-line interface")
@click.version_option(message="%(version)s")
@click.option(
    "--data-dir",
    default=DEFAULT_DATA_DIR,
    show_default=True,
    help="Directory where case data is stored.",
)
@click.pass_context
def cli(ctx: click.Context, data_dir: str) -> None:
    """Base command group for Ark Angel."""
    ctx.obj = FileCaseStore(data_dir)


@cli.command("version")
def show_version() -> None:
    """Show the current Ark Angel version."""
    try:
        click.echo(version("ark-angel"))
    except Exception:
        from ark_angel import __version__

        click.echo(__version__)


@cli.group("case", help="Manage investigation cases.")
def case_group() -> None:
    """Case management commands."""


@case_group.command("create")
@click.argument("case_id")
@click.argument("title")
@click.pass_obj
def case_create(store: FileCaseStore, case_id: str, title: str) -> None:
    """Create a new case."""
    store.create_case(Case(identifier=case_id, title=title))
    click.echo(f"Created case {case_id!r}: {title}")


@case_group.command("list")
@click.pass_obj
def case_list(store: FileCaseStore) -> None:
    """List known case identifiers."""
    for case_id in store.list_cases():
        click.echo(case_id)


@case_group.command("show")
@click.argument("case_id")
@click.pass_obj
def case_show(store: FileCaseStore, case_id: str) -> None:
    """Show a case's title, evidence count, and lead count."""
    case = store.get_case(case_id)
    click.echo(f"{case.identifier}: {case.title}")
    click.echo(f"  evidence: {len(case.evidence)}")
    click.echo(f"  leads: {len(case.leads)}")


@cli.command("ingest")
@click.argument("case_id")
@click.argument("source_file", type=click.Path(exists=True, dir_okay=False))
@click.pass_obj
def ingest(store: FileCaseStore, case_id: str, source_file: str) -> None:
    """Ingest a local text file as evidence for a case (one item per line)."""
    leads = FileIngestionSource().fetch_leads(source_file)
    evidence = [
        Evidence(identifier=lead.identifier, type="text", content=lead.summary)
        for lead in leads
    ]
    store.add_evidence(case_id, evidence)
    click.echo(f"Ingested {len(evidence)} evidence item(s) into case {case_id!r}")


@cli.command("enrich")
@click.argument("case_id")
@click.pass_obj
def enrich(store: FileCaseStore, case_id: str) -> None:
    """Run rule-based enrichment over a case's evidence to generate leads."""
    case = store.get_case(case_id)
    leads = RuleBasedAnalyzer().analyze_evidence(case, case.evidence)
    store.add_leads(case_id, leads)
    click.echo(f"Generated {len(leads)} lead(s) for case {case_id!r}")


@cli.command("report")
@click.argument("case_id")
@click.pass_obj
def report(store: FileCaseStore, case_id: str) -> None:
    """Print a brief summary report for a case."""
    case = store.get_case(case_id)
    analyzer = RuleBasedAnalyzer()
    click.echo(f"Case: {case.title} ({case.identifier})")
    click.echo(f"Opened: {case.created_at.isoformat()}")
    click.echo(f"Evidence items: {len(case.evidence)}")
    click.echo("Leads:")
    for lead in sorted(case.leads, key=analyzer.score_lead, reverse=True):
        click.echo(f"  [{analyzer.score_lead(lead):.1f}] {lead.summary}")


if __name__ == "__main__":
    cli()
