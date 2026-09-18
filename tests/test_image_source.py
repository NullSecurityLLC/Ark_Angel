"""Tests for ImageIngestionSource."""
from unittest.mock import patch

import pytest

from ark_angel.ingest.image_source import ImageIngestionSource, _dms_to_decimal


def test_dms_to_decimal_north():
    dms = ((37, 1), (46, 1), (4500, 100))  # 37° 46' 45.00"
    assert abs(_dms_to_decimal(dms, "N") - 37.779167) < 0.001


def test_dms_to_decimal_south_is_negative():
    dms = ((37, 1), (46, 1), (0, 1))
    assert _dms_to_decimal(dms, "S") < 0


def test_dms_to_decimal_west_is_negative():
    dms = ((122, 1), (25, 1), (0, 1))
    assert _dms_to_decimal(dms, "W") < 0


def test_no_exif_returns_empty():
    with patch("ark_angel.ingest.image_source._load_exif", return_value={}):
        leads = ImageIngestionSource().fetch_leads("photo.jpg")
    assert leads == []


def test_gps_exif_creates_lead():
    import piexif

    fake_exif = {
        "GPS": {
            piexif.GPSIFD.GPSLatitude: ((37, 1), (46, 1), (4500, 100)),
            piexif.GPSIFD.GPSLatitudeRef: b"N",
            piexif.GPSIFD.GPSLongitude: ((122, 1), (25, 1), (0, 1)),
            piexif.GPSIFD.GPSLongitudeRef: b"W",
        },
        "0th": {},
        "Exif": {},
    }
    with patch("ark_angel.ingest.image_source._load_exif", return_value=fake_exif):
        leads = ImageIngestionSource().fetch_leads("photo.jpg")

    gps_lead = next((l for l in leads if "GPS" in l.summary), None)
    assert gps_lead is not None
    assert "37." in gps_lead.summary
    assert "-122." in gps_lead.summary


def test_device_exif_creates_lead():
    import piexif

    fake_exif = {
        "GPS": {},
        "0th": {
            piexif.ImageIFD.Make: b"Apple",
            piexif.ImageIFD.Model: b"iPhone 14",
        },
        "Exif": {},
    }
    with patch("ark_angel.ingest.image_source._load_exif", return_value=fake_exif):
        leads = ImageIngestionSource().fetch_leads("photo.jpg")

    device_lead = next((l for l in leads if "Apple" in l.summary), None)
    assert device_lead is not None
    assert "iPhone 14" in device_lead.summary


def test_timestamp_exif_creates_lead():
    import piexif

    fake_exif = {
        "GPS": {},
        "0th": {},
        "Exif": {
            piexif.ExifIFD.DateTimeOriginal: b"2023:07:04 12:30:00",
        },
    }
    with patch("ark_angel.ingest.image_source._load_exif", return_value=fake_exif):
        leads = ImageIngestionSource().fetch_leads("photo.jpg")

    ts_lead = next((l for l in leads if "2023" in l.summary), None)
    assert ts_lead is not None
