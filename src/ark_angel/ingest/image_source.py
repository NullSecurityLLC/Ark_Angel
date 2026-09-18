"""Ingest image files and extract EXIF metadata as leads."""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ark_angel.ingest.base import IngestionSource
from ark_angel.models import Lead


def _load_exif(image_path: str) -> dict:
    try:
        import piexif
        from PIL import Image

        img = Image.open(image_path)
        raw = img.info.get("exif", b"")
        if not raw:
            return {}
        return piexif.load(raw)
    except Exception:
        return {}


def _dms_to_decimal(dms: tuple, ref: str) -> float:
    d = dms[0][0] / dms[0][1]
    m = dms[1][0] / dms[1][1] / 60
    s = dms[2][0] / dms[2][1] / 3600
    val = d + m + s
    return -val if ref in ("S", "W") else val


class ImageIngestionSource(IngestionSource):
    """Extracts EXIF metadata from an image file and generates leads."""

    def fetch_leads(self, identifier: str) -> Sequence[Lead]:
        exif = _load_exif(identifier)
        if not exif:
            return []

        leads: list[Lead] = []
        stem = Path(identifier).name

        gps = exif.get("GPS", {})
        if gps:
            import piexif

            lat_data = gps.get(piexif.GPSIFD.GPSLatitude)
            lat_ref = gps.get(piexif.GPSIFD.GPSLatitudeRef)
            lon_data = gps.get(piexif.GPSIFD.GPSLongitude)
            lon_ref = gps.get(piexif.GPSIFD.GPSLongitudeRef)

            if lat_data and lon_data and lat_ref and lon_ref:
                lat_ref_s = lat_ref.decode() if isinstance(lat_ref, bytes) else lat_ref
                lon_ref_s = lon_ref.decode() if isinstance(lon_ref, bytes) else lon_ref
                lat = _dms_to_decimal(lat_data, lat_ref_s)
                lon = _dms_to_decimal(lon_data, lon_ref_s)
                leads.append(
                    Lead(
                        identifier=f"exif-gps-{stem}",
                        summary=f"GPS coordinates in {stem}: {lat:.6f}, {lon:.6f}",
                    )
                )

        ifd0 = exif.get("0th", {})
        if ifd0:
            import piexif

            make = ifd0.get(piexif.ImageIFD.Make, b"")
            model_val = ifd0.get(piexif.ImageIFD.Model, b"")
            make_s = make.decode(errors="replace").strip("\x00") if isinstance(make, bytes) else str(make)
            model_s = model_val.decode(errors="replace").strip("\x00") if isinstance(model_val, bytes) else str(model_val)
            if make_s or model_s:
                leads.append(
                    Lead(
                        identifier=f"exif-device-{stem}",
                        summary=f"Device in {stem}: {(make_s + ' ' + model_s).strip()}",
                    )
                )

        exif_ifd = exif.get("Exif", {})
        if exif_ifd:
            import piexif

            dt = exif_ifd.get(piexif.ExifIFD.DateTimeOriginal, b"")
            dt_s = dt.decode(errors="replace").strip("\x00") if isinstance(dt, bytes) else str(dt)
            if dt_s:
                leads.append(
                    Lead(
                        identifier=f"exif-timestamp-{stem}",
                        summary=f"Capture timestamp in {stem}: {dt_s}",
                    )
                )

        return leads
