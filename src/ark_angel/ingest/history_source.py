"""Import a time window of browser history as investigator activity.

For when the Recorder wasn't running. History has no page text, so it gives
the trail (what you searched, which profiles you opened, in what order) but
nothing that counts as corroboration. Supported:

- Chrome, Edge, Brave, Opera, Vivaldi: the ``History`` SQLite file
  (times are microseconds since 1601-01-01 UTC)
- Firefox: ``places.sqlite`` (microseconds since 1970)
- Safari: ``History.db`` (seconds since 2001-01-01 UTC)

The file is copied before reading, because the browser keeps it locked
while it's open. A window (``since``) is required: a whole browsing history
is nobody's case evidence.
"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from ark_angel.ingest.browse_source import Capture, capture_to_evidence
from ark_angel.models import Evidence

_CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
_SAFARI_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
FORMATS = ("chrome", "firefox", "safari")


@dataclass
class HistoryImport:
    format: str
    evidence: list[Evidence] = field(default_factory=list)
    skipped: int = 0  # non-web URLs (chrome://, file://, about:...)


def _local(moment: datetime) -> datetime:
    """An aware UTC time as naive local wall-clock time, like every other timestamp in a case."""
    return moment.astimezone().replace(tzinfo=None)


def _to_utc(local: datetime) -> datetime:
    return local.astimezone(timezone.utc) if local.tzinfo else local.replace(tzinfo=None).astimezone(timezone.utc)


def detect_format(con: sqlite3.Connection) -> str:
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if {"urls", "visits"} <= tables:
        return "chrome"
    if {"moz_places", "moz_historyvisits"} <= tables:
        return "firefox"
    if {"history_items", "history_visits"} <= tables:
        return "safari"
    raise ValueError("not a Chrome/Edge/Brave History, Firefox places.sqlite or Safari History.db file")


def _query(fmt: str, since: datetime, until: datetime) -> tuple[str, tuple]:
    if fmt == "chrome":
        lo, hi = ((t - _CHROME_EPOCH) // timedelta(microseconds=1) for t in (since, until))
        return ("SELECT u.url, u.title, v.visit_time FROM visits v JOIN urls u ON u.id = v.url "
                "WHERE v.visit_time BETWEEN ? AND ? ORDER BY v.visit_time", (lo, hi))
    if fmt == "firefox":
        lo, hi = ((t - _UNIX_EPOCH) // timedelta(microseconds=1) for t in (since, until))
        return ("SELECT p.url, p.title, v.visit_date FROM moz_historyvisits v JOIN moz_places p ON p.id = v.place_id "
                "WHERE v.visit_date BETWEEN ? AND ? ORDER BY v.visit_date", (lo, hi))
    lo, hi = ((t - _SAFARI_EPOCH).total_seconds() for t in (since, until))
    return ("SELECT i.url, v.title, v.visit_time FROM history_visits v JOIN history_items i ON i.id = v.history_item "
            "WHERE v.visit_time BETWEEN ? AND ? ORDER BY v.visit_time", (lo, hi))


def _when(fmt: str, raw) -> datetime:
    if fmt == "chrome":
        return _local(_CHROME_EPOCH + timedelta(microseconds=int(raw)))
    if fmt == "firefox":
        return _local(_UNIX_EPOCH + timedelta(microseconds=int(raw)))
    return _local(_SAFARI_EPOCH + timedelta(seconds=float(raw)))


def load_history(path: str | Path, since: datetime, until: Optional[datetime] = None, fmt: str = "auto",
                 session: str = "") -> HistoryImport:
    """Visits between ``since`` and ``until`` (local times; ``until`` defaults to now) as evidence."""
    until = until or datetime.now()
    if until <= since:
        raise ValueError("--until must be after --since")
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "history.sqlite"
        shutil.copyfile(path, copy)
        for suffix in ("-wal", "-shm"):  # a running browser keeps recent visits in the write-ahead log
            side = Path(f"{path}{suffix}")
            if side.exists():
                shutil.copyfile(side, Path(f"{copy}{suffix}"))
        con = sqlite3.connect(copy)
        try:
            try:
                found = detect_format(con)
            except sqlite3.DatabaseError as exc:
                raise ValueError(f"{Path(path).name} isn't a readable SQLite history file ({exc})") from exc
            if fmt not in ("auto", found):
                raise ValueError(f"{Path(path).name} looks like a {found} history, not {fmt}")
            sql, args = _query(found, _to_utc(since), _to_utc(until))
            rows = con.execute(sql, args).fetchall()
        finally:
            con.close()

    result = HistoryImport(found)
    seen: set[str] = set()
    for url, title, raw in rows:
        if not str(url or "").lower().startswith(("http://", "https://")):
            result.skipped += 1
            continue
        evidence = capture_to_evidence(Capture(url=str(url)[:2048], when=_when(found, raw), title=str(title or "")[:300],
                                               session=session))
        if evidence.identifier not in seen:  # redirects can log the same visit twice in a second
            seen.add(evidence.identifier)
            result.evidence.append(evidence)
    return result
