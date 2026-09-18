"""GeoResolver backed by image EXIF GPS tags."""
from __future__ import annotations

from ark_angel.geo.base import GeoResolver
from ark_angel.models import Location


def _dms_to_decimal(dms: tuple, ref: str) -> float:
    d = dms[0][0] / dms[0][1]
    m = dms[1][0] / dms[1][1] / 60
    s = dms[2][0] / dms[2][1] / 3600
    val = d + m + s
    return -val if ref in ("S", "W") else val


class ExifGeoResolver(GeoResolver):
    """Resolves a GPS location directly from an image file's EXIF data."""

    def resolve(self, query: str) -> Location:
        """Extract GPS coordinates from the EXIF tags of an image file.

        Args:
            query: Path to the image file.

        Raises:
            ValueError: If the file has no GPS EXIF data.
        """
        try:
            import piexif
            from PIL import Image

            img = Image.open(query)
            raw = img.info.get("exif", b"")
            if not raw:
                raise ValueError(f"no EXIF data in {query!r}")
            exif = piexif.load(raw)
        except (FileNotFoundError, OSError) as exc:
            raise ValueError(str(exc)) from exc

        gps = exif.get("GPS", {})
        lat_data = gps.get(piexif.GPSIFD.GPSLatitude)
        lat_ref = gps.get(piexif.GPSIFD.GPSLatitudeRef)
        lon_data = gps.get(piexif.GPSIFD.GPSLongitude)
        lon_ref = gps.get(piexif.GPSIFD.GPSLongitudeRef)

        if not (lat_data and lon_data and lat_ref and lon_ref):
            raise ValueError(f"no GPS coordinates in EXIF of {query!r}")

        lat_ref_s = lat_ref.decode() if isinstance(lat_ref, bytes) else lat_ref
        lon_ref_s = lon_ref.decode() if isinstance(lon_ref, bytes) else lon_ref
        lat = _dms_to_decimal(lat_data, lat_ref_s)
        lon = _dms_to_decimal(lon_data, lon_ref_s)

        return Location(name=f"{lat:.6f}, {lon:.6f}", latitude=lat, longitude=lon)
