"""Command-line interface for Ark Angel."""
import os
import sys
import time
from pathlib import Path
from typing import Optional

import click

from ark_angel import __version__, pipeline
from ark_angel.cases.store import CaseNotFoundError, FileCaseStore
from ark_angel.models import Case, Place
from ark_angel.safety import ensure_safe_target, running_elevated
from ark_angel.textsafe import printable, terminal_safe

DEFAULT_DATA_DIR = ".ark_angel/cases"


def write_out(path: str, text: str, newline: Optional[str] = None) -> None:
    """Write an output file the user named with --out, never into an operating-system folder."""
    ensure_safe_target(path, "write a file")
    with open(path, "w", encoding="utf-8", newline=newline) as handle:
        handle.write(text)


def echo(message="", **kwargs) -> None:
    """``click.echo`` that can't be made to emit terminal control sequences by evidence text (see textsafe)."""
    click.echo(terminal_safe(message) if isinstance(message, str) else message, **kwargs)


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
    if running_elevated():
        click.echo("Note: ArkAngel is running as Administrator. It never needs that; a normal window is safer.",
                   err=True)
    ctx.obj = FileCaseStore(data_dir)


@cli.command("version")
def show_version() -> None:
    """Show the current Ark Angel version."""
    echo(__version__)


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
    echo(f"Created case {case_id!r}: {title}")


@case_group.command("list")
@click.pass_obj
def case_list(store: FileCaseStore) -> None:
    """List known case identifiers."""
    for case_id in store.list_cases():
        echo(case_id)


@case_group.command("show")
@click.argument("case_id")
@click.pass_obj
def case_show(store: FileCaseStore, case_id: str) -> None:
    """Show a case's title, evidence count, and lead count."""
    case = store.get_case(case_id)
    echo(f"{case.identifier}: {case.title}")
    if case.principal is not None:
        echo(f"  principal defender case protecting {case.principal.name} (`ark-angel defend show`)")
    echo(f"  evidence: {len(case.evidence)}")
    echo(f"  leads: {len(case.leads)}")


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
        echo("Columns were guessed from the header; use `ark-angel ingest-csv --dry-run` to check or override.")
        return
    evidence = pipeline.ingest_text(store, case_id, source_file)
    echo(f"Ingested {len(evidence)} evidence item(s) into case {case_id!r}")
    try:
        from ark_angel.ingest.chat_source import detect_format

        if detect_format(Path(source_file)) == "whatsapp":
            echo("This looks like a WhatsApp export. `ark-angel ingest-chat` reads it per message, "
                       "with senders and times.")
    except (ValueError, OSError):
        pass


def _report_email(result, case_id: str) -> None:
    attached = len(result.evidence) - result.messages
    echo(f"Imported {result.messages} email(s) into case {case_id!r}"
               + (f", plus {attached} photo attachment(s) with EXIF ({result.attachments_with_gps} with GPS)"
                  if attached else ""))
    if result.skipped_attachments:
        echo(f"  {result.skipped_attachments} image attachment(s) had no EXIF data")


def _report_csv(result, case_id: str, dry_run: bool) -> None:
    verb = "Would import" if dry_run else "Imported"
    echo(f"{verb} {len(result.evidence)} tip(s) into case {case_id!r}: "
               f"{result.with_time} with a time, {result.with_coords} with coordinates")
    echo(f"  columns: {printable(result.mapping.describe())}")
    problems = [(result.skipped_blank, "blank row(s) skipped"),
                (result.unparsed_times, "time(s) not understood (kept in the text; try --dayfirst?)"),
                (result.bad_coordinates, "row(s) with unusable coordinates")]
    for count, label in problems:
        if count:
            echo(f"  {count} {label}")


