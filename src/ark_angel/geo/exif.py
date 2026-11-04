"""Shared EXIF helpers: loading tags and converting GPS coordinates."""
from __future__ import annotations

import math
from typing import Optional


def load_exif(image_path) -> dict:
    """Return the piexif tag dict for an image path or file-like object, or ``{}`` if it has none."""
    try:
        import piexif
        from PIL import Image

        with Image.open(image_path) as img:
            raw = img.info.get("exif", b"")
        if not raw:
            return {}
        return piexif.load(raw)
    except Exception:
        return {}


def load_exif_bytes(data: bytes) -> dict:
    """:func:`load_exif` for image bytes held in memory, e.g. an email attachment."""
    import io

    return load_exif(io.BytesIO(data))


def exif_metadata(exif: dict) -> dict:
    """The fields correlation uses: ``latitude``/``longitude``, ``timestamp``, ``device``."""
    metadata: dict = {}
    if not exif:
        return metadata
    coords = gps_from_exif(exif)
    if coords:
        metadata["latitude"], metadata["longitude"] = coords
    timestamp = timestamp_from_exif(exif)
    if timestamp:
        metadata["timestamp"] = timestamp
    device = device_from_exif(exif)
    if device:
        metadata["device"] = device
    return metadata


def dms_to_decimal(dms: tuple, ref: str) -> float:
    """Convert EXIF degree/minute/second rationals to signed decimal degrees.

    Raises:
        ValueError: anything that isn't three well-formed rationals (EXIF comes from strangers' photos).
    """
    try:
        d = dms[0][0] / dms[0][1]
        m = dms[1][0] / dms[1][1] / 60
        s = dms[2][0] / dms[2][1] / 3600
    except (ZeroDivisionError, TypeError, IndexError) as exc:
        raise ValueError(f"malformed GPS rational: {exc.__class__.__name__}") from None
    val = d + m + s
    return -val if ref in ("S", "W") else val


def decimal_to_dms(value: float) -> tuple:
    """Convert decimal degrees to EXIF degree/minute/second rationals."""
    value = abs(value)
    degrees = int(value)
    minutes_full = (value - degrees) * 60
    minutes = int(minutes_full)
    seconds = round((minutes_full - minutes) * 60 * 100)
    return ((degrees, 1), (minutes, 1), (seconds, 100))


MAX_TAG_CHARS = 100  # Make, Model and timestamps are short; a 64 KB "camera model" is someone being clever


def _text(value) -> str:
    if isinstance(value, bytes):
        value = value.decode(errors="replace")
    text = str(value).strip("\x00").strip() if value else ""
    # Control characters can't be in a real camera name, and would reach terminals and reports.
    return "".join(ch for ch in text if ch.isprintable())[:MAX_TAG_CHARS]


def gps_from_exif(exif: dict) -> Optional[tuple[float, float]]:
    """Return ``(latitude, longitude)`` from EXIF tags, or ``None``."""
    import piexif

    gps = exif.get("GPS") or {}
    lat_data = gps.get(piexif.GPSIFD.GPSLatitude)
    lat_ref = gps.get(piexif.GPSIFD.GPSLatitudeRef)
    lon_data = gps.get(piexif.GPSIFD.GPSLongitude)
    lon_ref = gps.get(piexif.GPSIFD.GPSLongitudeRef)
    if not (lat_data and lon_data and lat_ref and lon_ref):
        return None
    try:
        lat, lon = dms_to_decimal(lat_data, _text(lat_ref)), dms_to_decimal(lon_data, _text(lon_ref))
    except ValueError:
        return None  # a broken GPS block is no GPS, not a crash (photos arrive from anonymous tippers)
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def device_from_exif(exif: dict) -> str:
    """Return ``"Make Model"`` from EXIF tags, or ``""``."""
    import piexif

    ifd0 = exif.get("0th") or {}
    make = _text(ifd0.get(piexif.ImageIFD.Make, b""))
    model = _text(ifd0.get(piexif.ImageIFD.Model, b""))
    return f"{make} {model}".strip()


def timestamp_from_exif(exif: dict) -> str:
    """Return the raw ``DateTimeOriginal`` string (``YYYY:MM:DD HH:MM:SS``), or ``""``."""
    import piexif

    exif_ifd = exif.get("Exif") or {}
    return _text(exif_ifd.get(piexif.ExifIFD.DateTimeOriginal, b""))
