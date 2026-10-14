"""Command-line interface for Ark Angel."""
from importlib.metadata import version
from pathlib import Path

import click

from ark_angel import pipeline
from ark_angel.cases.store import CaseNotFoundError, FileCaseStore
from ark_angel.models import Case, Place

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


@cli.group("place", help="Manage a case's gazetteer of named places.")
def place_group() -> None:
    """Gazetteer commands: map place names in evidence to coordinates."""


def _describe(place: Place) -> str:
    aliases = f"  aka {', '.join(place.aliases)}" if place.aliases else ""
    return (f"{place.name}: {place.latitude:.5f}, {place.longitude:.5f} "
            f"(±{place.radius_m:g} m, {place.source}){aliases}")


# ignore_unknown_options lets negative coordinates like -104.9997 through as arguments.
@place_group.command("add", context_settings={"ignore_unknown_options": True})
@click.argument("case_id")
@click.argument("name")
@click.argument("latitude", type=float)
@click.argument("longitude", type=float)
@click.option("--alias", "aliases", multiple=True, help="Another way the place is written (repeatable).")
@click.option("--radius-m", default=250.0, show_default=True,
              help="Precision in metres. Use kilometres-scale values for towns and cities.")
@click.pass_obj
def place_add(store: FileCaseStore, case_id: str, name: str, latitude: float, longitude: float,
              aliases: tuple, radius_m: float) -> None:
    """Add (or replace) a named place."""
    place, = pipeline.add_places(store, case_id, [Place(name, latitude, longitude, list(aliases), radius_m)])
    click.echo(f"Added {_describe(place)}")
    click.echo(f"Run `ark-angel enrich {case_id}` to re-correlate.")


@place_group.command("list")
@click.argument("case_id")
@click.pass_obj
def place_list(store: FileCaseStore, case_id: str) -> None:
    """List a case's named places."""
    places = store.get_case(case_id).places
    if not places:
        click.echo("No places yet. Try `ark-angel place suggest` to see which names the evidence uses.")
    for place in places:
        click.echo(_describe(place))


@place_group.command("remove")
@click.argument("case_id")
@click.argument("name")
@click.pass_obj
def place_remove(store: FileCaseStore, case_id: str, name: str) -> None:
    """Remove a named place."""
    if not pipeline.remove_place(store, case_id, name):
        raise click.ClickException(f"no place named {name!r} in case {case_id!r}")
    click.echo(f"Removed {name!r}")


@place_group.command("import")
@click.argument("case_id")
@click.argument("csv_file", type=click.Path(exists=True, dir_okay=False))
@click.pass_obj
def place_import(store: FileCaseStore, case_id: str, csv_file: str) -> None:
    """Import places from a CSV: name,latitude,longitude[,aliases][,radius_m] (aliases split by ';')."""
    places = pipeline.add_places(store, case_id, pipeline.read_places_csv(csv_file))
    click.echo(f"Imported {len(places)} place(s) into case {case_id!r}")


@place_group.command("suggest")
@click.argument("case_id")
@click.pass_obj
def place_suggest(store: FileCaseStore, case_id: str) -> None:
    """Show place-like names in the evidence that the gazetteer doesn't cover yet."""
    from ark_angel.analysis.places import suggest_places

    case = store.get_case(case_id)
    suggestions = suggest_places(case.evidence, case.places)
    if not suggestions:
        click.echo("Every place-like name in the evidence is already in the gazetteer.")
        return
    click.echo("Unresolved place names (most mentioned first):")
    for suggestion in suggestions:
        click.echo(f"  {suggestion.name}  ({len(suggestion.evidence_ids)}x: {', '.join(suggestion.evidence_ids[:4])})")
    click.echo(f"Resolve with `ark-angel place add {case_id} NAME LAT LON` or "
               f"`ark-angel place lookup {case_id} NAME --online`.")


@place_group.command("lookup")
@click.argument("case_id")
@click.argument("query")
@click.option("--online", is_flag=True, help="Send QUERY (only the query text) to OpenStreetMap Nominatim.")
@click.option("--pick", type=int, help="Save result number N to the gazetteer.")
@click.option("--name", help="Name to match in evidence (defaults to QUERY).")
@click.option("--alias", "aliases", multiple=True, help="Extra aliases to save with the picked result.")
@click.pass_obj
def place_lookup(store: FileCaseStore, case_id: str, query: str, online: bool, pick: int, name: str,
                 aliases: tuple) -> None:
    """Look up coordinates for a place name, then optionally save one result."""
    from ark_angel.geo.nominatim import search

    store.get_case(case_id)
    if not online:
        raise click.ClickException(
            "lookup sends the query text to OpenStreetMap Nominatim; re-run with --online to allow it "
            f"(or add the place by hand with `ark-angel place add {case_id} ...`)"
        )
    candidates = search(query)
    if not candidates:
        raise click.ClickException(f"no results for {query!r}")
    if pick is None:
        for number, (place, display) in enumerate(candidates, start=1):
            click.echo(f"  {number}. {place.latitude:.5f}, {place.longitude:.5f} (±{place.radius_m:g} m)  {display}")
        click.echo("Re-run with --pick N to save one.")
        return
    if not 1 <= pick <= len(candidates):
        raise click.ClickException(f"--pick must be between 1 and {len(candidates)}")
    place = candidates[pick - 1][0]
    place.name = name or query
    place.aliases = list(aliases)
    pipeline.add_places(store, case_id, [place])
    click.echo(f"Added {_describe(place)}")
    click.echo(f"Run `ark-angel enrich {case_id}` to re-correlate.")


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
