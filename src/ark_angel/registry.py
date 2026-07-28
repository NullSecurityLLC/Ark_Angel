"""Generic plugin registry for Ark Angel tool implementations.

Lets ingestion sources, analyzers, and geo resolvers be registered under a
name and looked up dynamically, so a contributor can add a new tool without
modifying core CLI or pipeline code (see docs/roadmap.md, Phase 2).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Dict, Generic, List, TypeVar

if TYPE_CHECKING:
    from ark_angel.analysis.base import Analyzer
    from ark_angel.geo.base import GeoResolver
    from ark_angel.ingest.base import IngestionSource

T = TypeVar("T")


class Registry(Generic[T]):
    """Maps string names to plugin classes of a given base type."""

    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._entries: Dict[str, type] = {}

    def register(self, name: str) -> Callable[[type], type]:
        """Class decorator that registers a plugin implementation under ``name``."""

        def decorator(cls: type) -> type:
            if name in self._entries:
                raise ValueError(f"{self._kind} {name!r} is already registered")
            self._entries[name] = cls
            return cls

        return decorator

    def get(self, name: str) -> type:
        try:
            return self._entries[name]
        except KeyError:
            raise KeyError(f"unknown {self._kind}: {name!r}") from None

    def create(self, name: str, *args, **kwargs) -> T:
        return self.get(name)(*args, **kwargs)

    def names(self) -> List[str]:
        return sorted(self._entries)


ingestion_sources: "Registry[IngestionSource]" = Registry("ingestion source")
analyzers: "Registry[Analyzer]" = Registry("analyzer")
geo_resolvers: "Registry[GeoResolver]" = Registry("geo resolver")
