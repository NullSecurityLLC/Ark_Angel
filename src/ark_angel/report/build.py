"""Assemble a case into a plain-data report that every renderer shares."""
from __future__ import annotations

from typing import Any, Optional

from ark_angel.analysis.correlate import CORRELATED_PREFIXES, analyze_case
from ark_angel.models import Case, Review
from ark_angel.review import RankedLead, lead_score, ranked_leads

__all__ = ["build_report", "lead_score"]


def _review_dict(review: Optional[Review]) -> Optional[dict[str, Any]]:
    if review is None:
        return None
    return {"status": review.status, "note": review.note, "reviewer": review.reviewer,
            "reviewed_at": review.reviewed_at}


def _lead_dict(item: RankedLead) -> dict[str, Any]:
    lead, state = item.lead, item.state
    return {
        "rank": item.rank,
        "identifier": lead.identifier,
        "summary": lead.summary,
        "score": round(lead_score(lead), 2),
        "reasons": list(lead.reasons),
        "evidence_ids": list(lead.evidence_ids),
        "source": lead.source,
        "correlated": lead.identifier.startswith(CORRELATED_PREFIXES),
        "status": state.status,
        "resurfaced": state.resurfaced,
        "new_evidence_ids": list(state.new_evidence_ids),
        "review": _review_dict(state.review),
    }


def build_report(case: Case) -> dict[str, Any]:
    """Return leads in review order plus the entity index, geo clusters and timeline."""
    analysis = analyze_case(case.evidence, places=case.places)
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
        "leads": [_lead_dict(item) for item in ranked_leads(case)],
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
