"""Command-line interface for Ark Angel."""
import os
import sys
import time
from pathlib import Path

import click

from ark_angel import __version__, pipeline
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
@click.version_option(version=__version__, message="%(version)s")
@click.option(
    "--data-dir",
    default=DEFAULT_DATA_DIR,
    show_default=True,
    envvar="ARK_ANGEL_DATA_DIR",
    help="Directory where case data is stored (or $ARK_ANGEL_DATA_DIR).",
)
@click.option("--no-color", is_flag=True, help="Plain output (also honours the NO_COLOR environment variable).")
@click.pass_context
def cli(ctx: click.Context, data_dir: str, no_color: bool) -> None:
    """Base command group for Ark Angel."""
    if no_color or os.environ.get("NO_COLOR"):
        ctx.color = False
    elif os.environ.get("FORCE_COLOR"):
        ctx.color = True
    # Evidence can hold characters a Windows console can't show; print "?" rather than crash.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ctx.obj = FileCaseStore(data_dir)


@cli.command("version")
def show_version() -> None:
    """Show the current Ark Angel version."""
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
    """Ingest a file: text (one item per line), .csv/.tsv tip-line export, or .eml/.mbox email."""
    if source_file.lower().endswith((".eml", ".mbox")):
        _report_email(pipeline.ingest_email(store, case_id, source_file), case_id)
        return
    if source_file.lower().endswith((".csv", ".tsv")):
        from ark_angel.ingest.csv_source import CsvTipSource

        _report_csv(pipeline.ingest_csv(store, case_id, source_file, CsvTipSource()), case_id, dry_run=False)
        click.echo("Columns were guessed from the header; use `ark-angel ingest-csv --dry-run` to check or override.")
        return
    evidence = pipeline.ingest_text(store, case_id, source_file)
    click.echo(f"Ingested {len(evidence)} evidence item(s) into case {case_id!r}")
    try:
        from ark_angel.ingest.chat_source import detect_format

        if detect_format(Path(source_file)) == "whatsapp":
            click.echo("This looks like a WhatsApp export. `ark-angel ingest-chat` reads it per message, "
                       "with senders and times.")
    except (ValueError, OSError):
        pass


def _report_email(result, case_id: str) -> None:
    attached = len(result.evidence) - result.messages
    click.echo(f"Imported {result.messages} email(s) into case {case_id!r}"
               + (f", plus {attached} photo attachment(s) with EXIF ({result.attachments_with_gps} with GPS)"
                  if attached else ""))
    if result.skipped_attachments:
        click.echo(f"  {result.skipped_attachments} image attachment(s) had no EXIF data")


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
    from ark_angel.style import PLAIN, terminal_style

    data = build_report(store.get_case(case_id))
    renderers = {
        "text": lambda: render_text(data, limit=top, show_dismissed=show_all,
                                    style=PLAIN if out_path else terminal_style()),
        "md": lambda: render_markdown(data),
        "json": lambda: render_json(data),
        "html": lambda: render_html(data),
    }
    output = renderers[fmt]()
    if out_path:
        Path(out_path).write_text(output, encoding="utf-8")
        click.echo(f"Wrote {fmt} report to {out_path}")
    else:
        click.echo(output)


_MAP_FORMATS = {".kml": "kml", ".geojson": "geojson", ".json": "geojson"}


def _map_format(out_path: str, fmt: str) -> str:
    fmt = fmt or _MAP_FORMATS.get(Path(out_path).suffix.lower(), "")
    if not fmt:
        raise click.ClickException("can't tell the format from the file name; use .kml or .geojson, or pass --format")
    return fmt


def _write_map(path: str, fmt: str, data: dict, canvass: list, include_content: bool) -> None:
    from ark_angel.report.gis import render_geojson, render_kml

    render = render_kml if fmt == "kml" else render_geojson
    Path(path).write_text(render(data, canvass, include_content=include_content), encoding="utf-8")
    click.echo(f"Wrote {fmt.upper()} map to {path}")
    if fmt == "kml":
        click.echo("  Google My Maps: Create a new map > Import. Google Earth: File > Open.")
    else:
        click.echo("  QGIS / ArcGIS: add as a vector layer. Quick look: drag it onto geojson.io.")
    if include_content:
        click.echo("  It includes evidence excerpts; importing into an online map uploads them. "
                   "--no-content leaves them out.")


