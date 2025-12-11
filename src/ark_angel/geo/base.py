from __future__ import annotations

from abc import ABC, abstractmethod

from ark_angel.models import Location


class GeoResolver(ABC):
    """Resolves unstructured location hints into structured coordinates."""

    @abstractmethod
    def resolve(self, query: str) -> Location:
        """Resolve a location string into a :class:`~ark_angel.models.Location`.

        Args:
            query: Free-form text such as an address, landmark name, or GPS
                reference.

        Returns:
            A fully populated location object with coordinates and, when
            available, a country code. Implementations should raise
            ``ValueError`` when the query cannot be resolved.
        """

        raise NotImplementedError
