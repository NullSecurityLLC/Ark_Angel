"""Rule-based :class:`Analyzer`: flags evidence containing contact details."""
from __future__ import annotations

from typing import Sequence

from ark_angel.analysis.base import Analyzer
from ark_angel.analysis.extract import extract_entities
from ark_angel.models import Case, Evidence, Lead

_TAG_WEIGHTS = {"email": 2.0, "phone": 1.0, "plate": 1.0, "handle": 0.5}


class RuleBasedAnalyzer(Analyzer):
    """Tags each evidence item that contains contact-type entities.

    This is the per-item pass; cross-source matching lives in
    :mod:`ark_angel.analysis.correlate`.
    """

    def analyze_evidence(self, case: Case, evidence_items: Sequence[Evidence]) -> Sequence[Lead]:
        leads: list[Lead] = []
        for item in evidence_items:
            entities = [e for e in extract_entities(str(item.content)) if e.kind in _TAG_WEIGHTS]
            if not entities:
                continue
            tags = list(dict.fromkeys(e.kind for e in entities))
            lead = Lead(
                identifier=f"tag-{item.identifier}",
                summary=f"{', '.join(tags)} present in {item.identifier}: "
                        f"{', '.join(e.value for e in entities)}",
                evidence_ids=[item.identifier],
                entities=entities,
                source="analysis",
            )
            lead.score = self.score_lead(lead)
            leads.append(lead)
        return leads

    def score_lead(self, lead: Lead) -> float:
        if lead.entities:
            kinds = {e.kind for e in lead.entities}
            return sum(weight for tag, weight in _TAG_WEIGHTS.items() if tag in kinds)
        return lead.score or sum(weight for tag, weight in _TAG_WEIGHTS.items() if tag in lead.summary)