_map_out = click.option("--format", "fmt", type=click.Choice(["kml", "geojson"]),
                        help="Map format (default: from the --out extension).")
_no_content = click.option("--no-content", is_flag=True,
                           help="Leave evidence text out of the map (exhibit numbers and sources stay).")


@cli.command("map")
@click.argument("case_id")
@click.option("--out", "out_path", required=True, type=click.Path(dir_okay=False),
              help="File to write: .kml (Google Earth, Google My Maps) or .geojson (QGIS, ArcGIS).")
@_map_out
@_no_content
@click.pass_obj
def map_export(store: FileCaseStore, case_id: str, out_path: str, fmt: str, no_content: bool) -> None:
    """Export a case's locations as a GIS layer: location leads, evidence points and places. Offline."""
    from ark_angel.report import build_report

    data = build_report(store.get_case(case_id))
    if not data["clusters"] and not data["places"]:
        raise click.ClickException("nothing to map yet: no geotagged evidence or places in this case")
    _write_map(out_path, _map_format(out_path, fmt), data, [], not no_content)


@cli.command("cameras")
@click.argument("case_id")
@click.option("--online", is_flag=True, help="Query OpenStreetMap (sends only the search circles to Overpass).")
@click.option("--radius-m", type=float, default=300.0, show_default=True,
              help="How far past each location lead's spread to look.")
@click.option("--lead", "lead_refs", multiple=True,
              help="Only canvass this location lead (number, id or prefix; repeatable).")
@click.option("--at", "spots", multiple=True, metavar="LAT,LON", help="Also canvass this spot (repeatable).")
@click.option("--out", "out_path", type=click.Path(dir_okay=False),
              help="Save the canvass: .csv (a checklist for the team), or .kml/.geojson (the case map plus cameras).")
