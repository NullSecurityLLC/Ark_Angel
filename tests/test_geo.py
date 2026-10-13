from pathlib import Path

import pytest

from ark_angel.demo import make_photo
from ark_angel.geo.exif import decimal_to_dms, dms_to_decimal
from ark_angel.geo.exif_resolver import ExifGeoResolver
from ark_angel.ingest.image_source import ImageIngestionSource


def test_decimal_dms_round_trip() -> None:
    assert abs(dms_to_decimal(decimal_to_dms(39.7524), "N") - 39.7524) < 1e-5
    assert abs(dms_to_decimal(decimal_to_dms(-104.9996), "W") + 104.9996) < 1e-5


def test_real_jpeg_exif_round_trip(tmp_path: Path) -> None:
    photo = make_photo(tmp_path / "p.jpg", -33.8568, 151.2153, "2026:01:02 03:04:05", "Acme", "Cam 1")

    location = ExifGeoResolver().resolve(str(photo))
    assert abs(location.latitude + 33.8568) < 1e-4
    assert abs(location.longitude - 151.2153) < 1e-4

    evidence, leads = ImageIngestionSource().ingest(str(photo))
    assert evidence is not None
    assert evidence.metadata["timestamp"] == "2026:01:02 03:04:05"
    assert evidence.metadata["device"] == "Acme Cam 1"
    assert all(lead.evidence_ids == [evidence.identifier] for lead in leads)


def test_resolver_rejects_missing_file_and_no_exif(tmp_path: Path) -> None:
    from PIL import Image

    plain = tmp_path / "plain.png"
    Image.new("RGB", (4, 4)).save(plain)
    with pytest.raises(ValueError):
        ExifGeoResolver().resolve(str(tmp_path / "missing.jpg"))
    with pytest.raises(ValueError):
        ExifGeoResolver().resolve(str(plain))
