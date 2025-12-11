from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from ark_angel.models import Case, Evidence, Lead


class Analyzer(ABC):
    """Processes evidence to generate insights or new leads."""

    @abstractmethod
    def analyze_evidence(self, case: Case, evidence_items: Sequence[Evidence]) -> Sequence[Lead]:
        """Derive leads from evidence within a case context.

        Args:
            case: The case the evidence belongs to, including existing leads for
                additional context.
            evidence_items: Evidence to analyze. Items should reference unique
                identifiers and may include metadata.

        Returns:
            A sequence of leads generated or updated based on the evidence. Each
            lead should include an identifier and link back to supporting
            evidence via ``evidence_ids`` when possible.
        """

        raise NotImplementedError

    @abstractmethod
    def score_lead(self, lead: Lead) -> float:
        """Score a lead for prioritization.

        Args:
            lead: Lead to score.

        Returns:
            A numeric score representing lead relevance. Higher is more
            important. Implementations should define the scoring semantics and
            return a deterministic value for the same input.
        """

        raise NotImplementedError