@click.option("--endpoint", help="Overpass API URL, e.g. a self-hosted instance (default: overpass-api.de).")
@_map_out
@_no_content
@click.pass_obj
def cameras(store: FileCaseStore, case_id: str, online: bool, radius_m: float, lead_refs: tuple, spots: tuple,
            out_path: str, endpoint: str, fmt: str, no_content: bool) -> None:
    """Video canvass: mapped cameras near the case's location leads, nearest first.

    Reads OpenStreetMap's public map of surveillance cameras (shop, street and
    traffic cameras, plate readers). It never connects to a camera. The result
    is a list of places to ask for footage. Volunteers map these, so coverage
    has gaps: no camera listed does not mean no camera.
    """
    from ark_angel.geo import cameras as cams
    from ark_angel.report import build_report
    from ark_angel.report.gis import google_maps_url, render_canvass_csv, street_view_url
    from ark_angel.review import resolve_lead
    from ark_angel.style import terminal_style

    style = terminal_style()
    case = store.get_case(case_id)
    data = build_report(case)
    lead_ids = [resolve_lead(case, ref).lead.identifier for ref in lead_refs] or None
    targets = cams.targets_from_report(data, radius_m, lead_ids)
    if lead_ids and len(targets) < len(lead_ids):
        raise click.ClickException("--lead must name location-cluster leads (`ark-angel lead list` shows them)")
    for number, spot in enumerate(spots, start=1):
        try:
            lat, lon = (float(v) for v in spot.split(","))
        except ValueError:
            raise click.ClickException(f"--at wants LAT,LON, got {spot!r}")
        targets.append(cams.CanvassTarget(f"spot {number}", lat, lon, radius_m))
    if not targets:
        raise click.ClickException("no location leads to canvass yet (run enrich, or give a spot with --at)")

    if not online:
        click.echo(f"Spots it would search ({len(targets)}):")
        for t in targets:
            click.echo(f"  {t.label}: {t.latitude:.5f}, {t.longitude:.5f}, within {t.radius_m:.0f} m")
        click.echo("Re-run with --online to look them up. Only these circles are sent, to the Overpass API.")
        return

    click.echo(f"Looking up mapped cameras around {len(targets)} spot(s)...")
    found = cams.fetch_cameras(targets, endpoint or cams.OVERPASS_URL)
    canvass = cams.canvass(targets, found)
    for t in canvass:
        click.echo("\n" + style.heading(t.label) + style.dim(f"  {t.latitude:.5f}, {t.longitude:.5f}"))
        click.echo(style.dim(f"  {google_maps_url(t.latitude, t.longitude)}"))
        if t.latest:
            click.echo(f"  latest dated evidence here: {t.latest}")
        if not t.hits:
            click.echo(style.dim(f"  no mapped cameras within {t.radius_m:.0f} m. Walk it: OSM coverage has gaps."))
            continue
        for hit in t.hits:
            cam = hit.camera
            facing = {True: style.accent("points at the spot"), False: style.dim("points away"),
                      None: ""}[hit.facing]
            who = f" · {cam.operator}" if cam.operator else ""
            where = f" · {cam.zone}" if cam.zone else ""
            click.echo(f"  {hit.distance_m:>5.0f} m  {style.strong(cam.kind)}{who}{where}  {facing}".rstrip())
            click.echo(style.dim(f"           {street_view_url(cam.latitude, cam.longitude)}"))
    total = len({(h.camera.osm_type, h.camera.osm_id) for t in canvass for h in t.hits})
    click.echo(f"\n{total} mapped camera(s). {cams.RETENTION_NOTE}")
    if any(h.camera.kind == "ALPR" for t in canvass for h in t.hits):
        click.echo("Plate readers nearby: their operator (often a police department) can search reads for a "
                   "plate in this case. Ask through the investigating agency.")
    click.echo(style.dim("Map data © OpenStreetMap contributors (ODbL)."))

    if out_path:
        result = cams.canvass_dicts(canvass)
        if out_path.lower().endswith(".csv") and not fmt:
            with open(out_path, "w", encoding="utf-8", newline="") as handle:  # write_text has no newline= on 3.9
                handle.write(render_canvass_csv(result))
            click.echo(f"Wrote canvass checklist to {out_path}")
        else:
            _write_map(out_path, _map_format(out_path, fmt), data, result, not no_content)


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


@cli.command("ingest-email")
@click.argument("case_id")
@click.argument("path", type=click.Path(exists=True))
@click.option("--source", help="Source name for these messages (default: the file or folder name).")
@click.pass_obj
def ingest_email(store: FileCaseStore, case_id: str, path: str, source: str) -> None:
    """Import email: an .eml file, an .mbox mailbox, or a folder of .eml files.

    One evidence item per message (sender, subject, body without quoted replies),
    plus one per image attachment that carries EXIF (GPS, time, device).
    Recipients are kept out of the scanned text so the tip line's own address
    doesn't become a lead.
    """
    _report_email(pipeline.ingest_email(store, case_id, path, source), case_id)


@cli.command("ingest-chat")
@click.argument("case_id")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option("--format", "fmt", type=click.Choice(["auto", "whatsapp", "telegram"]), default="auto",
              show_default=True, help="Export format.")
