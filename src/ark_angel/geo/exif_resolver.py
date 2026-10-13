"""GeoResolver backed by image EXIF GPS tags."""
from __future__ import annotations

from pathlib import Path

from ark_angel.geo.base import GeoResolver
from ark_angel.geo.exif import gps_from_exif, load_exif
from ark_angel.models import Location


class ExifGeoResolver(GeoResolver):
    """Resolves a GPS location directly from an image file's EXIF data."""

    def resolve(self, query: str) -> Location:
        """Extract GPS coordinates from the EXIF tags of an image file.

        Args:
            query: Path to the image file.

        Raises:
            ValueError: If the file is missing or has no GPS EXIF data.
        """
        if not Path(query).is_file():
            raise ValueError(f"no such image: {query!r}")
        coords = gps_from_exif(load_exif(query))
        if coords is None:
            raise ValueError(f"no GPS coordinates in EXIF of {query!r}")
        lat, lon = coords
        return Location(name=f"{lat:.6f}, {lon:.6f}", latitude=lat, longitude=lon)
