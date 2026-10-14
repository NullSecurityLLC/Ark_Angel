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
    """Ingest a text file (one item per line) or a .csv/.tsv tip-line export (one item per row)."""
    if source_file.lower().endswith((".csv", ".tsv")):
        from ark_angel.ingest.csv_source import CsvTipSource

        _report_csv(pipeline.ingest_csv(store, case_id, source_file, CsvTipSource()), case_id, dry_run=False)
        click.echo("Columns were guessed from the header; use `ark-angel ingest-csv --dry-run` to check or override.")
        return
    evidence = pipeline.ingest_text(store, case_id, source_file)
    click.echo(f"Ingested {len(evidence)} evidence item(s) into case {case_id!r}")


def _report_csv(result, case_id: str, dry_run: bool) -> None:
    verb = "Would import" if dry_run else "Imported"
    click.echo(f"{verb} {len(result.evidence)} tip(s) into case {case_id!r}: "
               f"{result.with_time} with a time, {result.with_coords} with coordinates")
    click.echo(f"  columns: {result.mapping.describe()}")
    problems = [(result.skipped_blank, "blank row(s) skipped"),
                (result.unparsed_times, "time(s) not understood (kept in the text; try --dayfirst?)"),
                (result.bad_coordinates, "row(s) with unusable coordinates")]
    for count, label in problems:
        if count:
            click.echo(f"  {count} {label}")


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
@click.option("--top", type=int, help="Only show the N highest-ranked open leads (text format).")
@click.option("--all", "show_all", is_flag=True, help="Also list dismissed leads (text format).")
@click.pass_obj
def report(store: FileCaseStore, case_id: str, fmt: str, out_path: str, top: int, show_all: bool) -> None:
    """Print or save a case report with ranked, explained leads."""
    from ark_angel.report import build_report
    from ark_angel.report.html import render_html
    from ark_angel.report.text import render_json, render_markdown, render_text

    data = build_report(store.get_case(case_id))
    renderers = {
        "text": lambda: render_text(data, limit=top, show_dismissed=show_all),
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


@cli.command("ingest-csv")
@click.argument("case_id")
@click.argument("csv_file", type=click.Path(exists=True, dir_okay=False))
@click.option("--text", "text_cols", multiple=True, help="Column(s) holding the tip text (repeatable; joined).")
@click.option("--id", "id_col", help="Column holding the tip id.")
@click.option("--when", "when_col", help="Column holding the date/time received.")
@click.option("--lat", "lat_col", help="Latitude column.")
@click.option("--lon", "lon_col", help="Longitude column.")
@click.option("--dayfirst", is_flag=True, help="Read 01/10/2026 as 1 October, not January 10.")
@click.option("--source", help="Source name for these tips (default: the file name).")
@click.option("--dry-run", is_flag=True, help="Show the column mapping and first rows without importing.")
@click.pass_obj
def ingest_csv(store: FileCaseStore, case_id: str, csv_file: str, text_cols: tuple, id_col: str, when_col: str,
               lat_col: str, lon_col: str, dayfirst: bool, source: str, dry_run: bool) -> None:
    """Import a tip-line export (CSV/TSV), one evidence item per row.

    Columns are guessed from header names. Columns that aren't text, id, time or
    coordinates are kept as "Header: value" so they're still scanned for leads.
    """
    from ark_angel.ingest.csv_source import CsvTipSource

    tip_source = CsvTipSource(text=text_cols, id=id_col, when=when_col, lat=lat_col, lon=lon_col,
                              dayfirst=dayfirst, source=source)
    result = pipeline.ingest_csv(store, case_id, csv_file, tip_source, dry_run=dry_run)
    _report_csv(result, case_id, dry_run)
    if dry_run:
        for item in result.evidence[:3]:
            click.echo(f"  {item.identifier}: {item.content}")
        click.echo("Nothing written. Re-run without --dry-run to import.")


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


@cli.group("lead", help="Review leads: verify, dismiss, reopen.")
def lead_group() -> None:
    """Lead review commands. LEAD is a number from `lead list`, an identifier, or a unique prefix."""


_STATUS_MARKS = {"verified": "verified", "open": "", "dismissed": "dismissed"}


@lead_group.command("list")
@click.argument("case_id")
@click.option("--status", type=click.Choice(["open", "verified", "dismissed", "all"]), default=None,
              help="Only leads with this status (default: everything except dismissed).")
@click.pass_obj
def lead_list(store: FileCaseStore, case_id: str, status: str) -> None:
    """List leads with their numbers and identifiers."""
    from ark_angel.review import lead_score, ranked_leads

    shown = 0
    for item in ranked_leads(store.get_case(case_id)):
        state = item.state.status
        if (status is None and state == "dismissed") or (status not in (None, "all") and state != status):
            continue
        shown += 1
        flag = "RESURFACED" if item.state.resurfaced else _STATUS_MARKS[state]
        click.echo(f"#{item.rank:<3} [{lead_score(item.lead):5.1f}] {flag:<10} {item.lead.summary}")
        click.echo(click.style(f"       {item.lead.identifier}", dim=True))
    if not shown:
        click.echo("No leads match. Run `ark-angel enrich` first?" if status is None else f"No {status} leads.")


@lead_group.command("show")
@click.argument("case_id")
@click.argument("lead_ref", metavar="LEAD")
@click.pass_obj
def lead_show(store: FileCaseStore, case_id: str, lead_ref: str) -> None:
    """Show a lead's evidence, reasons and full review history."""
    from ark_angel.review import lead_score, resolve_lead

    case = store.get_case(case_id)
    item = resolve_lead(case, lead_ref)
    lead = item.lead
    click.echo(f"#{item.rank} {lead.summary}")
    click.echo(f"  id: {lead.identifier}")
    click.echo(f"  score: {lead_score(lead):.1f}   status: {item.state.status}"
               f"{' (resurfaced)' if item.state.resurfaced else ''}")
    if item.state.new_evidence_ids:
        click.echo(f"  new since last review: {', '.join(item.state.new_evidence_ids)}")
    for reason in lead.reasons:
        click.echo(f"  - {reason}")
    history = [r for r in case.reviews if r.lead_id == lead.identifier]
    if item.state.review is not None and item.state.review.lead_id != lead.identifier:
        history = [r for r in case.reviews if r.lead_id == item.state.review.lead_id]
        click.echo(f"  (review history carried over from {item.state.review.lead_id}, which shares its evidence)")
    click.echo("Review history:" if history else "Never reviewed.")
    for review in history:
        note = f": {review.note}" if review.note else ""
        click.echo(f"  {review.reviewed_at}  {review.status:<9} by {review.reviewer}{note}")


def _review(store: FileCaseStore, case_id: str, lead_ref: str, status: str, note: str, reviewer: str) -> None:
    item, review = pipeline.review_lead(store, case_id, lead_ref, status, note or "", reviewer or "")
    click.echo(f"Marked #{item.rank} {status}: {item.lead.summary}")
    if review.note:
        click.echo(f"  note: {review.note}")


_note_option = click.option("--note", help="Why. Required when dismissing.")
_by_option = click.option("--by", "reviewer", help="Reviewer name (default: $ARK_ANGEL_REVIEWER or your login).")


@lead_group.command("verify")
@click.argument("case_id")
@click.argument("lead_ref", metavar="LEAD")
@_note_option
@_by_option
@click.pass_obj
def lead_verify(store: FileCaseStore, case_id: str, lead_ref: str, note: str, reviewer: str) -> None:
    """Mark a lead verified: a human checked it and it holds up."""
    _review(store, case_id, lead_ref, "verified", note, reviewer)


@lead_group.command("dismiss")
@click.argument("case_id")
@click.argument("lead_ref", metavar="LEAD")
@_note_option
@_by_option
@click.pass_obj
def lead_dismiss(store: FileCaseStore, case_id: str, lead_ref: str, note: str, reviewer: str) -> None:
    """Dismiss a lead. It comes back on its own if new evidence joins it later."""
    _review(store, case_id, lead_ref, "dismissed", note, reviewer)


@lead_group.command("reopen")
@click.argument("case_id")
@click.argument("lead_ref", metavar="LEAD")
@_note_option
@_by_option
@click.pass_obj
def lead_reopen(store: FileCaseStore, case_id: str, lead_ref: str, note: str, reviewer: str) -> None:
    """Put a verified or dismissed lead back in the open list."""
    _review(store, case_id, lead_ref, "open", note, reviewer)


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

    from ark_angel.report.text import review_line

    def show(lead: dict, label: str) -> None:
        click.echo(f"  {label} [{lead['score']:>5.1f}] {lead['summary']}")
        details = [review_line(lead)] if lead["review"] else []
        for line in details + lead["reasons"][:2 - len(details)]:
            click.echo(click.style(f"              {line}", dim=True))

    verified = [lead for lead in data["leads"] if lead["status"] == "verified"]
    open_leads = [lead for lead in data["leads"] if lead["status"] == "open"]
    if verified:
        click.echo(click.style("\nVerified", bold=True))
        for lead in verified:
            show(lead, f"#{lead['rank']:<2}")
    click.echo(click.style("\nTop open leads", bold=True))
    for position, lead in enumerate(open_leads[:5], start=1):
        show(lead, f"{position}.")

    Path(out_path).write_text(render_html(data, notice=DEMO_NOTICE))
    click.echo(f"\nWrote HTML case file to {out_path}")
    click.echo(f"Explore further: ark-angel lead list {case_id}   |   ark-angel report {case_id} --all")


if __name__ == "__main__":
    cli()