@click.option("--dayfirst", is_flag=True, help="Dates are day/month (most non-US phones).")
@click.option("--source", help="Source name for these messages (default: the file name).")
@click.pass_obj
def ingest_chat(store: FileCaseStore, case_id: str, path: str, fmt: str, dayfirst: bool, source: str) -> None:
    """Import a WhatsApp (.txt) or Telegram (result.json) chat export, one item per message.

    Other chat tools usually export CSV; use `ingest-csv` for those.
    """
    result = pipeline.ingest_chat(store, case_id, path, fmt, dayfirst, source)
    click.echo(f"Imported {len(result.evidence)} {result.format} message(s) into case {case_id!r}")
    if result.skipped:
        click.echo(f"  {result.skipped} system or media-only message(s) skipped")
    if result.unparsed_times:
        click.echo(f"  {result.unparsed_times} time(s) not understood (kept in the text; try --dayfirst?)")


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
@click.argument("latitude", type=float, required=False)
@click.argument("longitude", type=float, required=False)
@click.option("--alias", "aliases", multiple=True, help="Another way the place is written (repeatable).")
@click.option("--radius-m", type=float, help="Precision in metres (default 250 for hand-entered places).")
@click.option("--state", "states", multiple=True, help="Without coordinates: narrow the built-in lookup to a state.")
@click.pass_obj
def place_add(store: FileCaseStore, case_id: str, name: str, latitude: float, longitude: float,
              aliases: tuple, radius_m: float, states: tuple) -> None:
    """Add (or replace) a named place.

    Give LATITUDE LONGITUDE for a landmark. Leave them out for a US city, county
    or state and it comes from the built-in gazetteer, e.g.
    `ark-angel place add C1 Boulder --state CO`.
    """
    if (latitude is None) != (longitude is None):
        raise click.ClickException("give both LATITUDE and LONGITUDE, or neither to use the built-in gazetteer")
    if latitude is None:
        place = pipeline.place_from_builtin(name, states)
        place.aliases += [a for a in aliases if a not in place.aliases]
        if radius_m:
            place.radius_m = radius_m
    else:
        place = Place(name, latitude, longitude, list(aliases), radius_m or 250.0)
    place, = pipeline.add_places(store, case_id, [place])
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


@place_group.command("find")
@click.argument("name")
@click.option("--state", "states", multiple=True, help="Only this state (repeatable; code or name).")
def place_find(name: str, states: tuple) -> None:
    """Search the built-in US gazetteer (states, counties, cities). Offline."""
    from ark_angel.geo.builtin import find, normalize_states

    entries = find(name, normalize_states(states))
    if not entries:
        raise click.ClickException(f"no state, county or city called {name!r}"
                                   + (f" in {', '.join(states)}" if states else ""))
    for entry in entries[:20]:
        click.echo(f"  {entry.label}  {entry.latitude:.4f}, {entry.longitude:.4f}")
    if len(entries) > 20:
        click.echo(f"  … and {len(entries) - 20} more; narrow it with --state")


@place_group.command("resolve")
@click.argument("case_id")
@click.option("--state", "states", multiple=True,
              help="The case's region: prefer places in this state (repeatable; code or name).")
@click.option("--apply", "apply_", is_flag=True, help="Add the resolved places to the case gazetteer.")
@click.pass_obj
def place_resolve(store: FileCaseStore, case_id: str, states: tuple, apply_: bool) -> None:
    """Resolve unmatched place names in the evidence against the built-in US gazetteer.

    Shows what each name would resolve to; nothing is saved without --apply.
    Hints in the evidence ("Boulder, CO") beat --state.
    """
    resolutions = pipeline.resolve_unmatched_places(store, case_id, states)
    if not resolutions:
        click.echo("No unmatched place names in the evidence.")
        return
    for r in resolutions:
        if r.status == "resolved":
            click.echo(f"  {r.name} -> {r.entry.label}  [{r.why}]")
        else:
            click.echo(f"  {r.name}: {r.status}, {r.why}")
            for entry in r.candidates[:3]:
                click.echo(click.style(f"      {entry.label}", dim=True))
    resolved = [r for r in resolutions if r.status == "resolved"]
    if not apply_:
        click.echo(f"{len(resolved)} of {len(resolutions)} resolved. Nothing saved; re-run with --apply to add them.")
        return
    pipeline.add_places(store, case_id, [r.entry.to_place(r.name) for r in resolved])
    click.echo(f"Added {len(resolved)} place(s). Run `ark-angel enrich {case_id}` to re-correlate.")


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
    from ark_angel.style import terminal_style

    style = terminal_style()
    items = ranked_leads(store.get_case(case_id))
    top = max((lead_score(item.lead) for item in items), default=0.0)
    shown = 0
    for item in items:
        state = item.state.status
        if (status is None and state == "dismissed") or (status not in (None, "all") and state != status):
            continue
        shown += 1
        score = lead_score(item.lead)
        flag = "RESURFACED" if item.state.resurfaced else _STATUS_MARKS[state]
        click.echo(f"{style.accent(f'#{item.rank:<4}')}{score:5.1f}  {style.bar(score, top)}  "
                   f"{style.accent(f'{flag:<10}')} {style.strong(item.lead.summary)}")
        click.echo(style.dim(f"{'':>34}{item.lead.identifier}"))
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


