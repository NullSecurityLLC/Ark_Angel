"""Rule-based :class:`Analyzer`: flags evidence containing contact info."""
from __future__ import annotations

import re
from typing import Sequence

from ark_angel.analysis.base import Analyzer
from ark_angel.models import Case, Evidence, Lead
from ark_angel.registry import analyzers

EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_PATTERN = re.compile(r"\+?\d[\d\-.\s]{7,}\d")

_TAG_WEIGHTS = {"email": 2.0, "phone": 1.0}


@analyzers.register("rules")
class RuleBasedAnalyzer(Analyzer):
    """Tags evidence containing emails or phone numbers, generating leads."""

    def analyze_evidence(self, case: Case, evidence_items: Sequence[Evidence]) -> Sequence[Lead]:
        leads: list[Lead] = []
        for item in evidence_items:
            text = str(item.content)
            tags = [tag for tag, pattern in self._patterns() if pattern.search(text)]
            if tags:
                leads.append(
                    Lead(
                        identifier=f"tag-{item.identifier}",
                        summary=f"{', '.join(tags)} present in {item.identifier}",
                        evidence_ids=[item.identifier],
                    )
                )
        return leads

    def score_lead(self, lead: Lead) -> float:
        return sum(weight for tag, weight in _TAG_WEIGHTS.items() if tag in lead.summary)

    @staticmethod
    def _patterns():
        return (("email", EMAIL_PATTERN), ("phone", PHONE_PATTERN))