@cli.command("enrich")
@click.argument("case_id")
@click.pass_obj
def enrich(store: FileCaseStore, case_id: str) -> None:
    """Extract entities, correlate them across sources and rank the leads.

    Safe to re-run: earlier analysis leads are replaced, not duplicated.
    """
    leads, analysis = pipeline.enrich(store, case_id)
    if store.get_case(case_id).principal is not None:
        threats = sum(1 for lead in leads if lead.identifier.startswith("threat-"))
        echo(f"Generated {len(leads)} lead(s) for defender case {case_id!r} "
                   f"({threats} threat, {len(leads) - threats} exposure/pattern). "
                   f"See `ark-angel defend brief {case_id}`.")
        return
    correlated = sum(1 for lead in leads if not lead.identifier.startswith("tag-"))
    echo(f"Generated {len(leads)} lead(s) for case {case_id!r} "
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
        write_out(out_path, output)
        echo(f"Wrote {fmt} report to {out_path}")
    else:
        echo(output)


_MAP_FORMATS = {".kml": "kml", ".geojson": "geojson", ".json": "geojson"}


def _map_format(out_path: str, fmt: str) -> str:
    fmt = fmt or _MAP_FORMATS.get(Path(out_path).suffix.lower(), "")
    if not fmt:
        raise click.ClickException("can't tell the format from the file name; use .kml or .geojson, or pass --format")
    return fmt


def _write_map(path: str, fmt: str, data: dict, canvass: list, include_content: bool) -> None:
    from ark_angel.report.gis import render_geojson, render_kml

    render = render_kml if fmt == "kml" else render_geojson
    write_out(path, render(data, canvass, include_content=include_content))
    echo(f"Wrote {fmt.upper()} map to {path}")
    if fmt == "kml":
        echo("  Google My Maps: Create a new map > Import. Google Earth: File > Open.")
    else:
        echo("  QGIS / ArcGIS: add as a vector layer. Quick look: drag it onto geojson.io.")
    if include_content:
        echo("  It includes evidence excerpts; importing into an online map uploads them. "
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
    withheld = data["sensitive_clusters_withheld"]
    if not data["clusters"] and not data["places"]:
        raise click.ClickException("nothing to map yet: no geotagged evidence or places in this case"
                                   + (f" ({withheld} location(s) near sensitive places left out)" if withheld else ""))
    _write_map(out_path, _map_format(out_path, fmt), data, [], not no_content)
    if withheld:
        echo(f"  Left out {withheld} location(s) on or next to the principal's sensitive places.")


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
        echo(f"Spots it would search ({len(targets)}):")
        for t in targets:
            echo(f"  {printable(t.label)}: {t.latitude:.5f}, {t.longitude:.5f}, within {t.radius_m:.0f} m")
        echo("Re-run with --online to look them up. Only these circles are sent, to the Overpass API.")
        return

    echo(f"Looking up mapped cameras around {len(targets)} spot(s)...")
    found = cams.fetch_cameras(targets, endpoint or cams.OVERPASS_URL)
    canvass = cams.canvass(targets, found)
    for t in canvass:
        echo("\n" + style.heading(t.label) + style.dim(f"  {t.latitude:.5f}, {t.longitude:.5f}"))
        echo(style.dim(f"  {google_maps_url(t.latitude, t.longitude)}"))
        if t.latest:
            echo(f"  latest dated evidence here: {printable(t.latest)}")
        if not t.hits:
            echo(style.dim(f"  no mapped cameras within {t.radius_m:.0f} m. Walk it: OSM coverage has gaps."))
            continue
        for hit in t.hits:
            cam = hit.camera
            facing = {True: style.accent("points at the spot"), False: style.dim("points away"),
                      None: ""}[hit.facing]
            who = f" · {printable(cam.operator)}" if cam.operator else ""  # from OpenStreetMap: anyone can edit it
            where = f" · {printable(cam.zone)}" if cam.zone else ""
            echo(f"  {hit.distance_m:>5.0f} m  {style.strong(cam.kind)}{who}{where}  {facing}".rstrip())
            echo(style.dim(f"           {street_view_url(cam.latitude, cam.longitude)}"))
    total = len({(h.camera.osm_type, h.camera.osm_id) for t in canvass for h in t.hits})
    echo(f"\n{total} mapped camera(s). {cams.RETENTION_NOTE}")
    if any(h.camera.kind == "ALPR" for t in canvass for h in t.hits):
        echo("Plate readers nearby: their operator (often a police department) can search reads for a "
                   "plate in this case. Ask through the investigating agency.")
    echo(style.dim("Map data © OpenStreetMap contributors (ODbL)."))

    if out_path:
        result = cams.canvass_dicts(canvass)
        if out_path.lower().endswith(".csv") and not fmt:
            write_out(out_path, render_canvass_csv(result), newline="")
            echo(f"Wrote canvass checklist to {out_path}")
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
            echo(f"  {printable(item.identifier)}: {printable(item.content)}")
        echo("Nothing written. Re-run without --dry-run to import.")


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
    echo(f"Imported {len(result.evidence)} {result.format} message(s) into case {case_id!r}")
    if result.skipped:
        echo(f"  {result.skipped} system or media-only message(s) skipped")
    if result.unparsed_times:
        echo(f"  {result.unparsed_times} time(s) not understood (kept in the text; try --dayfirst?)")


@cli.command("ingest-image")
@click.argument("case_id")
@click.argument("image_file", type=click.Path(exists=True, dir_okay=False))
@click.pass_obj
def ingest_image(store: FileCaseStore, case_id: str, image_file: str) -> None:
    """Extract EXIF metadata (GPS, device, timestamp) from an image and add it to a case."""
    evidence, leads = pipeline.ingest_image(store, case_id, image_file)
    if evidence is not None:
        echo(f"Extracted {len(leads)} EXIF item(s) from {Path(image_file).name}")
    else:
        echo("No EXIF metadata found in image.")


@cli.command("username")
@click.argument("case_id")
@click.argument("username")
@click.pass_obj
def lookup_username(store: FileCaseStore, case_id: str, username: str) -> None:
    """Search for a username across public platforms and add hits to a case."""
    from ark_angel.ingest.username_source import SITES, UsernameIngestionSource

    store.get_case(case_id)
    echo(f"Searching {len(SITES)} platforms for {username!r}...")
    leads = pipeline.ingest_username_hits(store, case_id, UsernameIngestionSource().check(username))
    echo(f"Found {len(leads)} match(es) (unverified, confirm by hand)")
    for lead in leads:
        echo(f"  {printable(lead.summary)}")


@cli.command("pivot")
@click.argument("case_id")
@click.option("--online", is_flag=True, help="Actually run username lookups (makes network requests).")
@click.pass_obj
def pivot(store: FileCaseStore, case_id: str, online: bool) -> None:
    """Feed every handle found in a case's evidence into the username lookup."""
    from ark_angel.ingest.username_source import UsernameIngestionSource

    handles = pipeline.handles_in_case(store, case_id)
    if not handles:
        echo("No handles found in this case's evidence.")
        return
    if not online:
        echo(f"Handles found in evidence ({len(handles)}):")
        for handle in handles:
            echo(f"  @{handle}")
        echo("Re-run with --online to check them against public platforms.")
        return
    source = UsernameIngestionSource()
    for handle in handles:
        try:
            hits = source.check(handle)
        except ValueError:
            echo(f"@{printable(handle)}: skipped, not a plain username")
            continue
        leads = pipeline.ingest_username_hits(store, case_id, hits)
        echo(f"@{handle}: {len(leads)} platform match(es)")
    echo(f"Run `ark-angel enrich {case_id}` to correlate the new hits.")


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
    echo(f"Added {_describe(place)}")
    echo(f"Run `ark-angel enrich {case_id}` to re-correlate.")


@place_group.command("list")
@click.argument("case_id")
@click.pass_obj
def place_list(store: FileCaseStore, case_id: str) -> None:
    """List a case's named places."""
    places = store.get_case(case_id).places
    if not places:
        echo("No places yet. Try `ark-angel place suggest` to see which names the evidence uses.")
    for place in places:
        echo(_describe(place))


@place_group.command("remove")
@click.argument("case_id")
@click.argument("name")
@click.pass_obj
def place_remove(store: FileCaseStore, case_id: str, name: str) -> None:
    """Remove a named place."""
    if not pipeline.remove_place(store, case_id, name):
        raise click.ClickException(f"no place named {name!r} in case {case_id!r}")
    echo(f"Removed {name!r}")


@place_group.command("import")
@click.argument("case_id")
@click.argument("csv_file", type=click.Path(exists=True, dir_okay=False))
@click.pass_obj
def place_import(store: FileCaseStore, case_id: str, csv_file: str) -> None:
    """Import places from a CSV: name,latitude,longitude[,aliases][,radius_m] (aliases split by ';')."""
    places = pipeline.add_places(store, case_id, pipeline.read_places_csv(csv_file))
    echo(f"Imported {len(places)} place(s) into case {case_id!r}")


@place_group.command("suggest")
@click.argument("case_id")
@click.pass_obj
def place_suggest(store: FileCaseStore, case_id: str) -> None:
    """Show place-like names in the evidence that the gazetteer doesn't cover yet."""
    from ark_angel.analysis.places import suggest_places

    case = store.get_case(case_id)
    suggestions = suggest_places(case.evidence, case.known_places())
    if not suggestions:
        echo("Every place-like name in the evidence is already in the gazetteer.")
        return
    echo("Unresolved place names (most mentioned first):")
    for suggestion in suggestions:
        echo(f"  {printable(suggestion.name)}  ({len(suggestion.evidence_ids)}x: {', '.join(suggestion.evidence_ids[:4])})")
    echo(f"Resolve with `ark-angel place add {case_id} NAME LAT LON` or "
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
        echo(f"  {entry.label}  {entry.latitude:.4f}, {entry.longitude:.4f}")
    if len(entries) > 20:
        echo(f"  … and {len(entries) - 20} more; narrow it with --state")


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
        echo("No unmatched place names in the evidence.")
        return
    for r in resolutions:
        if r.status == "resolved":
            echo(f"  {printable(r.name)} -> {r.entry.label}  [{r.why}]")
        else:
            echo(f"  {printable(r.name)}: {r.status}, {r.why}")
            for entry in r.candidates[:3]:
                echo(click.style(f"      {entry.label}", dim=True))
    resolved = [r for r in resolutions if r.status == "resolved"]
    if not apply_:
        echo(f"{len(resolved)} of {len(resolutions)} resolved. Nothing saved; re-run with --apply to add them.")
        return
    pipeline.add_places(store, case_id, [r.entry.to_place(r.name) for r in resolved])
    echo(f"Added {len(resolved)} place(s). Run `ark-angel enrich {case_id}` to re-correlate.")


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
            echo(f"  {number}. {place.latitude:.5f}, {place.longitude:.5f} (±{place.radius_m:g} m)  {display}")
        echo("Re-run with --pick N to save one.")
        return
    if not 1 <= pick <= len(candidates):
        raise click.ClickException(f"--pick must be between 1 and {len(candidates)}")
    place = candidates[pick - 1][0]
    place.name = name or query
    place.aliases = list(aliases)
    pipeline.add_places(store, case_id, [place])
    echo(f"Added {_describe(place)}")
    echo(f"Run `ark-angel enrich {case_id}` to re-correlate.")


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
        echo(f"{style.accent(f'#{item.rank:<4}')}{score:5.1f}  {style.bar(score, top)}  "
                   f"{style.accent(f'{flag:<10}')} {style.strong(printable(item.lead.summary))}")
        echo(style.dim(f"{'':>34}{item.lead.identifier}"))
    if not shown:
        echo("No leads match. Run `ark-angel enrich` first?" if status is None else f"No {status} leads.")


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
    echo(f"#{item.rank} {printable(lead.summary)}")
    echo(f"  id: {lead.identifier}")
    echo(f"  score: {lead_score(lead):.1f}   status: {item.state.status}"
               f"{' (resurfaced)' if item.state.resurfaced else ''}")
    if item.state.new_evidence_ids:
        echo(f"  new since last review: {', '.join(item.state.new_evidence_ids)}")
    for reason in lead.reasons:
        echo(f"  - {printable(reason)}")
    history = [r for r in case.reviews if r.lead_id == lead.identifier]
    if item.state.review is not None and item.state.review.lead_id != lead.identifier:
        history = [r for r in case.reviews if r.lead_id == item.state.review.lead_id]
        echo(f"  (review history carried over from {item.state.review.lead_id}, which shares its evidence)")
    echo("Review history:" if history else "Never reviewed.")
    for review in history:
        note = f": {review.note}" if review.note else ""
        echo(f"  {review.reviewed_at}  {review.status:<9} by {printable(review.reviewer)}{printable(note)}")


def _review(store: FileCaseStore, case_id: str, lead_ref: str, status: str, note: str, reviewer: str) -> None:
    item, review = pipeline.review_lead(store, case_id, lead_ref, status, note or "", reviewer or "")
    echo(f"Marked #{item.rank} {status}: {printable(item.lead.summary)}")
    if review.note:
        echo(f"  note: {printable(review.note)}")


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


# Browsing: the Recorder extension, history import and the investigator's trail


def _local_time(value: str, option: str):
    from ark_angel.analysis.extract import parse_datetime

    when = parse_datetime(value)
    if when is None:
        raise click.ClickException(f"{option} wants a date like 2026-11-02 or 2026-11-02 14:30, got {value!r}")
    return when


@cli.command("browse")
@click.argument("case_id")
@click.option("--port", type=int, default=8766, show_default=True, help="Local port the extension sends to.")
@click.option("--new-token", is_flag=True, help="Issue a new pairing token (the extension must be re-paired).")
@click.option("--no-enrich", is_flag=True, help="Don't re-run enrich when recording stops.")
@click.pass_obj
def browse(store: FileCaseStore, case_id: str, port: int, new_token: bool, no_enrich: bool) -> None:
    """Record your browsing into a case, through the ArkAngel Recorder extension.

    Starts a receiver on 127.0.0.1 (nothing leaves this computer). In the
    extension, set the address and token shown here and press Record. Every
    page, search and "Add to case" clip lands in the case as you go, and pages
    that mention something already in the case are flagged as MATCH. Stop with
    Ctrl+C; then `ark-angel trail` shows what you did.

    Only what pages say counts toward leads: your searches, the links you
    follow and the profiles you open never corroborate anything.
    """
    from ark_angel.browse_server import Recorder, RecorderServer, load_token
    from ark_angel.style import terminal_style

    style = terminal_style()
    recorder = Recorder(store, case_id, log=echo)
    token = load_token(store.data_dir.parent / "recorder_token", rotate=new_token)
    try:
        server = RecorderServer(recorder, token, port)
    except OSError as exc:
        raise click.ClickException(f"can't listen on 127.0.0.1:{port} ({exc.strerror}); try --port")
    echo(style.heading(f"Recording into case {case_id}") + style.dim(f"  {printable(recorder.title)}"))
    echo(f"  address  {style.accent(f'http://127.0.0.1:{server.port}')}")
    echo(f"  token    {style.accent(token)}")
    echo(style.dim("  Paste both into the ArkAngel Recorder extension and press Record. Ctrl+C stops.\n"))
    import signal

    def _stop(*_):  # closing the terminal or `kill` stops it as cleanly as Ctrl+C
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, _stop)
    except (ValueError, AttributeError):  # not the main thread, or a platform without SIGTERM
        pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    echo(f"\nRecorded {recorder.captured} capture(s), {recorder.matched} matching the case.")
    if recorder.captured and not no_enrich:
        leads, _ = pipeline.enrich(store, case_id)
        echo(f"Re-ran enrich: {len(leads)} lead(s).")
    echo(f"See what you did: ark-angel trail {case_id}")


@cli.command("ingest-history")
@click.argument("case_id")
@click.argument("history_file", type=click.Path(exists=True, dir_okay=False))
@click.option("--since", required=True, help="Start of the window, local time: 2026-11-02 or '2026-11-02 14:30'.")
@click.option("--until", help="End of the window (default: now).")
@click.option("--format", "fmt", type=click.Choice(["auto", "chrome", "firefox", "safari"]), default="auto",
              show_default=True)
@click.option("--dry-run", is_flag=True, help="Show what would be imported, without saving.")
@click.pass_obj
def ingest_history(store: FileCaseStore, case_id: str, history_file: str, since: str, until: str, fmt: str,
                   dry_run: bool) -> None:
    """Import a window of browser history, for when the Recorder wasn't running.

    HISTORY_FILE is Chrome/Edge/Brave's `History`, Firefox's `places.sqlite`
    or Safari's `History.db` (see the README for where they live). History has
    no page text, so it gives you the trail (searches, profiles, order) but
    adds nothing that counts toward leads.
    """
    result = pipeline.ingest_history(store, case_id, history_file, _local_time(since, "--since"),
                                     _local_time(until, "--until") if until else None, fmt, dry_run)
    searches = sum(1 for e in result.evidence if e.metadata["kind"] == "search")
    profiles = sum(1 for e in result.evidence if e.metadata.get("profile"))
    verb = "Would import" if dry_run else "Imported"
    echo(f"{verb} {len(result.evidence)} visit(s) from {result.format} history into case {case_id!r}: "
               f"{searches} search(es), {profiles} profile page(s)"
               + (f"; {result.skipped} non-web page(s) skipped" if result.skipped else ""))
    if dry_run:
        for item in result.evidence[:15]:
            echo(f"  {printable(item.content)}")
        if len(result.evidence) > 15:
            echo(f"  … {len(result.evidence) - 15} more")
    else:
        echo(f"See the trail: ark-angel trail {case_id}")


@cli.command("trail")
@click.argument("case_id")
@click.option("--format", "fmt", type=click.Choice(["text", "md", "json"]), default="text", show_default=True)
@click.option("--out", "out_path", type=click.Path(dir_okay=False), help="Write to a file instead of stdout.")
@click.option("--session", type=int, help="Only show this session's steps (text format).")
@click.pass_obj
def trail(store: FileCaseStore, case_id: str, fmt: str, out_path: str, session: int) -> None:
    """Your investigation trail: sessions, pivots, threads you followed, loose ends you dropped."""
    from ark_angel.analysis.trail import build_trail
    from ark_angel.report.trail import render_trail_json, render_trail_markdown, render_trail_text
    from ark_angel.style import PLAIN, terminal_style

    data = build_trail(store.get_case(case_id).evidence)
    output = {
        "text": lambda: render_trail_text(data, case_id, PLAIN if out_path else terminal_style(), session),
        "md": lambda: render_trail_markdown(data, case_id),
        "json": lambda: render_trail_json(data, case_id),
    }[fmt]()
    if out_path:
        write_out(out_path, output)
        echo(f"Wrote {fmt} trail to {out_path}")
    else:
        echo(output)


# Principal defender

_identifier_options = [
    click.option("--alias", "aliases", multiple=True, help="Another name the principal goes by (repeatable)."),
    click.option("--phone", "phones", multiple=True, help="The principal's phone number (repeatable)."),
    click.option("--email", "emails", multiple=True, help="The principal's email address (repeatable)."),
    click.option("--handle", "handles", multiple=True, help="The principal's social handle (repeatable)."),
    click.option("--plate", "plates", multiple=True, help="A plate on the principal's vehicle (repeatable)."),
]


def _identifier_flags(view):
    for option in reversed(_identifier_options):
        view = option(view)
    return view


def _identifiers(phones: tuple, emails: tuple, handles: tuple, plates: tuple) -> list[tuple[str, str]]:
    return ([("phone", v) for v in phones] + [("email", v) for v in emails] + [("handle", v) for v in handles]
            + [("plate", v) for v in plates])


@cli.group("defend", help="Principal defender: how exposed is the person you protect, and who threatens them?")
def defend_group() -> None:
    """Principal-defender cases.

    A defender case protects a named principal. Feed it what's out there about
    them (people-search listings, their posts and photos, messages they've
    received) and `defend brief` ranks the threats and exposures, each with a fix.
    Ingest, enrich, lead review and reports all work on it like any other case.
    """


@defend_group.command("init")
@click.argument("case_id")
@click.argument("name")
@click.option("--authorized-by", required=True,
              help='Who asked for this protection and how, e.g. "the principal, signed engagement 2026-10-12".')
@click.option("--title", default="", help="Case title (default: \"Protective intel: NAME\").")
@_identifier_flags
@click.pass_obj
def defend_init(store: FileCaseStore, case_id: str, name: str, authorized_by: str, title: str, aliases: tuple,
                phones: tuple, emails: tuple, handles: tuple, plates: tuple) -> None:
    """Open a defender case protecting NAME."""
    case = pipeline.create_defender_case(store, case_id, name, authorized_by, title, aliases,
                                         _identifiers(phones, emails, handles, plates))
    echo(f"Created defender case {case_id!r}: {case.title}")
    echo(f"Next: `ark-angel defend place {case_id} Home LAT LON --alias \"<street address>\"`, "
               f"then ingest what's out there and run `ark-angel defend brief {case_id}`.")


@defend_group.command("add")
@click.argument("case_id")
@_identifier_flags
@click.pass_obj
def defend_add(store: FileCaseStore, case_id: str, aliases: tuple, phones: tuple, emails: tuple, handles: tuple,
               plates: tuple) -> None:
    """Add names or identifiers to the principal's profile."""
    found = _identifiers(phones, emails, handles, plates)
    if not aliases and not found:
        raise click.ClickException("nothing to add: pass --alias, --phone, --email, --handle or --plate")
    pipeline.add_principal_identifiers(store, case_id, aliases, found)
    echo(f"Updated the principal's profile. Run `ark-angel enrich {case_id}` to re-check.")


@defend_group.command("place", context_settings={"ignore_unknown_options": True})
@click.argument("case_id")
@click.argument("name")
@click.argument("latitude", type=float)
@click.argument("longitude", type=float)
@click.option("--alias", "aliases", multiple=True,
              help="How it's written in evidence, e.g. the street address (repeatable).")
@click.option("--radius-m", type=float, default=150.0, show_default=True,
              help="Geotagged evidence this close counts as exposing the place.")
@click.option("--remove", is_flag=True, help="Remove the sensitive place NAME instead (coordinates ignored).")
@click.pass_obj
def defend_place(store: FileCaseStore, case_id: str, name: str, latitude: float, longitude: float, aliases: tuple,
                 radius_m: float, remove: bool) -> None:
    """Add (or replace) a sensitive place: home, work, school, a gym.

    NAME is only a label. Give the street address or building name with
    --alias: that is what's searched for in the evidence. Geotagged evidence
    within --radius-m counts as exposing it too. Sensitive places never go into
    `map` exports or the case gazetteer.
    """
    if remove:
        removed = pipeline.remove_sensitive_place(store, case_id, name)
        echo(f"Removed {name!r}" if removed else f"No sensitive place named {name!r}")
        return
    place = pipeline.add_sensitive_place(store, case_id, Place(name, latitude, longitude, list(aliases), radius_m,
                                                               source="sensitive"))
    echo(f"Added sensitive place {_describe(place)}")
    echo(f"Run `ark-angel enrich {case_id}` to re-check.")


@defend_group.command("show")
@click.argument("case_id")
@click.pass_obj
def defend_show(store: FileCaseStore, case_id: str) -> None:
    """Show the principal's profile: names, identifiers, sensitive places."""
    case = pipeline.defender_case(store, case_id)
    p = case.principal
    echo(f"{case.identifier}: {case.title}")
    echo(f"  principal: {p.name}" + (f" (also {', '.join(p.aliases)})" if p.aliases else ""))
    echo(f"  authorized by: {p.authorized_by}" + (f" on {p.created_at[:10]}" if p.created_at else ""))
    for label, values in (("phones", p.phones), ("emails", p.emails), ("handles", p.handles), ("plates", p.plates)):
        if values:
            echo(f"  {label}: {', '.join(values)}")
    echo("  sensitive places:" if p.places else "  sensitive places: none yet (`ark-angel defend place`)")
    for place in p.places:
        echo(f"    {_describe(place)}")


@defend_group.command("brief")
@click.argument("case_id")
@click.option("--format", "fmt", type=click.Choice(["text", "md", "json"]), default="text", show_default=True)
@click.option("--out", "out_path", type=click.Path(dir_okay=False), help="Write to a file instead of stdout.")
@click.option("--short", is_flag=True, help="Findings and fixes only, no evidence lines (text format).")
@click.option("--all", "show_all", is_flag=True, help="Also list dismissed / remediated leads (text format).")
@click.pass_obj
def defend_brief(store: FileCaseStore, case_id: str, fmt: str, out_path: str, short: bool, show_all: bool) -> None:
    """Risk level, threats and exposures, each with what to do about it.

    Run `ark-angel enrich` first after adding evidence. Mark a fixed exposure
    with `lead dismiss --note "listing removed"`: if it shows up again in new
    evidence, it comes back as RESURFACED.
    """
    from ark_angel.report.defend import build_brief, render_brief_json, render_brief_markdown, render_brief_text
    from ark_angel.style import PLAIN, terminal_style

    data = build_brief(pipeline.defender_case(store, case_id))
    output = {
        "text": lambda: render_brief_text(data, PLAIN if out_path else terminal_style(), reasons=not short,
                                          show_dismissed=show_all),
        "md": lambda: render_brief_markdown(data),
        "json": lambda: render_brief_json(data),
    }[fmt]()
    if out_path:
        write_out(out_path, output)
        echo(f"Wrote {fmt} briefing to {out_path}")
    else:
        echo(output)


@defend_group.command("demo")
@click.option("--case-id", default="DEFEND-DEMO", show_default=True, help="Case id to (re)build the demo under.")
@click.pass_obj
def defend_demo(store: FileCaseStore, case_id: str) -> None:
    """Build a synthetic defender case end to end, offline, and print its briefing."""
    from ark_angel.defend_demo import DEFEND_DEMO_NOTICE, build_defend_demo
    from ark_angel.report.defend import build_brief, render_brief_text
    from ark_angel.style import terminal_style

    style = terminal_style()
    echo(style.heading("ArkAngel principal defender demo")
               + style.dim(f"  {style.dot} everything below is synthetic"))
    echo(style.dim(style.rule * 60))
    case = build_defend_demo(store, store.data_dir.parent / "defend_demo_inputs", case_id,
                             log=lambda line: echo(style.dim(line)))
    echo("")
    echo(render_brief_text(build_brief(case), style, reasons=False))
    echo("")
    echo(style.dim(DEFEND_DEMO_NOTICE))
    echo(style.dim(f"Explore further: ark-angel defend brief {case_id}   |   ark-angel lead list {case_id}"))


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
    echo(f"Created admin {username.strip().lower()!r}. Start the site with `ark-angel web serve`.")


@web_group.command("users")
@click.option("--pending", is_flag=True, help="Only accounts waiting for approval.")
@click.pass_obj
def web_users(obj, pending: bool) -> None:
    """List accounts."""
    db = obj.db
    rows = db.users("pending" if pending else None)
    if not rows:
        echo("No pending accounts." if pending else "No accounts yet. Create one with `ark-angel web create-admin`.")
    for row in rows:
        # Sign-up notes are typed by anonymous visitors: no escape sequences into an admin's terminal.
        note = f"  {printable(row['signup_note'])}" if row["status"] == "pending" and row["signup_note"] else ""
        echo(f"{row['username']:<24} {row['role']:<7} {row['status']:<9}{note}")


@web_group.command("approve")
@click.argument("username")
@click.pass_obj
def web_approve(obj, username: str) -> None:
    """Approve a pending account (or re-enable a disabled one)."""
    db = obj.db
    db.set_status(username, "active")
    echo(f"Approved {username.strip().lower()!r}")


@web_group.command("disable")
@click.argument("username")
@click.pass_obj
def web_disable(obj, username: str) -> None:
    """Disable an account and sign it out everywhere."""
    db = obj.db
    db.set_status(username, "disabled")
    echo(f"Disabled {username.strip().lower()!r}")


@web_group.command("passwd")
@click.argument("username")
@click.pass_obj
def web_passwd(obj, username: str) -> None:
    """Set a password. Every session that user has is signed out."""
    db = obj.db
    db.set_password(username, _new_password("New password"))
    echo(f"Password changed for {username.strip().lower()!r}; all of its sessions were signed out.")


@web_group.command("delete-user")
@click.argument("username")
@click.confirmation_option(prompt="Delete this account and sign out its sessions?")
@click.pass_obj
def web_delete_user(obj, username: str) -> None:
    """Delete an account (refused while it still owns cases)."""
    db = obj.db
    if not db.delete_user(username):
        raise click.ClickException(f"no user {username.strip().lower()!r}")
    echo(f"Deleted {username.strip().lower()!r}")


@web_group.command("reset-2fa")
@click.argument("username")
@click.confirmation_option(prompt="Turn off two-factor sign-in for this account and sign it out everywhere?")
@click.pass_obj
def web_reset_2fa(obj, username: str) -> None:
    """Turn off two-factor sign-in for someone who lost their phone and their recovery codes.

    Check it's really them first (a call, in person): this is exactly what an
    attacker with their password would ask for. They sign in with the password
    and, if the site requires it, set two-factor up again straight away.
    """
    obj.db.reset_totp(username)
    echo(f"Two-factor sign-in is off for {username.strip().lower()!r}, and it's signed out everywhere.")


@web_group.command("import-case")
@click.argument("case_id")
@click.option("--owner", required=True, help="Username that will own the case on the site.")
@click.pass_obj
def web_import_case(obj, case_id: str, owner: str) -> None:
    """Put a case made with the command line onto the site, owned by OWNER. Its data stays where it is."""
    store, db = obj.store, obj.db
    case = store.get_case(case_id)
    db.register_case(case.identifier, owner)
    echo(f"Case {case.identifier!r} is now on the site, owned by {owner.strip().lower()!r}.")


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
    echo(f"Sent a test email to {to} via {config.host}:{config.port} ({config.security}).")


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
        echo(f"Backed up and encrypted to {path}. Only the matching private key can open it.")
    else:
        echo(f"Backed up to {path}. It holds tips and contact details: store it encrypted.")


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
            echo(f"{stamp()} could not email the alert: {mail_exc.__class__.__name__}", err=True)

    try:
        config = backup.BackupConfig.from_env(os.environ)
    except backup.BackupError as exc:
        if once:
            raise
        # Don't crash-loop the container (or block the site): say so loudly, every day, until it's fixed.
        while True:
            echo(f"{stamp()} BACKUPS ARE NOT RUNNING: {exc}", err=True)
            alert("Backups are not set up", f"Nightly backups aren't running: {exc}.\n\nUntil this is fixed, "
                  "tips and evidence on the site have no backup. See the Backups section of docs/deploy.md.\n")
            time.sleep(24 * 60 * 60)

    def run() -> bool:
        try:
            out = backup.run_once(config, obj.store.data_dir, obj.db_path)
        except Exception as exc:  # anything at all: a silent crash-loop is the one failure we can't allow
            if not isinstance(exc, backup.BackupError):
                exc = backup.BackupError(f"{exc.__class__.__name__}: {exc}")
            echo(f"{stamp()} backup FAILED: {exc}", err=True)
            alert("Nightly backup FAILED", f"The backup at {stamp()} failed:\n\n    {exc}\n\nThe site itself is fine, "
                  "but new tips and evidence aren't backed up until this is fixed. Details: docker compose logs backup\n")
            return False
        where = f"copied to {config.dest}" if config.dest else "kept on this server only (no ARK_ANGEL_BACKUP_DEST)"
        echo(f"{stamp()} backup ok: {out.name}, {where}")
        backup.ping(config.ping_url)
        return True

    if not config.dest:
        echo("Warning: no ARK_ANGEL_BACKUP_DEST, so backups stay on this server. If it dies, they die with it.",
                   err=True)
    if once:
        if not run():
            raise SystemExit(1)
        return
    echo(f"Nightly backups at {config.at} UTC" + (f" to {config.dest}" if config.dest else "") +
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
        ensure_safe_target(base, "restore a backup")
        base.mkdir(parents=True, exist_ok=True)
        # Written, closed, then read back: Windows can't reopen a NamedTemporaryFile that is still open.
        fd, spool = tempfile.mkstemp(dir=base, prefix=".restore-", suffix=".tar.gz")
        try:
            with os.fdopen(fd, "wb") as out:
                shutil.copyfileobj(sys.stdin.buffer, out)
            moved = backup.restore(Path(spool), obj.store.data_dir, obj.db_path, force=force)
        finally:
            Path(spool).unlink(missing_ok=True)
        echo("Restored the backup from standard input." + (f" The previous data is in {moved}." if moved else ""))
        echo("Start the site again to use it.")
        return
    if archive.endswith(".age"):
        raise click.ClickException("decrypt it first on your own computer: age -d -i KEYFILE -o backup.tar.gz "
                                   f"{Path(archive).name}")
    moved = backup.restore(Path(archive), obj.store.data_dir, obj.db_path, force=force)
    echo(f"Restored {Path(archive).name}." + (f" The previous data is in {moved}." if moved else ""))
    echo("Start the site again to use it.")


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
@click.option("--require-2fa", "require_2fa", type=click.Choice(["admins", "everyone", "off"]), default="admins",
              show_default=True, envvar="ARK_ANGEL_REQUIRE_2FA",
              help="Who must set up two-factor sign-in before using the site (or $ARK_ANGEL_REQUIRE_2FA).")
@click.option("--wait-for-admin", is_flag=True,
              help="With no admin yet, wait for one to be created instead of exiting (used by the Docker image).")
@click.pass_obj
def web_serve(obj, host: str, port: int, allow_hosts: tuple, behind_proxy: bool, proxy_ip: str, site_name: str,
              threads: int, require_2fa: str, wait_for_admin: bool) -> None:
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
        echo("No admin yet. Create one with: docker compose run --rm app ark-angel web create-admin NAME")
        while not db.has_admin():
            time.sleep(3)
        echo("Admin found, starting.")
    if host not in LOCAL_HOSTS and not behind_proxy:
        raise click.ClickException(
            f"listening on {host} would send passwords over plain HTTP to your network. Put the site behind an "
            "HTTPS reverse proxy and pass --behind-proxy and --allow-host, or keep the default 127.0.0.1.")
    if behind_proxy and not allow_hosts:
        raise click.ClickException("--behind-proxy needs --allow-host with the name the proxy serves it at")
    from ark_angel.web.notify import mailer_from_env

    mailer = mailer_from_env(os.environ)
    app = create_app(store, db, allowed_hosts=(*LOCAL_HOSTS, *allow_hosts), secure_cookies=behind_proxy,
                     site_name=site_name, mailer=mailer, require_2fa=require_2fa)
    shown = f"[{host}]" if ":" in host else host
    echo(f"{site_name} website on http://{shown}:{port}/" + (" (behind your HTTPS proxy)" if behind_proxy else ""))
    echo(f"Email notifications: on, via {mailer.config.host}:{mailer.config.port}" if mailer.enabled
               else "Email notifications: off (set ARK_ANGEL_SMTP_HOST to turn them on)")
    echo({"admins": "Two-factor sign-in: required for admins",
                "everyone": "Two-factor sign-in: required for everyone",
                "off": "Two-factor sign-in: optional (--require-2fa off)"}[require_2fa])
    echo("Ctrl+C to stop.")
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
    echo(style.heading("ArkAngel demo") + style.dim(f"  {style.dot} everything below is synthetic"))
    echo(style.dim(style.rule * 60))
    workdir = store.data_dir.parent / "demo_inputs"
    case = build_demo_case(store, workdir, case_id, log=lambda line: echo(style.dim(line)))
    data = build_report(case)

    echo("")
    for line in case_header(data, style):
        echo(line)
    top = top_score(data)
    verified = [lead for lead in data["leads"] if lead["status"] == "verified"]
    open_leads = [lead for lead in data["leads"] if lead["status"] == "open"]
    if verified:
        echo("\n" + style.heading("Verified"))
        for lead in verified:
            for line in lead_lines(lead, top, style, reasons=1):
                echo(line)
    echo("\n" + style.heading("Top open leads"))
    for lead in open_leads[:5]:
        for line in lead_lines(lead, top, style, reasons=2):
            echo(line)

    write_out(out_path, render_html(data, notice=DEMO_NOTICE))
    echo("\n" + style.accent(f"Wrote HTML case file to {out_path}")
               + style.dim(" (open it in a browser; prints cleanly)"))
    echo(style.dim(f"Explore further: ark-angel lead list {case_id}   |   ark-angel report {case_id} --all"))


if __name__ == "__main__":
    cli()