_WEB_MISSING = "the website needs the web extras: pip install 'ark-angel[web]'"


@cli.group("web", help="The ArkAngel website: public profiles and tips, member workspaces, admin console.")
@click.option("--db", "db_path", type=click.Path(dir_okay=False),
              help="Site database (default: web.db next to the case data directory).")
@click.pass_context
def web_group(ctx: click.Context, db_path: str) -> None:
    """Website commands. Run `web create-admin` once, then `web serve`."""
    try:
        from ark_angel.web.db import SiteDB
    except ImportError:
        raise click.ClickException(_WEB_MISSING)
    ctx.obj = _WebContext(ctx.obj, Path(db_path) if db_path else ctx.obj.data_dir.parent / "web.db", SiteDB)


class _WebContext:
    """The case store plus the site database, opened on first use: the backup job only ever reads it."""

    def __init__(self, store: FileCaseStore, db_path: Path, factory) -> None:
        self.store, self.db_path, self._factory, self._db = store, db_path, factory, None

    @property
    def db(self):
        if self._db is None:
            self._db = self._factory(self.db_path)
        return self._db


def _new_password(prompt: str = "Password") -> str:
    from ark_angel.web.db import validate_password

    password = click.prompt(prompt, hide_input=True, confirmation_prompt=True)
    validate_password(password)
    return password


@web_group.command("create-admin")
@click.argument("username")
@click.option("--email", default="", help="Where to send admin alerts (new account requests, profiles to review).")
@click.pass_obj
def web_create_admin(obj, username: str, email: str) -> None:
    """Create an approved admin account (asks for the password twice; 12+ characters)."""
    db = obj.db
    db.create_user(username, _new_password(), role="admin", status="active", email=email)
    click.echo(f"Created admin {username.strip().lower()!r}. Start the site with `ark-angel web serve`.")


@web_group.command("users")
@click.option("--pending", is_flag=True, help="Only accounts waiting for approval.")
@click.pass_obj
def web_users(obj, pending: bool) -> None:
    """List accounts."""
    db = obj.db
    rows = db.users("pending" if pending else None)
    if not rows:
        click.echo("No pending accounts." if pending else "No accounts yet. Create one with `ark-angel web create-admin`.")
    for row in rows:
        note = f"  {row['signup_note']}" if row["status"] == "pending" and row["signup_note"] else ""
        click.echo(f"{row['username']:<24} {row['role']:<7} {row['status']:<9}{note}")


@web_group.command("approve")
@click.argument("username")
@click.pass_obj
def web_approve(obj, username: str) -> None:
    """Approve a pending account (or re-enable a disabled one)."""
    db = obj.db
    db.set_status(username, "active")
    click.echo(f"Approved {username.strip().lower()!r}")


@web_group.command("disable")
@click.argument("username")
@click.pass_obj
def web_disable(obj, username: str) -> None:
    """Disable an account and sign it out everywhere."""
    db = obj.db
    db.set_status(username, "disabled")
    click.echo(f"Disabled {username.strip().lower()!r}")


@web_group.command("passwd")
@click.argument("username")
@click.pass_obj
def web_passwd(obj, username: str) -> None:
    """Set a password. Every session that user has is signed out."""
    db = obj.db
    db.set_password(username, _new_password("New password"))
    click.echo(f"Password changed for {username.strip().lower()!r}; all of its sessions were signed out.")


@web_group.command("delete-user")
@click.argument("username")
@click.confirmation_option(prompt="Delete this account and sign out its sessions?")
@click.pass_obj
def web_delete_user(obj, username: str) -> None:
    """Delete an account (refused while it still owns cases)."""
    db = obj.db
    if not db.delete_user(username):
        raise click.ClickException(f"no user {username.strip().lower()!r}")
    click.echo(f"Deleted {username.strip().lower()!r}")


