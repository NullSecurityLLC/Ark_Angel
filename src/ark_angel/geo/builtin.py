"""Bundled offline US gazetteer: states, counties and cities.

This is a *resolver*, not a matcher. Matching ~25,000 town names against every
tip would be a disaster (Riley, Kansas is a real town), so nothing here is
matched against evidence directly. It turns the place names an investigator
(or ``place suggest``) already picked out into coordinates, narrowed by state
hints like "Boulder, CO". Results go into the case gazetteer, where the usual
matching takes over.

Data: cities from GeoNames (CC BY 4.0) via geonamescache; county centres from
the US Census Bureau (public domain). See ``geo/data/README.md``.
"""
from __future__ import annotations

import csv
import gzip
import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from typing import Iterable, Optional, Sequence

from ark_angel.models import Place

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
_STATE_BY_NAME = {name.lower(): code for code, name in STATES.items()}
_KIND_ORDER = {"state": 0, "county": 1, "city": 2}
# "Saint Paul" / "St. Paul", "Fort Collins" / "Ft. Collins", "Mount Vernon" / "Mt. Vernon".
_ABBREVIATIONS = (("saint", "st"), ("sainte", "ste"), ("fort", "ft"), ("mount", "mt"))


@dataclass(frozen=True)
class Entry:
    kind: str  # "state", "county" or "city"
    name: str
    state: str  # USPS code
    latitude: float
    longitude: float
    radius_m: int
    population: int = 0

    @property
    def label(self) -> str:
        where = self.name if self.kind == "state" else f"{self.name}, {self.state}"
        pop = f", pop {self.population:,}" if self.population else ""
        return f"{where} ({self.kind}{pop}, ±{self.radius_m / 1000:g} km)"

    def to_place(self, name: Optional[str] = None) -> Place:
        """A case-gazetteer place, named the way the evidence writes it."""
        name = name or self.name
        aliases = [alias for alias in _variants(name) if alias != name]
        return Place(name=name, latitude=self.latitude, longitude=self.longitude, aliases=aliases,
                     radius_m=float(self.radius_m), source=f"builtin:{self.kind}:{self.name}, {self.state}")


def _key(name: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    full = dict((short, long) for long, short in _ABBREVIATIONS)
    return " ".join(full.get(w, w) for w in words)


def _variants(name: str) -> list[str]:
    """``"Saint Paul"`` -> ``["Saint Paul", "St. Paul"]`` (and the reverse)."""
    words = name.split()
    if not words:
        return [name]
    first = words[0].lower().rstrip(".")
    for long, short in _ABBREVIATIONS:
        if first == long:
            return [name, " ".join([short.capitalize() + ".", *words[1:]])]
        if first == short:
            return [name, " ".join([long.capitalize(), *words[1:]])]
    return [name]


@lru_cache(maxsize=1)
def _index() -> dict[str, tuple[Entry, ...]]:
    path = resources.files("ark_angel") / "geo" / "data" / "us_gazetteer.csv.gz"
    index: dict[str, list[Entry]] = {}
    with gzip.open(path.open("rb"), "rt", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            entry = Entry(kind=row["kind"], name=row["name"], state=row["state"], latitude=float(row["latitude"]),
                          longitude=float(row["longitude"]), radius_m=int(float(row["radius_m"])),
                          population=int(row["population"] or 0))
            index.setdefault(_key(entry.name), []).append(entry)
    return {key: tuple(entries) for key, entries in index.items()}


def normalize_states(states: Iterable[str]) -> list[str]:
    """``["co", "Texas"]`` -> ``["CO", "TX"]``.

    Raises:
        ValueError: for something that isn't a US state or DC.
    """
    codes = []
    for state in states:
        value = state.strip()
        code = value.upper() if value.upper() in STATES else _STATE_BY_NAME.get(value.lower())
        if not code:
            raise ValueError(f"unknown US state {state!r} (use a code like CO or a name like Colorado)")
        codes.append(code)
    return codes


def find(name: str, states: Sequence[str] = ()) -> list[Entry]:
    """Every gazetteer entry called ``name``, optionally limited to some states.

    States come first, then counties, then cities by population.
    """
    entries = _index().get(_key(name), ())
    if states:
        wanted = set(states)
        entries = tuple(e for e in entries if e.state in wanted)
    return sorted(entries, key=lambda e: (_KIND_ORDER[e.kind], -e.population, e.state))


_STATE_ALTERNATION = "|".join(sorted((re.escape(n) for n in STATES.values()), key=len, reverse=True))


def state_hints(name: str, texts: Iterable[str]) -> list[str]:
    """States named right after ``name``: "Boulder, CO" or "Boulder, Colorado"."""
    pattern = re.compile(rf"\b{re.escape(name)}\s*,\s*(?:([A-Z]{{2}})\b|({_STATE_ALTERNATION})\b)")
    hints: list[str] = []
    for text in texts:
        for match in pattern.finditer(text):
            code = match.group(1) if match.group(1) in STATES else _STATE_BY_NAME.get((match.group(2) or "").lower())
            if code and code not in hints:
                hints.append(code)
    return hints


@dataclass
class Resolution:
    name: str
    status: str  # "resolved", "ambiguous" or "unknown"
    entry: Optional[Entry] = None
    candidates: list[Entry] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    why: str = ""


def resolve(name: str, hints: Sequence[str] = (), default_states: Sequence[str] = ()) -> Resolution:
    """Pick the gazetteer entry ``name`` most plausibly means, or say why not.

    State hints from the evidence ("Boulder, CO") win over ``default_states``
    (the case's region). A bare state name with no hint means the state. A
    unique name nationwide resolves on its own. Anything else stays ambiguous
    for a human to pick.
    """
    everywhere = find(name)
    if not everywhere:
        return Resolution(name, "unknown", why="not in the built-in gazetteer")
    scope, scope_label = (list(hints), "the state named next to it") if hints else (list(default_states), "the case region")
    if scope:
        local = [e for e in everywhere if e.state in scope]
        if len(local) == 1:
            return Resolution(name, "resolved", local[0], everywhere, list(hints), f"only match in {scope_label}")
        if local:
            return Resolution(name, "ambiguous", None, local, list(hints), f"{len(local)} matches in {scope_label}")
        return Resolution(name, "ambiguous", None, everywhere, list(hints),
                          f"no match in {scope_label} ({', '.join(scope)}); {len(everywhere)} elsewhere")
    states = [e for e in everywhere if e.kind == "state"]
    if states:
        return Resolution(name, "resolved", states[0], everywhere, [], "a state name, and no other state was hinted")
    if len(everywhere) == 1:
        return Resolution(name, "resolved", everywhere[0], everywhere, [], "the only place with this name")
    return Resolution(name, "ambiguous", None, everywhere, [], f"{len(everywhere)} places share this name")
