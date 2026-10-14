"""Local file :class:`IngestionSource`: one lead per non-blank line."""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ark_angel.ingest.base import IngestionSource
from ark_angel.models import Lead


def read_text_file(path: Path) -> str:
    """Read UTF-8 (with or without a BOM), falling back to cp1252 for files from old Windows editors."""
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


class FileIngestionSource(IngestionSource):
    """Reads a local text file and produces one lead per non-blank line."""

    def fetch_leads(self, identifier: str) -> Sequence[Lead]:
        path = Path(identifier)
        lines = (line.strip() for line in read_text_file(path).splitlines())
        return [
            Lead(identifier=f"{path.name}:{index}", summary=line)
            for index, line in enumerate(lines, start=1)
            if line
        ]