@web_group.command("import-case")
@click.argument("case_id")
@click.option("--owner", required=True, help="Username that will own the case on the site.")
@click.pass_obj
def web_import_case(obj, case_id: str, owner: str) -> None:
    """Put a case made with the command line onto the site, owned by OWNER. Its data stays where it is."""
    store, db = obj.store, obj.db
    case = store.get_case(case_id)
    db.register_case(case.identifier, owner)
    click.echo(f"Case {case.identifier!r} is now on the site, owned by {owner.strip().lower()!r}.")


@web_group.command("test-email")
@click.argument("address")
def web_test_email(address: str) -> None:
    """Send one test email using the ARK_ANGEL_SMTP_* settings, to check they work."""
    from ark_angel.web.db import validate_email
    from ark_angel.web.notify import SmtpConfig, build_message, deliver

    config = SmtpConfig.from_env(os.environ)
    if config is None:
        raise click.ClickException("email isn't set up: set ARK_ANGEL_SMTP_HOST, ARK_ANGEL_SMTP_FROM and friends "
                                   "(see docs/deploy.md)")
    to = validate_email(address)
    if not to:
        raise click.ClickException("give an address to send to")
    try:
        deliver(config, [build_message(config, to, "ArkAngel test email",
                                       "If you're reading this, ArkAngel can send email.\n")])
    except Exception as exc:  # smtplib, TLS and network errors all mean "check the settings"
        raise click.ClickException(f"sending failed via {config.host}:{config.port} ({config.security}): {exc}")
    click.echo(f"Sent a test email to {to} via {config.host}:{config.port} ({config.security}).")


@web_group.command("backup")
@click.argument("dest", type=click.Path(dir_okay=False))
@click.option("--encrypt-to", "recipient", help="Encrypt for this age public key (age1...); writes DEST.age.")
@click.pass_obj
def web_backup(obj, dest: str, recipient: str) -> None:
    """Write everything (site database, cases, uploads, profile photos) to one .tar.gz, safely while running."""
    from ark_angel.web import backup

    path = backup.snapshot(obj.store.data_dir, obj.db_path, Path(dest))
    if recipient:
        path = backup.encrypt(path, recipient)
        click.echo(f"Backed up and encrypted to {path}. Only the matching private key can open it.")
    else:
        click.echo(f"Backed up to {path}. It holds tips and contact details: store it encrypted.")


@web_group.command("backup-nightly")
@click.option("--once", is_flag=True, help="Run one backup now and exit (non-zero on failure).")
@click.pass_obj
def web_backup_nightly(obj, once: bool) -> None:
    """Back up every night: snapshot, encrypt (age), copy off-site (rclone), prune, alert on failure.

    Configured with ARK_ANGEL_BACKUP_* (see docs/deploy.md). Needs only read access to the site data.
    """
    from datetime import datetime, timezone

    from ark_angel.web import backup
    from ark_angel.web.notify import SmtpConfig, alert_now

    smtp = SmtpConfig.from_env(os.environ)

    def stamp() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def alert(subject: str, body: str) -> None:
        if smtp is None:
            return
        try:
            alert_now(smtp, backup.admin_emails(obj.db_path, smtp.admin_emails), f"[ArkAngel] {subject}", body)
        except Exception as mail_exc:  # the alert failing mustn't hide the backup problem
            click.echo(f"{stamp()} could not email the alert: {mail_exc.__class__.__name__}", err=True)

    try:
        config = backup.BackupConfig.from_env(os.environ)
    except backup.BackupError as exc:
        if once:
            raise
        # Don't crash-loop the container (or block the site): say so loudly, every day, until it's fixed.
        while True:
            click.echo(f"{stamp()} BACKUPS ARE NOT RUNNING: {exc}", err=True)
            alert("Backups are not set up", f"Nightly backups aren't running: {exc}.\n\nUntil this is fixed, "
                  "tips and evidence on the site have no backup. See the Backups section of docs/deploy.md.\n")
            time.sleep(24 * 60 * 60)

    def run() -> bool:
        try:
            out = backup.run_once(config, obj.store.data_dir, obj.db_path)
        except Exception as exc:  # anything at all: a silent crash-loop is the one failure we can't allow
            if not isinstance(exc, backup.BackupError):
                exc = backup.BackupError(f"{exc.__class__.__name__}: {exc}")
            click.echo(f"{stamp()} backup FAILED: {exc}", err=True)
            alert("Nightly backup FAILED", f"The backup at {stamp()} failed:\n\n    {exc}\n\nThe site itself is fine, "
                  "but new tips and evidence aren't backed up until this is fixed. Details: docker compose logs backup\n")
            return False
        where = f"copied to {config.dest}" if config.dest else "kept on this server only (no ARK_ANGEL_BACKUP_DEST)"
        click.echo(f"{stamp()} backup ok: {out.name}, {where}")
        backup.ping(config.ping_url)
        return True

    if not config.dest:
        click.echo("Warning: no ARK_ANGEL_BACKUP_DEST, so backups stay on this server. If it dies, they die with it.",
                   err=True)
    if once:
        if not run():
            raise SystemExit(1)
        return
    click.echo(f"Nightly backups at {config.at} UTC" + (f" to {config.dest}" if config.dest else "") +
               f", keeping {config.keep_days} days off-site and {config.local_keep} copies here.")
    while True:
        due = backup.next_run(datetime.now(timezone.utc), config.at)
        while (wait := (due - datetime.now(timezone.utc)).total_seconds()) > 0:
            time.sleep(min(wait, 300))
        run()


