"""Ingest/enrich steps shared by the CLI commands and the demo."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from ark_angel.analysis.correlate import CaseAnalysis, analyze_case
from ark_angel.analysis.rules import RuleBasedAnalyzer
from ark_angel.cases.store import FileCaseStore
from ark_angel.ingest.file_source import FileIngestionSource
from ark_angel.ingest.image_source import ImageIngestionSource
from ark_angel.ingest.username_source import UsernameHit, hit_records
from ark_angel.models import Evidence, Lead


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
    analysis = analyze_case(case.evidence)
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
