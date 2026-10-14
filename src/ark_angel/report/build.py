"""Assemble a case into a plain-data report that every renderer shares."""
from __future__ import annotations

from typing import Any

from ark_angel.analysis.correlate import CORRELATED_PREFIXES, analyze_case
from ark_angel.analysis.rules import RuleBasedAnalyzer
from ark_angel.models import Case, Lead

_rules = RuleBasedAnalyzer()


def lead_score(lead: Lead) -> float:
    """A lead's stored score, falling back to rule weights for older case files."""
    return lead.score or _rules.score_lead(lead)


def build_report(case: Case) -> dict[str, Any]:
    """Return ranked leads plus the entity index, geo clusters and timeline."""
    analysis = analyze_case(case.evidence, places=case.places)
    leads = sorted(case.leads, key=lead_score, reverse=True)
    return {
        "case": {
            "identifier": case.identifier,
            "title": case.title,
            "created_at": case.created_at.isoformat(),
            "evidence_count": len(case.evidence),
            "lead_count": len(case.leads),
            "sources": sorted({(item.metadata or {}).get("source") or item.identifier.split(":", 1)[0]
                               for item in case.evidence}),
        },
        "leads": [
            {
                "identifier": lead.identifier,
                "summary": lead.summary,
                "score": round(lead_score(lead), 2),
                "reasons": list(lead.reasons),
                "evidence_ids": list(lead.evidence_ids),
                "source": lead.source,
                "correlated": lead.identifier.startswith(CORRELATED_PREFIXES),
            }
            for lead in leads
        ],
        "entities": [
            {
                "kind": kind,
                "value": value,
                "mentions": [{"evidence_id": m.evidence_id, "source": m.source, "excerpt": m.excerpt}
                             for m in mentions],
            }
            for (kind, value), mentions in sorted(
                analysis.entity_index.items(), key=lambda kv: (-len(kv[1]), kv[0])
            )
        ],
        "clusters": [
            {
                "latitude": round(cluster.latitude, 6),
                "longitude": round(cluster.longitude, 6),
                "radius_m": round(cluster.radius_km * 1000),
                "places": cluster.place_names,
                "points": [{"evidence_id": p.evidence_id, "source": p.source, "latitude": p.latitude,
                            "longitude": p.longitude, "label": p.label, "place": p.place}
                           for p in cluster.points],
            }
            for cluster in analysis.clusters
        ],
        "places": [
            {"name": p.name, "latitude": p.latitude, "longitude": p.longitude, "aliases": list(p.aliases),
             "radius_m": p.radius_m, "source": p.source}
            for p in case.places
        ],
        "timeline": [
            {"when": event.when.isoformat(sep=" ", timespec="minutes"), "evidence_id": event.evidence_id,
             "source": event.source, "text": event.text}
            for event in analysis.timeline
        ],
    }
