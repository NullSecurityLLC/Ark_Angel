"""The investigator's trail: what you looked at, what led where, and what you dropped.

Built from investigator-origin evidence (the Recorder extension, or imported
history), read against the rest of the case:

- **Sessions**: browsing split on session id, or on a gap of 45 minutes.
- **Hits**: pages that mention something already in the case evidence.
- **Pivots**: a search or profile visit on something you'd already seen,
  either in the case evidence or on a page earlier in the trail.
- **Threads**: one entity followed across several steps.
- **Loose ends**: things you clipped, or saw on a page that matched the case,
  and never searched for or opened.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Sequence

from ark_angel.analysis.base import is_investigator_activity
from ark_angel.analysis.correlate import entities_of, source_of
from ark_angel.analysis.extract import extract_entities, normalize_phone, parse_datetime
from ark_angel.models import Evidence

SESSION_GAP = timedelta(minutes=45)
DWELL_CAP = timedelta(minutes=30)
TRACKED = ("phone", "email", "handle", "plate")

Key = tuple[str, str]


@dataclass
class Seen:
    """Where an entity was before a step: case evidence, or an earlier page."""

    evidence_id: str
    label: str
    when: Optional[datetime] = None


@dataclass
class Step:
    evidence_id: str
    kind: str  # visit, search, clip
    when: datetime
    label: str  # page title, query or clip
    host: str
    url: str
    dwell: Optional[timedelta] = None
    hits: dict[Key, list[str]] = field(default_factory=dict)  # entity -> case evidence ids
    pivots: dict[Key, Seen] = field(default_factory=dict)  # entity looked up -> where you'd seen it
    looked_up: list[Key] = field(default_factory=list)  # entities this step searched for or opened
    found: list[Key] = field(default_factory=list)  # entities the page or clip showed


@dataclass
class Session:
    steps: list[Step] = field(default_factory=list)

    @property
    def start(self) -> datetime:
        return self.steps[0].when

    @property
    def end(self) -> datetime:
        return self.steps[-1].when + (self.steps[-1].dwell or timedelta())


@dataclass
class Trail:
    sessions: list[Session]
    threads: list[tuple[Key, list[Step]]]
    loose_ends: list[tuple[Key, Step, list[str]]]  # entity, where it was seen, matching case evidence
    domains: list[tuple[str, int]]

    @property
    def steps(self) -> list[Step]:
        return [step for session in self.sessions for step in session.steps]


def lookup_keys(text: str, known: set[Key]) -> list[Key]:
    """Entities a search query asks about: extracted ones, plus bare words that are known handles."""
    keys = [(e.kind, e.value) for e in extract_entities(text) if e.kind in TRACKED]
    for token in re.findall(r"[@\w.+-]+", text):
        bare = token.strip(".").lstrip("@").lower()
        if ("handle", bare) in known:
            keys.append(("handle", bare))
    digits = normalize_phone(text) if re.fullmatch(r"[\s\d()+.-]{10,}", text.strip()) else None
    if digits:
        keys.append(("phone", digits))
    return list(dict.fromkeys(keys))


def _when(item: Evidence) -> datetime:
    meta = item.metadata or {}
    return parse_datetime(str(meta.get("timestamp", ""))) or datetime.min


def build_trail(evidence: Sequence[Evidence]) -> Trail:
    """Reconstruct the investigator's trail from a case's evidence."""
    case_index: dict[Key, list[str]] = {}
    for item in evidence:
        if is_investigator_activity(item):
            continue
        for entity in entities_of(item):
            if entity.kind in TRACKED:
                ids = case_index.setdefault((entity.kind, entity.value), [])
                if item.identifier not in ids:
                    ids.append(item.identifier)
    case_labels = {item.identifier: source_of(item) for item in evidence}

    browsing = sorted((item for item in evidence if is_investigator_activity(item)),
                      key=lambda item: (_when(item), item.identifier))
    known: set[Key] = set(case_index)
    for item in browsing:
        known |= {(e.kind, e.value) for e in entities_of(item) if e.kind in TRACKED}

    sessions: list[Session] = []
    seen_on_page: dict[Key, Seen] = {}
    previous: Optional[tuple[Step, str]] = None
    for item in browsing:
        meta = item.metadata or {}
        kind = str(meta.get("kind") or "visit")
        label = (str(meta.get("query") or "") if kind == "search" else
                 str(meta.get("title") or meta.get("url") or item.identifier))
        if kind == "clip":
            label = str(meta.get("page_excerpt") or label)
        step = Step(item.identifier, kind, _when(item), label, str(meta.get("host") or ""), str(meta.get("url") or ""))
        session_id = str(meta.get("session") or "")

        if kind == "search":
            step.looked_up = lookup_keys(step.label, known)
        if meta.get("profile"):
            step.looked_up.append(("handle", str(meta["profile"])))
        step.found = [(e.kind, e.value) for e in entities_of(item) if e.kind in TRACKED]
        step.hits = {key: case_index[key] for key in step.found if key in case_index}
        for key in step.looked_up:
            if key in case_index:
                first = case_index[key][0]
                step.pivots[key] = Seen(first, f"{case_labels.get(first, first)} ({first})")
            elif key in seen_on_page:
                step.pivots[key] = seen_on_page[key]
        for key in step.found:
            seen_on_page.setdefault(key, Seen(item.identifier, f'"{step.label}" ({step.host})', step.when))

        new_session = (previous is None or (session_id and session_id != previous[1])
                       or step.when - previous[0].when > SESSION_GAP)
        if not new_session and previous is not None:
            previous[0].dwell = min(step.when - previous[0].when, DWELL_CAP)
        if new_session:
            sessions.append(Session())
        sessions[-1].steps.append(step)
        previous = (step, session_id)

    steps = [step for session in sessions for step in session.steps]
    threads: dict[Key, list[Step]] = {}
    for step in steps:
        for key in dict.fromkeys([*step.looked_up, *step.found]):
            threads.setdefault(key, []).append(step)
    followed = sorted(((key, run) for key, run in threads.items() if len(run) >= 2
                       and any(key in s.looked_up for s in run)),
                      key=lambda kv: (-len(kv[1]), kv[1][0].when))

    loose: list[tuple[Key, Step, list[str]]] = []
    chased = {key for step in steps for key in step.looked_up}
    for step in steps:
        candidates = step.found if step.kind == "clip" else list(step.hits)
        for key in candidates:
            if key not in chased and key not in {k for k, _, _ in loose}:
                loose.append((key, step, case_index.get(key, [])))
    loose.sort(key=lambda row: (not row[2], row[1].when))

    domains = Counter(step.host for step in steps if step.kind != "search" and step.host).most_common()
    return Trail(sessions=sessions, threads=followed, loose_ends=loose, domains=domains)