@web_group.command("restore")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, allow_dash=True))
@click.option("--force", is_flag=True, help="Move the site's current data aside (kept, not deleted) and restore.")
@click.pass_obj
def web_restore(obj, archive: str, force: bool) -> None:
    """Restore a backup (.tar.gz; decrypt a .age with `age -d -i key.txt` first). Stop the site first.

    ARCHIVE can be - to read it from standard input, so you can decrypt on your own computer and pipe it in:
    age -d -i key.txt BACKUP.age | ssh SERVER 'docker compose run --rm -T app ark-angel web restore - --force'
    """
    import shutil
    import tempfile

    from ark_angel.web import backup

    if archive == "-":
        base = obj.store.data_dir.parent
        base.mkdir(parents=True, exist_ok=True)
        # Written, closed, then read back: Windows can't reopen a NamedTemporaryFile that is still open.
        fd, spool = tempfile.mkstemp(dir=base, prefix=".restore-", suffix=".tar.gz")
        try:
            with os.fdopen(fd, "wb") as out:
                shutil.copyfileobj(sys.stdin.buffer, out)
            moved = backup.restore(Path(spool), obj.store.data_dir, obj.db_path, force=force)
        finally:
            Path(spool).unlink(missing_ok=True)
        click.echo("Restored the backup from standard input." + (f" The previous data is in {moved}." if moved else ""))
        click.echo("Start the site again to use it.")
        return
    if archive.endswith(".age"):
        raise click.ClickException("decrypt it first on your own computer: age -d -i KEYFILE -o backup.tar.gz "
                                   f"{Path(archive).name}")
    moved = backup.restore(Path(archive), obj.store.data_dir, obj.db_path, force=force)
    click.echo(f"Restored {Path(archive).name}." + (f" The previous data is in {moved}." if moved else ""))
    click.echo("Start the site again to use it.")


@web_group.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True,
              help="Address to listen on. Anything but localhost needs --behind-proxy.")
@click.option("--port", type=int, default=8765, show_default=True)
@click.option("--allow-host", "allow_hosts", multiple=True, envvar="ARK_ANGEL_ALLOW_HOST",
              help="Host name the site is reached at, e.g. arkangel.example.org (repeatable; or "
                   "$ARK_ANGEL_ALLOW_HOST, space-separated).")
@click.option("--behind-proxy", is_flag=True,
              help="Running behind an HTTPS reverse proxy: HTTPS-only cookies, HSTS, trust its forwarded headers.")
@click.option("--proxy-ip", default="127.0.0.1", show_default=True,
              help="The reverse proxy's address. '*' trusts any peer: only when nothing else can reach the port "
                   "(e.g. an unpublished Docker port).")
@click.option("--site-name", default="ArkAngel", show_default=True, envvar="ARK_ANGEL_SITE_NAME",
              help="Name shown in the header and titles (or $ARK_ANGEL_SITE_NAME).")
