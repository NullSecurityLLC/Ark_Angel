"""Pull normalized entities (phones, emails, handles, plates...) out of free text.

Normalization is the whole trick: ``(303) 555-0147``, ``303.555.0147`` and
``+1 303 555 0147`` all become ``3035550147`` so they match across sources.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Callable, Optional

from ark_angel.models import Entity

URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
DATETIME_PATTERN = re.compile(r"\b(\d{4})[-:/](\d{2})[-:/](\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?\b")
COORDS_PATTERN = re.compile(r"(?<![\d.])(-?\d{1,2}\.\d{3,})\s*,\s*(-?\d{1,3}\.\d{3,})(?![\d.])")
PLATE_PATTERN = re.compile(
    r"\b(?:licen[cs]e\s+plate|plates?|tags?|licen[cs]e)(?:\s+(?:number|no\.?|#))?\s*[:#]?\s*"
    r"([A-Z0-9][A-Z0-9 -]{2,8}[A-Z0-9])\b",
    re.IGNORECASE,
)
HANDLE_AT_PATTERN = re.compile(r"(?<![\w@.])@([A-Za-z0-9_](?:[A-Za-z0-9_.]{1,28}[A-Za-z0-9_]))")
HANDLE_KEYWORD_PATTERN = re.compile(
    r"\b(?:handle|username|user\s*name|account|goes by)(?:\s+is)?\s*[:\-]?\s*@?"
    r"([A-Za-z0-9_](?:[A-Za-z0-9_.]{1,28}[A-Za-z0-9_]))",
    re.IGNORECASE,
)
PHONE_PATTERN = re.compile(r"(?<![\w])\+?\(?\d[\d\-.\s()]{7,}\d(?![\w])")

# Words that follow "account"/"handle" in ordinary prose and are not handles.
_HANDLE_STOPWORDS = {"is", "was", "the", "for", "and", "with", "unknown", "number", "name"}


def normalize_phone(raw: str) -> Optional[str]:
    """Return digits only (US country code stripped), or ``None`` if not phone-like."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if 10 <= len(digits) <= 15 else None


def normalize_plate(raw: str) -> Optional[str]:
    value = re.sub(r"[\s-]", "", raw).upper()
    has_digit = any(ch.isdigit() for ch in value)
    return value if 4 <= len(value) <= 8 and has_digit else None


def parse_datetime(text: str) -> Optional[datetime]:
    """Parse the first ``YYYY-MM-DD[ HH:MM[:SS]]`` (or EXIF ``YYYY:MM:DD``) in ``text``."""
    match = DATETIME_PATTERN.search(text or "")
    if not match:
        return None
    year, month, day, hour, minute, second = match.groups()
    try:
        return datetime(int(year), int(month), int(day), int(hour or 0), int(minute or 0), int(second or 0))
    except ValueError:
        return None


def extract_entities(text: str) -> list[Entity]:
    """Return every entity found in ``text``, de-duplicated, in order found.

    Patterns run from most to least specific, and each match is masked out so
    a URL is never re-read as a handle or a date as a phone number.
    """
    text = str(text or "")
    entities: list[Entity] = []
    seen: set[tuple[str, str]] = set()
    masked = list(text)

    def add(kind: str, value: Optional[str], raw: str) -> None:
        if value and (kind, value) not in seen:
            seen.add((kind, value))
            entities.append(Entity(kind=kind, value=value, raw=raw.strip()))

    def scan(pattern: re.Pattern, kind: Optional[str], normalize: Callable[[re.Match], Optional[str]],
             group: int = 0) -> None:
        current = "".join(masked)
        for match in pattern.finditer(current):
            if kind:
                add(kind, normalize(match), match.group(group))
            start, end = match.span(group)
            masked[start:end] = " " * (end - start)

    scan(URL_PATTERN, "url", lambda m: m.group(0).rstrip(".,);:!?").lower())
    scan(EMAIL_PATTERN, "email", lambda m: m.group(0).lower())
    scan(DATETIME_PATTERN, None, lambda m: None)
    scan(COORDS_PATTERN, "coords", _normalize_coords)
    scan(PLATE_PATTERN, "plate", lambda m: normalize_plate(m.group(1)), group=1)
    scan(HANDLE_AT_PATTERN, "handle", lambda m: m.group(1).lower(), group=1)
    scan(HANDLE_KEYWORD_PATTERN, "handle", _normalize_keyword_handle, group=1)
    scan(PHONE_PATTERN, "phone", lambda m: normalize_phone(m.group(0)))
    return entities


def _normalize_coords(match: re.Match) -> Optional[str]:
    lat, lon = float(match.group(1)), float(match.group(2))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return f"{lat:.5f},{lon:.5f}"


def _normalize_keyword_handle(match: re.Match) -> Optional[str]:
    value = match.group(1).lower()
    # A bare word after "account" is only a handle if it looks like one.
    if value in _HANDLE_STOPWORDS or not re.search(r"[_\d.]", value):
        return None
    return value
