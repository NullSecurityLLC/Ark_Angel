"""Import tip-line exports (CSV/TSV) as evidence, one item per row.

Exports differ on every system, so columns are guessed from header names and
any guess can be overridden. Columns that aren't the tip text, id, time or
coordinates are kept as ``Header: value`` in the evidence text, so a callback
number or "caller location" still gets scanned for entities and place names.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

from ark_angel.ingest.base import IngestionSource
from ark_angel.models import Evidence, Lead

# Normalized header names (lowercase, alphanumerics only) for each role, best first.
TEXT_HEADERS = ("tip", "tiptext", "narrative", "description", "details", "summary", "message", "content", "comments",
                "comment", "information", "info", "body", "text", "notes", "note")
ID_HEADERS = ("tipid", "tipno", "tipnumber", "id", "no", "reference", "ref", "refno", "casenumber",
              "ticket", "ticketid", "record", "recordid")
WHEN_HEADERS = ("received", "datereceived", "receivedat", "datetime", "timestamp", "reported", "reportedat",
                "submitted", "submittedat", "created", "createdat", "calltime", "when")
DATE_HEADERS = ("date", "calldate", "reportdate")
TIME_HEADERS = ("time", "calltime", "reporttime")
LAT_HEADERS = ("lat", "latitude", "gpslat", "y")
LON_HEADERS = ("lon", "lng", "long", "longitude", "gpslon", "gpslng", "x")

_US_DATE = re.compile(
    r"^\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})(?:[ T,]+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp][Mm])?)?\s*$"
)
_ISO_DATE = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?")
_NAMED_FORMATS = ("%b %d, %Y %I:%M %p", "%B %d, %Y %I:%M %p", "%b %d, %Y", "%B %d, %Y", "%d %b %Y %H:%M",
                  "%d %B %Y %H:%M", "%d %b %Y", "%d %B %Y")


def _norm(header: str) -> str:
    """``"Tip #"`` -> ``"tipno"``, ``"Date/Time"`` -> ``"datetime"``."""
    return re.sub(r"[^a-z0-9]", "", header.lower().replace("#", "no"))


@dataclass
class ColumnMap:
    """Which CSV column plays which role."""

    text: list[str] = field(default_factory=list)
    id: Optional[str] = None
    when: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None
    lat: Optional[str] = None
    lon: Optional[str] = None
    extra: list[str] = field(default_factory=list)

    def describe(self) -> str:
        parts = [f"text={'+'.join(self.text) or '-'}"]
        for role in ("id", "when", "date", "time", "lat", "lon"):
            if getattr(self, role):
                parts.append(f"{role}={getattr(self, role)}")
        if self.extra:
            parts.append(f"extra={', '.join(self.extra)}")
        return "; ".join(parts)


def _find(headers: Sequence[str], candidates: Sequence[str], taken: set[str]) -> Optional[str]:
    by_norm = {_norm(h): h for h in headers if h not in taken}
    for candidate in candidates:
        if candidate in by_norm:
            return by_norm[candidate]
    return None


def _require(headers: Sequence[str], name: str) -> str:
    for header in headers:
        if header == name or _norm(header) == _norm(name):
            return header
    raise ValueError(f"no column named {name!r}; columns are: {', '.join(headers)}")


def detect_columns(headers: Sequence[str], text: Sequence[str] = (), id: Optional[str] = None,
                   when: Optional[str] = None, lat: Optional[str] = None, lon: Optional[str] = None) -> ColumnMap:
    """Guess column roles from header names; explicit arguments win over guesses.

    Raises:
        ValueError: if a named column doesn't exist or no text column can be found.
    """
    mapping = ColumnMap()
    taken: set[str] = set()

    def claim(role: str, explicit: Optional[str], candidates: Sequence[str]) -> None:
        header = _require(headers, explicit) if explicit else _find(headers, candidates, taken)
        if header:
            setattr(mapping, role, header)
            taken.add(header)

    mapping.text = [_require(headers, name) for name in text]
    taken.update(mapping.text)
    # Ids are claimed before guessed text so a "Tip #" column isn't mistaken for the tip itself.
    claim("id", id, ID_HEADERS)
    if not mapping.text:
        guess = _find(headers, TEXT_HEADERS, taken)
        if guess:
            mapping.text = [guess]
            taken.add(guess)
    claim("when", when, WHEN_HEADERS)
    if not mapping.when:
        claim("date", None, DATE_HEADERS)
        claim("time", None, TIME_HEADERS)
    claim("lat", lat, LAT_HEADERS)
    claim("lon", lon, LON_HEADERS)
    if bool(mapping.lat) != bool(mapping.lon):
        mapping.lat = mapping.lon = None  # half a coordinate pair is useless; keep both as extras
        taken = set(mapping.text) | {h for h in (mapping.id, mapping.when, mapping.date, mapping.time) if h}
    mapping.extra = [h for h in headers if h not in taken and h.strip()]
    if not mapping.text:
        raise ValueError(f"couldn't tell which column holds the tip text; columns are: {', '.join(headers)}. "
                         "Name it with --text COLUMN")
    return mapping


def parse_when(value: str, dayfirst: bool = False) -> Optional[datetime]:
    """Parse the date/time formats tip-line exports actually use.

    Handles ISO (``2026-10-01 16:45``), slashed (``10/01/2026 4:45 PM``, month
    first unless ``dayfirst`` or the first number is over 12), and named months
    (``Oct 1, 2026 4:45 PM``). Returns ``None`` when it can't tell.
    """
    value = (value or "").strip()
    if not value:
        return None
    match = _ISO_DATE.match(value)
    if match:
        year, month, day, hour, minute, second = match.groups()
        return _make(year, month, day, hour, minute, second)
    match = _US_DATE.match(value)
    if match:
        first, second_num, year, hour, minute, second, meridiem = match.groups()
        a, b = int(first), int(second_num)
        day, month = (a, b) if (dayfirst or a > 12) else (b, a)
        if len(year) == 2:
            year = f"20{year}"
        if hour is not None and meridiem:
            hour = str(int(hour) % 12 + (12 if meridiem.lower() == "pm" else 0))
        return _make(year, month, day, hour, minute, second)
    for fmt in _NAMED_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _make(year, month, day, hour=None, minute=None, second=None) -> Optional[datetime]:
    try:
        return datetime(int(year), int(month), int(day), int(hour or 0), int(minute or 0), int(second or 0))
    except ValueError:
        return None


def read_table(path: str | Path) -> tuple[list[str], list[dict[str, str]]]:
    """Read a CSV/TSV export, coping with Excel BOMs, cp1252 and ; or tab delimiters."""
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):  # latin-1 never fails, so text is always set
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel_tab if str(path).lower().endswith(".tsv") else csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = [h.strip() for h in (reader.fieldnames or [])]
    if not headers:
        raise ValueError(f"{path}: no header row found")
    reader.fieldnames = headers
    rows = [{k: (v or "").strip() for k, v in row.items() if k is not None} for row in reader]
    return headers, rows


@dataclass
class CsvImport:
    mapping: ColumnMap
    evidence: list[Evidence]
    skipped_blank: int = 0
    bad_coordinates: int = 0
    unparsed_times: int = 0

    @property
    def with_time(self) -> int:
        return sum(1 for e in self.evidence if (e.metadata or {}).get("timestamp"))

    @property
    def with_coords(self) -> int:
        return sum(1 for e in self.evidence if "latitude" in (e.metadata or {}))


class CsvTipSource(IngestionSource):
    """Turns each row of a tip-line export into one evidence item."""

    def __init__(self, text: Sequence[str] = (), id: Optional[str] = None, when: Optional[str] = None,
                 lat: Optional[str] = None, lon: Optional[str] = None, dayfirst: bool = False,
                 source: Optional[str] = None) -> None:
        self.overrides = {"text": text, "id": id, "when": when, "lat": lat, "lon": lon}
        self.dayfirst = dayfirst
        self.source = source

    def fetch_leads(self, identifier: str) -> Sequence[Lead]:
        return [Lead(identifier=e.identifier, summary=str(e.content)) for e in self.load(identifier).evidence]

    def load(self, path: str | Path) -> CsvImport:
        headers, rows = read_table(path)
        mapping = detect_columns(headers, **self.overrides)
        result = CsvImport(mapping=mapping, evidence=[])
        stem = Path(path).stem
        source = self.source or stem
        seen_ids: set[str] = set()
        for number, row in enumerate(rows, start=2):  # row 1 is the header
            item = self._row(row, number, mapping, stem, source, result)
            if item is None:
                result.skipped_blank += 1
                continue
            if item.identifier in seen_ids:  # duplicate tip ids in the export: keep both rows
                item.identifier = f"{item.identifier}@row{number}"
            seen_ids.add(item.identifier)
            result.evidence.append(item)
        return result

    def _row(self, row: dict[str, str], number: int, mapping: ColumnMap, stem: str, source: str,
             result: CsvImport) -> Optional[Evidence]:
        text = " ".join(row.get(col, "") for col in mapping.text).strip()
        extras = [f"{col}: {row[col]}" for col in mapping.extra if row.get(col)]
        if not text and not extras:
            return None

        metadata: dict = {"source": source, "row": number}
        raw_when = row.get(mapping.when, "") if mapping.when else " ".join(
            row.get(col, "") for col in (mapping.date, mapping.time) if col).strip()
        when = parse_when(raw_when, self.dayfirst)
        if when:
            metadata["timestamp"] = when.strftime("%Y-%m-%d %H:%M:%S")
        elif raw_when:
            result.unparsed_times += 1
            extras.insert(0, f"Received: {raw_when}")

        if mapping.lat and mapping.lon and (row.get(mapping.lat) or row.get(mapping.lon)):
            try:
                lat, lon = float(row[mapping.lat]), float(row[mapping.lon])
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    raise ValueError
                metadata.update(latitude=lat, longitude=lon)
            except (KeyError, ValueError):
                result.bad_coordinates += 1

        has_clock = ":" in raw_when or (when is not None and (when.hour or when.minute))
        prefix = (f"{when:%Y-%m-%d %H:%M} " if has_clock else f"{when:%Y-%m-%d} ") if when else ""
        content = prefix + " | ".join(part for part in [text, *extras] if part)
        tip_id = row.get(mapping.id, "") if mapping.id else ""
        identifier = f"{stem}:{tip_id or number}"
        return Evidence(identifier=identifier, type="tip", content=content, metadata=metadata)
