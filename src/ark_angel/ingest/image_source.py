"""Ingest image files and extract EXIF metadata as leads."""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ark_angel.geo.exif import device_from_exif, gps_from_exif, timestamp_from_exif
from ark_angel.geo.exif import dms_to_decimal as _dms_to_decimal  # noqa: F401  (re-exported)
from ark_angel.geo.exif import load_exif as _load_exif
from ark_angel.ingest.base import IngestionSource
from ark_angel.models import Evidence, Lead

IMAGE_LEAD_SCORE = 1.0


class ImageIngestionSource(IngestionSource):
    """Extracts EXIF metadata from an image file and generates leads."""

    def fetch_leads(self, identifier: str) -> Sequence[Lead]:
        return self.ingest(identifier)[1]

    def ingest(self, image_path: str) -> tuple[Evidence | None, list[Lead]]:
        """Return one evidence item for the image plus a lead per EXIF finding.

        The evidence carries ``latitude``/``longitude``/``timestamp``/``device``
        in its metadata so the correlation engine can cluster and order it.
        Returns ``(None, [])`` when the image has no EXIF data.
        """
        exif = _load_exif(image_path)
        if not exif:
            return None, []

        name = Path(image_path).name
        evidence_id = f"image:{name}"
        metadata: dict = {"source": "photos", "path": str(image_path)}
        leads: list[Lead] = []

        def add(kind: str, summary: str) -> None:
            leads.append(
                Lead(
                    identifier=f"exif-{kind}-{name}",
                    summary=summary,
                    evidence_ids=[evidence_id],
                    score=IMAGE_LEAD_SCORE,
                )
            )

        coords = gps_from_exif(exif)
        if coords:
            lat, lon = coords
            metadata.update(latitude=lat, longitude=lon)
            add("gps", f"GPS coordinates in {name}: {lat:.6f}, {lon:.6f}")

        device = device_from_exif(exif)
        if device:
            metadata["device"] = device
            add("device", f"Device in {name}: {device}")

        timestamp = timestamp_from_exif(exif)
        if timestamp:
            metadata["timestamp"] = timestamp
            add("timestamp", f"Capture timestamp in {name}: {timestamp}")

        if not leads:
            return None, []
        evidence = Evidence(
            identifier=evidence_id,
            type="image",
            content="; ".join(lead.summary for lead in leads),
            metadata=metadata,
        )
        return evidence, leads
