from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, Sequence

from ark_angel.models import Lead


class IngestionSource(ABC):
    """Ingests external data and produces leads.

    Implementations are expected to connect to third-party systems or files,
    pull raw items, and translate them into :class:`~ark_angel.models.Lead`
    objects. Each lead should reference identifiers meaningful to the source,
    such as a record ID or URL.
    """

    @abstractmethod
    def fetch_leads(self, identifier: str) -> Sequence[Lead]:
        """Retrieve leads associated with a source-specific identifier.

        Args:
            identifier: A unique value understood by the source (case ID,
                search term, account name, etc.).

        Returns:
            A sequence of leads created from the retrieved records. Implementers
            should ensure identifiers and summaries are populated and may link
            to supporting evidence by ID.
        """

        raise NotImplementedError

    def ingest_all(self, identifiers: Iterable[str]) -> list[Lead]:
        """Default helper that aggregates leads for multiple identifiers.

        Args:
            identifiers: Iterable of identifiers to fetch.

        Returns:
            A combined list of leads from all identifiers in the order they are
            processed.
        """

        leads: list[Lead] = []
        for value in identifiers:
            leads.extend(self.fetch_leads(value))
        return leads
