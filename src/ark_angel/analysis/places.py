"""Find named places in free text using a case gazetteer.

Matching is offline and deterministic: an investigator decides which names map
to which coordinates (by hand, CSV import, or an opt-in lookup), and the
matcher only finds those names. ``suggest_places`` points at capitalised place-
like phrases the gazetteer does not cover yet, so nothing silently slips by.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from ark_angel.models import Evidence, Place

# Capitalised phrase after a location preposition: "near Union Station", "in Boulder".
_CANDIDATE_PATTERN = re.compile(
    r"\b(?:at|near|in|by|outside|around|behind|under|from|toward|towards|across)\s+(?:the\s+)?"
    r"((?:[A-Z][\w'’-]*)(?:[ \t]+(?:of[ \t]+|de[ \t]+|la[ \t]+)?[A-Z][\w'’-]*)*)"
)
_NOT_PLACES = {
    "January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
    "November", "December", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "I", "The", "A", "An",
}


def _name_pattern(name: str) -> str:
    return r"\s+".join(re.escape(part) for part in name.split())


class PlaceMatcher:
    """Finds gazetteer names and aliases in text, longest match first."""

    def __init__(self, places: Iterable[Place]) -> None:
        self._by_key: dict[str, Place] = {}
        for place in places:
            for name in [place.name, *place.aliases]:
                key = _key(name)
                if key:
                    self._by_key.setdefault(key, place)
        names = sorted(self._by_key, key=len, reverse=True)
        self._pattern: Optional[re.Pattern] = (
            re.compile(r"(?<!\w)(?:" + "|".join(_name_pattern(n) for n in names) + r")(?!\w)", re.IGNORECASE)
            if names else None
        )

    def __bool__(self) -> bool:
        return self._pattern is not None

    def spans(self, text: str) -> list[tuple[Place, str, int, int]]:
        """Return ``(place, matched text, start, end)`` for every gazetteer match."""
        if self._pattern is None:
            return []
        return [(self._by_key[_key(m.group(0))], m.group(0), m.start(), m.end())
                for m in self._pattern.finditer(str(text))]

    def find(self, text: str) -> list[tuple[Place, str]]:
        """Return ``(place, matched text)`` for each distinct place mentioned."""
        found: list[tuple[Place, str]] = []
        seen: set[str] = set()
        for place, raw, _, _ in self.spans(text):
            if place.name not in seen:
                seen.add(place.name)
                found.append((place, raw))
        return found


def _key(name: str) -> str:
    return " ".join(name.lower().split())


@dataclass
class PlaceSuggestion:
    name: str
    evidence_ids: list[str] = field(default_factory=list)


def suggest_places(evidence: Sequence[Evidence], places: Iterable[Place] = ()) -> list[PlaceSuggestion]:
    """Capitalised place-like phrases in evidence that the gazetteer doesn't cover.

    These are hints for a human to resolve, ordered by how often they appear.
    """
    matcher = PlaceMatcher(places)
    suggestions: dict[str, PlaceSuggestion] = {}
    for item in evidence:
        text = str(item.content)
        covered = [(start, end) for _, _, start, end in matcher.spans(text)]
        for match in _CANDIDATE_PATTERN.finditer(text):
            name = match.group(1).rstrip("'’-")
            start, end = match.start(1), match.start(1) + len(name)
            if any(start < c_end and c_start < end for c_start, c_end in covered):
                continue  # already resolved by the gazetteer, e.g. "Cherry Creek" inside "Cherry Creek trail"
            if name.endswith(("'s", "’s")) or name in _NOT_PLACES or len(name) < 3:
                continue
            suggestion = suggestions.setdefault(_key(name), PlaceSuggestion(name=name))
            if item.identifier not in suggestion.evidence_ids:
                suggestion.evidence_ids.append(item.identifier)
    return sorted(suggestions.values(), key=lambda s: (-len(s.evidence_ids), s.name.lower()))