@click.option("--threads", type=int, default=8, show_default=True)
@click.option("--wait-for-admin", is_flag=True,
              help="With no admin yet, wait for one to be created instead of exiting (used by the Docker image).")
@click.pass_obj
def web_serve(obj, host: str, port: int, allow_hosts: tuple, behind_proxy: bool, proxy_ip: str, site_name: str,
              threads: int, wait_for_admin: bool) -> None:
    """Start the website. By default only this computer can reach it."""
    try:
        import waitress

        from ark_angel.web.app import LOCAL_HOSTS, create_app
    except ImportError:
        raise click.ClickException(_WEB_MISSING)
    store, db = obj.store, obj.db
    if not db.has_admin():
        if not wait_for_admin:
            raise click.ClickException("create an admin first: ark-angel web create-admin NAME")
        # No first-run web setup page on purpose: whoever reached it first would own the site.
        click.echo("No admin yet. Create one with: docker compose run --rm app ark-angel web create-admin NAME")
        while not db.has_admin():
            time.sleep(3)
        click.echo("Admin found, starting.")
    if host not in LOCAL_HOSTS and not behind_proxy:
        raise click.ClickException(
            f"listening on {host} would send passwords over plain HTTP to your network. Put the site behind an "
            "HTTPS reverse proxy and pass --behind-proxy and --allow-host, or keep the default 127.0.0.1.")
    if behind_proxy and not allow_hosts:
        raise click.ClickException("--behind-proxy needs --allow-host with the name the proxy serves it at")
    from ark_angel.web.notify import mailer_from_env

    mailer = mailer_from_env(os.environ)
    app = create_app(store, db, allowed_hosts=(*LOCAL_HOSTS, *allow_hosts), secure_cookies=behind_proxy,
                     site_name=site_name, mailer=mailer)
    shown = f"[{host}]" if ":" in host else host
    click.echo(f"{site_name} website on http://{shown}:{port}/" + (" (behind your HTTPS proxy)" if behind_proxy else ""))
    click.echo(f"Email notifications: on, via {mailer.config.host}:{mailer.config.port}" if mailer.enabled
               else "Email notifications: off (set ARK_ANGEL_SMTP_HOST to turn them on)")
    click.echo("Ctrl+C to stop.")
    proxy = {"trusted_proxy": proxy_ip, "trusted_proxy_count": 1,
             "trusted_proxy_headers": {"x-forwarded-for", "x-forwarded-proto", "x-forwarded-host"}} \
        if behind_proxy else {}
    waitress.serve(app, host=host, port=port, ident="ark-angel", threads=threads, **proxy)


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

    from ark_angel.report.text import case_header, lead_lines, top_score
    from ark_angel.style import terminal_style

    style = terminal_style()
    click.echo(style.heading("ArkAngel demo") + style.dim(f"  {style.dot} everything below is synthetic"))
    click.echo(style.dim(style.rule * 60))
    workdir = store.data_dir.parent / "demo_inputs"
    case = build_demo_case(store, workdir, case_id, log=lambda line: click.echo(style.dim(line)))
    data = build_report(case)

    click.echo("")
    for line in case_header(data, style):
        click.echo(line)
    top = top_score(data)
    verified = [lead for lead in data["leads"] if lead["status"] == "verified"]
    open_leads = [lead for lead in data["leads"] if lead["status"] == "open"]
    if verified:
        click.echo("\n" + style.heading("Verified"))
        for lead in verified:
            for line in lead_lines(lead, top, style, reasons=1):
                click.echo(line)
    click.echo("\n" + style.heading("Top open leads"))
    for lead in open_leads[:5]:
        for line in lead_lines(lead, top, style, reasons=2):
            click.echo(line)

    Path(out_path).write_text(render_html(data, notice=DEMO_NOTICE), encoding="utf-8")
    click.echo("\n" + style.accent(f"Wrote HTML case file to {out_path}")
               + style.dim(" (open it in a browser; prints cleanly)"))
    click.echo(style.dim(f"Explore further: ark-angel lead list {case_id}   |   ark-angel report {case_id} --all"))


if __name__ == "__main__":
    cli()
