from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from ark_angel.models import Case, Evidence, Lead


class CaseStore(ABC):
    """Persists and retrieves cases along with their related data."""

    @abstractmethod
    def create_case(self, case: Case) -> Case:
        """Create a new case record.

        Args:
            case: Case object containing a unique identifier and initial data.

        Returns:
            The stored case, potentially enriched with persistence metadata.
        """

        raise NotImplementedError

    @abstractmethod
    def add_leads(self, case_id: str, leads: Iterable[Lead]) -> None:
        """Associate leads with an existing case.

        Args:
            case_id: Identifier of the case to update.
            leads: Leads to attach. Implementations should ensure the case
                exists before associating the leads.
        """

        raise NotImplementedError

    @abstractmethod
    def add_evidence(self, case_id: str, evidence: Iterable[Evidence]) -> None:
        """Attach evidence items to a case.

        Args:
            case_id: Identifier of the case to update.
            evidence: Evidence items to store alongside the case.
        """

        raise NotImplementedError

    @abstractmethod
    def get_case(self, case_id: str) -> Case:
        """Retrieve a case with all associated data.

        Args:
            case_id: Identifier for the requested case.

        Returns:
            The case object with any persisted leads and evidence. A
            ``KeyError`` should be raised if the case is unknown.
        """

        raise NotImplementedError
