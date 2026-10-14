from datetime import datetime
from pathlib import Path

import pytest

from ark_angel.ingest.csv_source import CsvTipSource, detect_columns, parse_when, read_table


def _write(tmp_path: Path, name: str, content, encoding: str = "utf-8") -> Path:
    path = tmp_path / name
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding=encoding)
    return path


@pytest.mark.parametrize("headers, expected", [
    (["Tip #", "Received", "Narrative", "Callback Number"],
     "text=Narrative; id=Tip #; when=Received; extra=Callback Number"),
    (["ID", "Date", "Time", "Tip", "Lat", "Lng"], "text=Tip; id=ID; date=Date; time=Time; lat=Lat; lon=Lng"),
    (["Reference", "Date/Time", "Details", "Latitude"], "text=Details; id=Reference; when=Date/Time; extra=Latitude"),
])
def test_detect_columns_from_common_headers(headers, expected) -> None:
    assert detect_columns(headers).describe() == expected


def test_explicit_columns_win_and_are_case_insensitive() -> None:
    mapping = detect_columns(["Notes", "Summary", "Stamp", "Caller"], text=["summary", "Notes"], when="stamp")
    assert mapping.text == ["Summary", "Notes"]
    assert mapping.when == "Stamp"
    assert mapping.extra == ["Caller"]


def test_missing_text_column_and_unknown_override_are_explained() -> None:
    with pytest.raises(ValueError, match="Name it with --text"):
        detect_columns(["foo", "bar"])
    with pytest.raises(ValueError, match="no column named 'nope'"):
        detect_columns(["Tip", "Date"], when="nope")


@pytest.mark.parametrize("value, dayfirst, expected", [
    ("10/01/2026 4:45 PM", False, datetime(2026, 10, 1, 16, 45)),
    ("01/10/2026 16:45", True, datetime(2026, 10, 1, 16, 45)),
    ("13/10/2026", False, datetime(2026, 10, 13)),  # first number > 12 must be the day
    ("12/31/26 12:05 AM", False, datetime(2026, 12, 31, 0, 5)),
    ("12/31/26 12:05 PM", False, datetime(2026, 12, 31, 12, 5)),
    ("2026-10-01T16:45:10", False, datetime(2026, 10, 1, 16, 45, 10)),
    ("Oct 1, 2026 4:45 PM", False, datetime(2026, 10, 1, 16, 45)),
    ("02/30/2026", False, None),
    ("last tuesday", False, None),
    ("", False, None),
])
def test_parse_when(value, dayfirst, expected) -> None:
    assert parse_when(value, dayfirst) == expected


def test_read_table_handles_bom_semicolons_and_cp1252(tmp_path: Path) -> None:
    bom = _write(tmp_path, "a.csv", b"\xef\xbb\xbfTip;Date\nhello;2026-10-01\n")
    assert read_table(bom) == (["Tip", "Date"], [{"Tip": "hello", "Date": "2026-10-01"}])

    legacy = _write(tmp_path, "b.csv", "Tip,Date\ncafé – corner,2026-10-01\n", encoding="cp1252")
    assert read_table(legacy)[1][0]["Tip"] == "café – corner"

    tabbed = _write(tmp_path, "c.tsv", "Tip\tDate\nhello\t2026-10-01\n")
    assert read_table(tabbed)[0] == ["Tip", "Date"]


def test_load_builds_evidence_per_row(tmp_path: Path) -> None:
    export = _write(tmp_path, "tipline.csv", (
        "Tip #,Received,Narrative,Callback Number,Lat,Lon\n"
        'T-1,10/01/2026 4:45 PM,"Saw her near the station, red backpack",303-555-0162,39.75,-104.99\n'
        ",,,,,\n"
        "T-2,someday,plate ABC-1234,,abc,\n"
        "T-2,10/02/2026,duplicate id row,,,\n"
    ))
    result = CsvTipSource(source="hotline").load(export)

    assert [e.identifier for e in result.evidence] == ["tipline:T-1", "tipline:T-2", "tipline:T-2@row5"]
    first = result.evidence[0]
    assert first.content == "2026-10-01 16:45 Saw her near the station, red backpack | Callback Number: 303-555-0162"
    assert first.metadata == {"source": "hotline", "row": 2, "timestamp": "2026-10-01 16:45:00",
                              "latitude": 39.75, "longitude": -104.99}
    assert result.evidence[1].content == "plate ABC-1234 | Received: someday"
    assert result.evidence[2].content.startswith("2026-10-02 duplicate")  # date only, no fake 00:00
    assert (result.skipped_blank, result.unparsed_times, result.bad_coordinates) == (1, 1, 1)
    assert (result.with_time, result.with_coords) == (2, 1)


def test_rows_without_id_column_use_row_numbers(tmp_path: Path) -> None:
    export = _write(tmp_path, "t.csv", "Tip,Date\nfirst,2026-10-01\nsecond,2026-10-02\n")
    assert [e.identifier for e in CsvTipSource().load(export).evidence] == ["t:2", "t:3"]
