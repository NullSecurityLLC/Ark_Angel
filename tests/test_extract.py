from datetime import datetime

from ark_angel.analysis.extract import extract_entities, normalize_phone, parse_datetime


def _pairs(text: str) -> list[tuple[str, str]]:
    return [(e.kind, e.value) for e in extract_entities(text)]


def test_phone_formats_normalize_to_same_value() -> None:
    for raw in ("303-555-0147", "(303) 555-0147", "303.555.0147", "+1 303 555 0147"):
        assert ("phone", "3035550147") in _pairs(f"call {raw} now"), raw


def test_dates_and_coordinates_are_not_phones() -> None:
    pairs = _pairs("2026-10-01 16:45 pin dropped at 39.7489, -104.9985")
    assert pairs == [("coords", "39.74890,-104.99850")]


def test_email_is_lowercased_and_not_read_as_handle() -> None:
    assert _pairs("write to Sam.K@Example.org") == [("email", "sam.k@example.org")]


def test_handles_from_at_sign_and_keywords() -> None:
    pairs = _pairs("her handle is riley_t_plays and she follows @RT_Owl; account was closed")
    assert ("handle", "riley_t_plays") in pairs
    assert ("handle", "rt_owl") in pairs
    assert ("handle", "was") not in pairs


def test_plate_variants_normalize() -> None:
    assert _pairs("plate ABC-1234") == _pairs("plate abc 1234") == [("plate", "ABC1234")]


def test_url_trailing_punctuation_stripped() -> None:
    assert ("url", "https://github.com/someone") in _pairs("see https://github.com/someone.")


def test_normalize_phone_rejects_short_numbers() -> None:
    assert normalize_phone("555-0147") is None


def test_parse_datetime_handles_iso_and_exif() -> None:
    assert parse_datetime("seen 2026-09-29 14:40 near") == datetime(2026, 9, 29, 14, 40)
    assert parse_datetime("2026:10:01 03:10:00") == datetime(2026, 10, 1, 3, 10)
    assert parse_datetime("no date here") is None
