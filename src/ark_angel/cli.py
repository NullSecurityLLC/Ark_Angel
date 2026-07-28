"""Command-line interface for Ark Angel."""
from importlib.metadata import version

import click

import ark_angel.analysis.rules  # noqa: F401  (registers the "rules" analyzer)
import ark_angel.ingest.file_source  # noqa: F401  (registers the "file" ingestion source)
from ark_angel.cases.store import FileCaseStore
from ark_angel.models import Case, Evidence
from ark_angel.registry import analyzers, ingestion_sources

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
@click.option(
    "--source",
    "source_name",
    default="file",
    show_default=True,
    help="Registered ingestion source to use.",
)
@click.pass_obj
def ingest(store: FileCaseStore, case_id: str, source_file: str, source_name: str) -> None:
    """Ingest a local text file as evidence for a case (one item per line)."""
    leads = ingestion_sources.create(source_name).fetch_leads(source_file)
    evidence = [
        Evidence(
            identifier=lead.identifier,
            type="text",
            content=lead.summary,
            metadata={"source": source_name},
        )
        for lead in leads
    ]
    store.add_evidence(case_id, evidence)
    click.echo(
        f"Ingested {len(evidence)} evidence item(s) into case {case_id!r} via {source_name!r}"
    )


@cli.command("enrich")
@click.argument("case_id")
@click.option(
    "--analyzer",
    "analyzer_name",
    default="rules",
    show_default=True,
    help="Registered analyzer to use.",
)
@click.pass_obj
def enrich(store: FileCaseStore, case_id: str, analyzer_name: str) -> None:
    """Run enrichment over a case's evidence to generate leads."""
    case = store.get_case(case_id)
    leads = analyzers.create(analyzer_name).analyze_evidence(case, case.evidence)
    for lead in leads:
        lead.metadata = {"produced_by": analyzer_name}
    store.add_leads(case_id, leads)
    click.echo(f"Generated {len(leads)} lead(s) for case {case_id!r} via {analyzer_name!r}")


@cli.command("report")
@click.argument("case_id")
@click.option(
    "--analyzer",
    "analyzer_name",
    default="rules",
    show_default=True,
    help="Registered analyzer to use for scoring leads.",
)
@click.pass_obj
def report(store: FileCaseStore, case_id: str, analyzer_name: str) -> None:
    """Print a brief summary report for a case."""
    case = store.get_case(case_id)
    analyzer = analyzers.create(analyzer_name)
    click.echo(f"Case: {case.title} ({case.identifier})")
    click.echo(f"Opened: {case.created_at.isoformat()}")
    click.echo(f"Evidence items: {len(case.evidence)}")
    click.echo("Leads:")
    for lead in sorted(case.leads, key=analyzer.score_lead, reverse=True):
        click.echo(f"  [{analyzer.score_lead(lead):.1f}] {lead.summary}")


@cli.command("plugins")
def list_plugins() -> None:
    """List registered ingestion sources and analyzers."""
    click.echo("Ingestion sources: " + ", ".join(ingestion_sources.names()))
    click.echo("Analyzers: " + ", ".join(analyzers.names()))


if __name__ == "__main__":
    cli()
