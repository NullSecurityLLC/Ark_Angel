"""Local file :class:`IngestionSource`: one lead per non-blank line."""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ark_angel.ingest.base import IngestionSource
from ark_angel.models import Lead
from ark_angel.registry import ingestion_sources


@ingestion_sources.register("file")
class FileIngestionSource(IngestionSource):
    """Reads a local text file and produces one lead per non-blank line."""

    def fetch_leads(self, identifier: str) -> Sequence[Lead]:
        path = Path(identifier)
        lines = (line.strip() for line in path.read_text().splitlines())
        return [
            Lead(identifier=f"{path.name}:{index}", summary=line)
            for index, line in enumerate(lines, start=1)
            if line
        ]
