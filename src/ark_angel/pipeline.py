"""Ingest/enrich steps shared by the CLI commands and the demo."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Optional

from ark_angel.analysis.correlate import CaseAnalysis, analyze_case
from ark_angel.geo.builtin import Resolution
from ark_angel.analysis.rules import RuleBasedAnalyzer
from ark_angel.cases.store import FileCaseStore
from ark_angel.ingest.csv_source import CsvImport, CsvTipSource
from ark_angel.ingest.file_source import FileIngestionSource
from ark_angel.ingest.image_source import ImageIngestionSource
from ark_angel.ingest.username_source import UsernameHit, hit_records
from ark_angel.models import Evidence, Lead, Place, Review
from ark_angel.review import RankedLead, make_review, resolve_lead


def ingest_text(store: FileCaseStore, case_id: str, path: str | Path) -> list[Evidence]:
    """Add each non-blank line of a text file as an evidence item."""
    path = Path(path)
    evidence = [
        Evidence(identifier=lead.identifier, type="text", content=lead.summary, metadata={"source": path.stem})
        for lead in FileIngestionSource().fetch_leads(str(path))
    ]
    store.add_evidence(case_id, evidence)
    return evidence


def ingest_image(store: FileCaseStore, case_id: str, path: str | Path) -> tuple[Optional[Evidence], list[Lead]]:
    """Add an image's EXIF findings (GPS, device, timestamp) to a case."""
    evidence, leads = ImageIngestionSource().ingest(str(path))
    if evidence is not None:
        store.add_evidence(case_id, [evidence])
        store.add_leads(case_id, leads)
    return evidence, leads


def ingest_username_hits(store: FileCaseStore, case_id: str, hits: Iterable[UsernameHit],
                         simulated: bool = False) -> list[Lead]:
    records = [hit_records(hit, simulated=simulated) for hit in hits]
    store.add_evidence(case_id, [evidence for evidence, _ in records])
    store.add_leads(case_id, [lead for _, lead in records])
    return [lead for _, lead in records]


def enrich(store: FileCaseStore, case_id: str) -> tuple[list[Lead], CaseAnalysis]:
    """Re-run tagging and correlation, replacing earlier analysis leads.

    Ingest-time leads (EXIF, username hits) are kept; analysis leads are
    recomputed from scratch so running this twice never duplicates anything.
    """
    case = store.get_case(case_id)
    analysis = analyze_case(case.evidence, places=case.places)
    new_leads = analysis.leads + list(RuleBasedAnalyzer().analyze_evidence(case, case.evidence))
    case.leads = [lead for lead in case.leads if lead.source != "analysis"] + new_leads
    store.save_case(case)
    return new_leads, analysis


def handles_in_case(store: FileCaseStore, case_id: str) -> list[str]:
    """Every distinct handle extracted from a case's evidence, most-mentioned first."""
    analysis = analyze_case(store.get_case(case_id).evidence)
    handles = [(len(mentions), value) for (kind, value), mentions in analysis.entity_index.items()
               if kind == "handle"]
    return [value for _, value in sorted(handles, key=lambda item: (-item[0], item[1]))]


def _validate_place(place: Place) -> Place:
    if not place.name.strip():
        raise ValueError("place name must not be empty")
    if not (-90 <= place.latitude <= 90 and -180 <= place.longitude <= 180):
        raise ValueError(f"coordinates out of range for {place.name!r}: {place.latitude}, {place.longitude}")
    if place.radius_m <= 0:
        raise ValueError(f"radius must be positive for {place.name!r}")
    return place


def add_places(store: FileCaseStore, case_id: str, places: Iterable[Place]) -> list[Place]:
    """Add places to a case gazetteer, replacing any existing entry with the same name."""
    case = store.get_case(case_id)
    incoming = [_validate_place(place) for place in places]
    names = {place.name.lower() for place in incoming}
    case.places = [p for p in case.places if p.name.lower() not in names] + incoming
    store.save_case(case)
    return incoming


def remove_place(store: FileCaseStore, case_id: str, name: str) -> bool:
    case = store.get_case(case_id)
    kept = [p for p in case.places if p.name.lower() != name.lower()]
    removed = len(kept) != len(case.places)
    case.places = kept
    store.save_case(case)
    return removed


def read_places_csv(path: str | Path) -> list[Place]:
    """Read a gazetteer CSV with columns ``name,latitude,longitude[,aliases][,radius_m]``.

    Aliases are separated by ``;``.
    """
    places = []
    with open(path, newline="") as handle:
        for line, row in enumerate(csv.DictReader(handle), start=2):
            try:
                places.append(Place(
                    name=row["name"].strip(),
                    latitude=float(row["latitude"]),
                    longitude=float(row["longitude"]),
                    aliases=[a.strip() for a in (row.get("aliases") or "").split(";") if a.strip()],
                    radius_m=float(row.get("radius_m") or 250),
                    source="import",
                ))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"{path}:{line}: bad gazetteer row ({exc})") from exc
    return places


def review_lead(store: FileCaseStore, case_id: str, ref: str, status: str, note: str = "",
                reviewer: str = "") -> tuple[RankedLead, Review]:
    """Record a verify/dismiss/reopen decision on the lead ``ref`` points at."""
    case = store.get_case(case_id)
    item = resolve_lead(case, ref)
    review = make_review(item.lead, status, note, reviewer)
    case.reviews.append(review)
    store.save_case(case)
    return item, review


def ingest_csv(store: FileCaseStore, case_id: str, path: str | Path, source: CsvTipSource,
               dry_run: bool = False) -> CsvImport:
    """Add each row of a tip-line export as an evidence item (or just preview with ``dry_run``)."""
    store.get_case(case_id)
    result = source.load(path)
    if not dry_run:
        store.add_evidence(case_id, result.evidence)
    return result


def resolve_unmatched_places(store: FileCaseStore, case_id: str, states: Iterable[str] = ()) -> list[Resolution]:
    """Resolve the place names ``place suggest`` finds against the built-in US gazetteer.

    State hints written next to a name in the evidence ("Boulder, CO") beat
    ``states``, the case's region.
    """
    from ark_angel.analysis.places import suggest_places
    from ark_angel.geo.builtin import normalize_states, resolve, state_hints

    case = store.get_case(case_id)
    region = normalize_states(states)
    texts = {item.identifier: str(item.content) for item in case.evidence}
    resolutions = []
    for suggestion in suggest_places(case.evidence, case.places):
        hints = state_hints(suggestion.name, (texts[eid] for eid in suggestion.evidence_ids))
        resolutions.append(resolve(suggestion.name, hints, region))
    return resolutions


def place_from_builtin(name: str, states: Iterable[str] = ()) -> Place:
    """A place for ``name`` from the built-in gazetteer; it must resolve unambiguously."""
    from ark_angel.geo.builtin import normalize_states, resolve

    resolution = resolve(name, default_states=normalize_states(states))
    if resolution.status != "resolved":
        options = "; ".join(entry.label for entry in resolution.candidates[:5])
        hint = f" Candidates: {options}. Narrow it with --state." if options else ""
        raise ValueError(f"can't place {name!r} from the built-in gazetteer: {resolution.why}.{hint}")
    return resolution.entry.to_place(name)
