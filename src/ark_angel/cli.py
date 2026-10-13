"""Command-line interface for Ark Angel."""
from importlib.metadata import version
from pathlib import Path

import click

from ark_angel import pipeline
from ark_angel.cases.store import CaseNotFoundError, FileCaseStore
from ark_angel.models import Case

DEFAULT_DATA_DIR = ".ark_angel/cases"


class _Group(click.Group):
    """Turns expected failures into one-line errors instead of tracebacks."""

    def invoke(self, ctx: click.Context):
        try:
            return super().invoke(ctx)
        except CaseNotFoundError as exc:
            raise click.ClickException(f"no such case: {exc.args[0]!r} (see `ark-angel case list`)")
        except (FileExistsError, ValueError) as exc:
            raise click.ClickException(str(exc))


@click.group(cls=_Group, help="Ark Angel: pattern recognition that surfaces leads from case evidence.")
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
    evidence = pipeline.ingest_text(store, case_id, source_file)
    click.echo(f"Ingested {len(evidence)} evidence item(s) into case {case_id!r}")


@cli.command("enrich")
@click.argument("case_id")
@click.pass_obj
def enrich(store: FileCaseStore, case_id: str) -> None:
    """Extract entities, correlate them across sources and rank the leads.

    Safe to re-run: earlier analysis leads are replaced, not duplicated.
    """
    leads, analysis = pipeline.enrich(store, case_id)
    correlated = sum(1 for lead in leads if not lead.identifier.startswith("tag-"))
    click.echo(f"Generated {len(leads)} lead(s) for case {case_id!r} "
               f"({correlated} cross-source, {len(analysis.entity_index)} distinct entities)")


@cli.command("report")
@click.argument("case_id")
@click.option("--format", "fmt", type=click.Choice(["text", "md", "json", "html"]), default="text",
              show_default=True, help="Output format.")
@click.option("--out", "out_path", type=click.Path(dir_okay=False), help="Write to a file instead of stdout.")
@click.option("--top", type=int, help="Only show the N highest-ranked leads (text format).")
@click.pass_obj
def report(store: FileCaseStore, case_id: str, fmt: str, out_path: str, top: int) -> None:
    """Print or save a case report with ranked, explained leads."""
    from ark_angel.report import build_report
    from ark_angel.report.html import render_html
    from ark_angel.report.text import render_json, render_markdown, render_text

    data = build_report(store.get_case(case_id))
    renderers = {
        "text": lambda: render_text(data, limit=top),
        "md": lambda: render_markdown(data),
        "json": lambda: render_json(data),
        "html": lambda: render_html(data),
    }
    output = renderers[fmt]()
    if out_path:
        Path(out_path).write_text(output)
        click.echo(f"Wrote {fmt} report to {out_path}")
    else:
        click.echo(output)


@cli.command("ingest-image")
@click.argument("case_id")
@click.argument("image_file", type=click.Path(exists=True, dir_okay=False))
@click.pass_obj
def ingest_image(store: FileCaseStore, case_id: str, image_file: str) -> None:
    """Extract EXIF metadata (GPS, device, timestamp) from an image and add it to a case."""
    evidence, leads = pipeline.ingest_image(store, case_id, image_file)
    if evidence is not None:
        click.echo(f"Extracted {len(leads)} EXIF item(s) from {Path(image_file).name}")
    else:
        click.echo("No EXIF metadata found in image.")


@cli.command("username")
@click.argument("case_id")
@click.argument("username")
@click.pass_obj
def lookup_username(store: FileCaseStore, case_id: str, username: str) -> None:
    """Search for a username across public platforms and add hits to a case."""
    from ark_angel.ingest.username_source import SITES, UsernameIngestionSource

    store.get_case(case_id)
    click.echo(f"Searching {len(SITES)} platforms for {username!r}...")
    leads = pipeline.ingest_username_hits(store, case_id, UsernameIngestionSource().check(username))
    click.echo(f"Found {len(leads)} match(es) (unverified, confirm by hand)")
    for lead in leads:
        click.echo(f"  {lead.summary}")


@cli.command("pivot")
@click.argument("case_id")
@click.option("--online", is_flag=True, help="Actually run username lookups (makes network requests).")
@click.pass_obj
def pivot(store: FileCaseStore, case_id: str, online: bool) -> None:
    """Feed every handle found in a case's evidence into the username lookup."""
    from ark_angel.ingest.username_source import UsernameIngestionSource

    handles = pipeline.handles_in_case(store, case_id)
    if not handles:
        click.echo("No handles found in this case's evidence.")
        return
    if not online:
        click.echo(f"Handles found in evidence ({len(handles)}):")
        for handle in handles:
            click.echo(f"  @{handle}")
        click.echo("Re-run with --online to check them against public platforms.")
        return
    source = UsernameIngestionSource()
    for handle in handles:
        leads = pipeline.ingest_username_hits(store, case_id, source.check(handle))
        click.echo(f"@{handle}: {len(leads)} platform match(es)")
    click.echo(f"Run `ark-angel enrich {case_id}` to correlate the new hits.")


@cli.command("demo")
@click.option("--out", "out_path", default="ark_angel_demo.html", show_default=True,
              type=click.Path(dir_okay=False), help="Where to write the HTML case file.")
@click.option("--case-id", default="DEMO", show_default=True, help="Case id to (re)build the demo under.")
@click.pass_obj
def demo(store: FileCaseStore, out_path: str, case_id: str) -> None:
    """Build a synthetic case end to end, offline, and write an HTML report."""
    from ark_angel.demo import DEMO_NOTICE, build_demo_case
    from ark_angel.report import build_report
    from ark_angel.report.html import render_html

    click.echo(click.style("ArkAngel demo: everything below is synthetic\n", bold=True))
    workdir = store.data_dir.parent / "demo_inputs"
    case = build_demo_case(store, workdir, case_id, log=click.echo)
    data = build_report(case)

    click.echo(click.style("\nTop leads", bold=True))
    for rank, lead in enumerate(data["leads"][:5], start=1):
        click.echo(f"  {rank}. [{lead['score']:>5.1f}] {lead['summary']}")
        for reason in lead["reasons"][:2]:
            click.echo(click.style(f"              {reason}", dim=True))

    Path(out_path).write_text(render_html(data, notice=DEMO_NOTICE))
    click.echo(f"\nWrote HTML case file to {out_path}")
    click.echo(f"Explore further: ark-angel report {case_id} --top 10   |   ark-angel pivot {case_id}")


if __name__ == "__main__":
    cli()
