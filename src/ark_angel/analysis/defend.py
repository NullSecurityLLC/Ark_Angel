"""Principal defender: the engine turned around to protect someone.

A missing-person case asks "where is this person?". A defender case asks the
same question from the other side: how easily could someone hostile answer it
about the principal, and is anyone trying? Three passes, all deterministic
and explained like every other ArkAngel score:

- **Exposures**: the principal's own identifiers (phone, email, handles,
  plates) and sensitive places (home, work, school) wherever they show up in
  the evidence: people-search listings, posts, geotagged photos. An
  identifier sitting next to the principal's name counts for more, because
  that's what lets a stranger tie it to them.
- **Pattern of life**: places the evidence keeps putting the principal, and
  routines (same place, same weekday, same time of day) that make them
  predictable.
- **Threats**: threatening language in messages, grouped by sender, with
  escalation over time and a hard flag when a threat shows knowledge of a
  real sensitive place.

Every lead carries a ``Fix:`` reason saying what to do about it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional, Sequence

from ark_angel.analysis.correlate import (
    SOURCE_DIVERSITY_BONUS, CaseAnalysis, analyze_case, entities_of, haversine_km, scan_text, source_of,
)
from ark_angel.analysis.extract import normalize_phone, normalize_plate, parse_datetime
from ark_angel.models import Entity, Evidence, Lead, Place, Principal

# How bad one evidence item exposing each kind of identifier is.
EXPOSURE_WEIGHTS = {"place": 5.0, "phone": 4.0, "plate": 3.5, "email": 3.0, "handle": 2.0}
# Extra per item that also names the principal (or carries another of their identifiers).
LINK_BONUS = 1.0
PATTERN_WEIGHT = 2.0
ROUTINE_BONUS = 3.0
ROUTINE_WINDOW_HOURS = 2
# Any threat outranks a passive exposure of the same size.
THREAT_BASE = 5.0
KNOWS_LOCATION_BONUS = 5.0
ESCALATION_BONUS = 3.0
DEFENDER_PREFIXES = ("exp-", "threat-", "geo-")

_LABELS = {"phone": "Phone", "email": "Email", "plate": "Plate", "handle": "Handle", "place": "Sensitive place"}

FIXES = {
    "phone": "find each listing and file an opt-out (people-search sites honour removal requests); give out a "
             "forwarding number (a VoIP line) for anything public from now on.",
    "email": "remove it from the listings, check it against breach-notification services, and use a separate "
             "alias address for anything public-facing.",
    "handle": "lock the account to private and prune followers, or retire the handle: it ties the principal's "
              "posts together.",
    "plate": "a plate leads to the vehicle and wherever it's parked; take down photos that show it, or ask the "
             "poster to blur it.",
    "place_text": "request removal of the address from each listing; look into the state's address "
                  "confidentiality program and county assessor/recorder privacy options.",
    "place_geo": "delete the post or re-upload it with location data stripped, and turn off location tagging in "
                 "the camera app.",
    "pattern": "the evidence keeps placing the principal here; stop posting from it in real time (post after "
               "leaving) and check it isn't a place that should be registered as sensitive.",
    "routine": "it's predictable: vary the days, times and route, and never post while there.",
    "threat": "preserve every message in full (screenshots plus exports with headers), don't reply, and report "
              "it to law enforcement.",
    "threat_location": "this sender knows a real sensitive location. Treat it as urgent: tell law enforcement "
                       "that, and plan for the principal not being there.",
}


@dataclass(frozen=True)
class ThreatPattern:
    category: str
    weight: float
    pattern: re.Pattern


def _p(text: str) -> re.Pattern:
    return re.compile(text, re.IGNORECASE)


# Ordered most to least severe. Each pattern counts once per message. Violence needs a target ("kill you",
# not "killed it at the gym") so ordinary slang doesn't trip it.
THREAT_PATTERNS: tuple[ThreatPattern, ...] = (
    ThreatPattern("violence", 6.0, _p(r"\b(?:kill|murder|shoot|stab|hurt|strangle|choke|beat|burn|rape)"
                                      r"(?:s|ed|ing)?\s+(?:you|u|ya|her|him|them|your\s+\w+)\b")),
    ThreatPattern("violence", 6.0, _p(r"\b(?:you(?:'?re| are)|ur)\s+(?:dead|gonna die|going to die)\b")),
    ThreatPattern("violence", 6.0, _p(r"\b(?:deserve to die|if i can'?t have you)\b")),
    ThreatPattern("location knowledge", 5.0, _p(r"\bi know where (?:you|u|your \w+) (?:live|work|sleep|park|go|"
                                                r"are|stay|run)s?\b")),
    ThreatPattern("location knowledge", 5.0, _p(r"\b(?:i|we)(?: was| were)? (?:saw|seen|watched|watching|"
                                                r"followed|following) (?:you|u)\b")),
    ThreatPattern("location knowledge", 5.0, _p(r"\b(?:outside|near|by) (?:your|ur) (?:house|home|place|door|"
                                                r"work|office|building|school|car|window)\b")),
    ThreatPattern("approach", 4.0, _p(r"\b(?:i'?m|i am|im) (?:coming|on my way) (?:for|to (?:get|see|find)) "
                                      r"(?:you|u)\b")),
    ThreatPattern("approach", 4.0, _p(r"\b(?:i'?ll|i will|gonna|going to) (?:find|get|track down) (?:you|u)\b")),
    ThreatPattern("approach", 4.0, _p(r"\b(?:you|u) can'?t hide\b")),
    ThreatPattern("fixation", 3.0, _p(r"\b(?:you|u)(?:'ll| will) (?:regret|pay for) (?:this|it|that)\b")),
    ThreatPattern("fixation", 3.0, _p(r"\b(?:make|making) (?:you|u) pay\b")),
    ThreatPattern("fixation", 3.0, _p(r"\b(?:you|u) (?:ruined|destroyed|wrecked) my\b")),
    ThreatPattern("fixation", 3.0, _p(r"\b(?:you(?:'?re| are) mine|you owe me|last warning)\b")),
)
_CATEGORY_ORDER = {"violence": 0, "location knowledge": 1, "approach": 2, "fixation": 3}


@dataclass
class ThreatHit:
    category: str
    weight: float
    phrase: str


def threat_hits(text: str) -> list[ThreatHit]:
    """Threat phrases in ``text``, each pattern counted once."""
    hits = []
    for rule in THREAT_PATTERNS:
        match = rule.pattern.search(str(text or ""))
        if match:
            hits.append(ThreatHit(rule.category, rule.weight, " ".join(match.group(0).split())))
    return hits


# Identifiers


@dataclass
class _Identity:
    """The principal's identifiers, normalized so they match extracted entities."""

    keys: set[tuple[str, str]]
    name_pattern: Optional[re.Pattern]
    place_pattern: Optional[re.Pattern]
    by_alias: dict[str, Place]
    places: list[Place]

    @classmethod
    def of(cls, principal: Principal) -> "_Identity":
        keys = {("phone", v) for v in map(normalize_phone, principal.phones) if v}
        keys |= {("email", v.strip().lower()) for v in principal.emails if v.strip()}
        keys |= {("handle", v.strip().lstrip("@").lower()) for v in principal.handles if v.strip()}
        keys |= {("plate", v) for v in map(normalize_plate, principal.plates) if v}
        names = [principal.name, *principal.aliases]
        # A sensitive place's name is a label ("Home"); only its aliases (the street address) are searched for.
        by_alias = {" ".join(a.split()).lower(): place for place in principal.places for a in place.aliases
                    if a.strip()}
        return cls(keys, _phrase_pattern(names), _phrase_pattern(list(by_alias)), by_alias, list(principal.places))

    def names_principal(self, text: str) -> bool:
        return bool(self.name_pattern and self.name_pattern.search(text))

    def find_places(self, text: str) -> list[tuple[Place, str]]:
        """Sensitive places whose aliases appear in ``text``, with the matched text, once per place."""
        if self.place_pattern is None:
            return []
        found: dict[str, tuple[Place, str]] = {}
        for match in self.place_pattern.finditer(str(text)):
            place = self.by_alias[" ".join(match.group(0).split()).lower()]
            found.setdefault(place.name, (place, match.group(0)))
        return list(found.values())


def _phrase_pattern(phrases: Iterable[str]) -> Optional[re.Pattern]:
    """Whole-word, case-insensitive, any-whitespace pattern for ``phrases``, longest first."""
    cleaned = sorted({" ".join(p.split()) for p in phrases if p.strip()}, key=len, reverse=True)
    if not cleaned:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(r"\s+".join(map(re.escape, p.split())) for p in cleaned)
                      + r")(?!\w)", re.IGNORECASE)


def normalize_identifier(kind: str, value: str) -> str:
    """Normalize a principal identifier the way extraction normalizes evidence."""
    value = value.strip()
    if kind == "phone":
        normalized = normalize_phone(value)
    elif kind == "plate":
        normalized = normalize_plate(value)
    elif kind == "handle":
        normalized = value.lstrip("@").lower() or None
    elif kind == "email":
        normalized = value.lower() if "@" in value else None
    else:
        raise ValueError(f"unknown identifier kind {kind!r}")
    if not normalized:
        raise ValueError(f"{value!r} doesn't look like a {kind}")
    return normalized


# Analysis


@dataclass
class _Exposure:
    kind: str
    value: str
    label: str
    items: dict[str, str] = field(default_factory=dict)  # evidence id -> why
    sources: set[str] = field(default_factory=set)
    linked: set[str] = field(default_factory=set)
    geotagged: set[str] = field(default_factory=set)
    when: dict[str, datetime] = field(default_factory=dict)

    def add(self, evidence_id: str, source: str, why: str, linked: bool) -> None:
        self.items.setdefault(evidence_id, why)
        self.sources.add(source)
        if linked:
            self.linked.add(evidence_id)


@dataclass
class DefenderAnalysis:
    """Everything the defender pass found, plus the ordinary correlation underneath it."""

    leads: list[Lead]
    correlation: CaseAnalysis

    @property
    def threats(self) -> list[Lead]:
        return [lead for lead in self.leads if lead.identifier.startswith("threat-")]

    @property
    def exposures(self) -> list[Lead]:
        return [lead for lead in self.leads if not lead.identifier.startswith("threat-")]


def _excerpt(text: str, limit: int = 90) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _when(item: Evidence) -> Optional[datetime]:
    meta = item.metadata or {}
    return parse_datetime(str(meta.get("timestamp", ""))) or parse_datetime(str(item.content))


def _coords(item: Evidence) -> list[tuple[float, float]]:
    meta = item.metadata or {}
    if "latitude" in meta and "longitude" in meta:
        return [(float(meta["latitude"]), float(meta["longitude"]))]
    return [tuple(float(v) for v in e.value.split(",")) for e in entities_of(item) if e.kind == "coords"]


def analyze_principal(principal: Principal, evidence: Sequence[Evidence],
                      places: Sequence[Place] = ()) -> DefenderAnalysis:
    """Run the exposure, pattern-of-life and threat passes for ``principal``.

    ``places`` is the case's ordinary gazetteer. The principal's sensitive
    places stay out of it on purpose, so they never land in an exported map.
    """
    identity = _Identity.of(principal)
    correlation = analyze_case(evidence, places=places)
    exposures: dict[tuple[str, str], _Exposure] = {}

    def exposure(kind: str, value: str, label: str) -> _Exposure:
        return exposures.setdefault((kind, value), _Exposure(kind, value, label))

    for item in evidence:
        text, source = scan_text(item), source_of(item)
        entities = {(e.kind, e.value): e for e in entities_of(item)}
        own = [key for key in entities if key in identity.keys]
        named = identity.names_principal(text)
        linked_ids = named or len(own) > 1
        # A page you browsed: say which site, since that's where the opt-out goes.
        site = f"on {item.metadata['url']}: " if (item.metadata or {}).get("url") else ""
        for key in own:
            raw = entities[key].raw or key[1]
            exposure(*key, f"{_LABELS[key[0]]} {key[1]}").add(
                item.identifier, source,
                f'{site}"{_excerpt(text)}"' + (f" (written {raw})" if raw != key[1] else ""), linked_ids)

        for place, raw in identity.find_places(text):
            entry = exposure("place", place.name, f"Sensitive place {place.name}")
            alias = " ".join(raw.split()).lower() != place.name.lower()
            entry.add(item.identifier, source, f'{site}names {place.name}{f" as {raw!r}" if alias else ""}: '
                                               f'"{_excerpt(text)}"', named or bool(own))
        for lat, lon in _coords(item):
            for place in identity.places:
                distance_m = haversine_km(place.latitude, place.longitude, lat, lon) * 1000
                if distance_m <= place.radius_m:
                    entry = exposure("place", place.name, f"Sensitive place {place.name}")
                    entry.add(item.identifier, source,
                              f"geotagged {distance_m:.0f} m from {place.name} (radius {place.radius_m:g} m)",
                              named or bool(own))
                    entry.geotagged.add(item.identifier)
                    when = _when(item)
                    if when:
                        entry.when[item.identifier] = when

    by_id = {item.identifier: item for item in evidence}
    leads = [_exposure_lead(e) for e in exposures.values()]
    leads += _pattern_leads(correlation, identity, by_id)
    leads += _threat_leads(evidence, identity)
    leads.sort(key=lambda lead: (-lead.score, lead.identifier))
    return DefenderAnalysis(leads=leads, correlation=correlation)


def _exposure_lead(e: _Exposure) -> Lead:
    weight = EXPOSURE_WEIGHTS[e.kind]
    count, sources, linked = len(e.items), sorted(e.sources), len(e.linked)
    score = weight * count + SOURCE_DIVERSITY_BONUS * (len(sources) - 1) + LINK_BONUS * linked
    reasons = [f"{_plural(count, 'evidence item')} across {_plural(len(sources), 'source')} ({', '.join(sources)}): "
               f"{weight:g} × {count} + {SOURCE_DIVERSITY_BONUS:g} × {len(sources) - 1} + {LINK_BONUS:g} × {linked} "
               f"linked"]
    if linked:
        reasons.append(f"{linked} of {count} also name the principal or carry another of their identifiers, "
                       "so they tie this to them")
    reasons += [f"{eid}: {why}" for eid, why in e.items.items()]
    if e.kind == "place":
        routine = _routine(list(e.when.values()))
        if routine:
            score += ROUTINE_BONUS
            reasons.append(f"routine: {routine} (+{ROUTINE_BONUS:g})")
        fix_keys = (["place_geo"] if e.geotagged else []) + (["place_text"] if len(e.geotagged) < count else [])
        if routine:
            fix_keys.append("routine")
        summary = f"{e.label} is exposed in {_plural(count, 'item')} across {_plural(len(sources), 'source')}"
    else:
        fix_keys = [e.kind]
        summary = f"{e.label} is exposed in {_plural(count, 'item')} across {_plural(len(sources), 'source')}"
    reasons += [f"Fix: {FIXES[key]}" for key in fix_keys]
    return Lead(
        identifier=f"exp-{e.kind}-{e.value}",
        summary=summary,
        evidence_ids=list(e.items),
        score=round(score, 2),
        reasons=reasons,
        entities=[Entity(kind=e.kind, value=e.value)],
        source="analysis",
    )


def touches_sensitive(latitude: float, longitude: float, spread_m: float, places: Sequence[Place]) -> bool:
    """True if a circle of ``spread_m`` around the point overlaps any sensitive place."""
    return any(haversine_km(place.latitude, place.longitude, latitude, longitude) * 1000 <= place.radius_m + spread_m
               for place in places)


def _routine(times: Sequence[datetime]) -> str:
    """Describe the most repeated weekday + time-of-day slot, if anything repeats."""
    best: list[datetime] = []
    for anchor in times:
        group = [t for t in times if t.weekday() == anchor.weekday()
                 and abs((t.hour * 60 + t.minute) - (anchor.hour * 60 + anchor.minute)) <= ROUTINE_WINDOW_HOURS * 60]
        if len(group) > len(best):
            best = group
    if len(best) < 2 or len({t.date() for t in best}) < 2:
        return ""
    minutes = sorted(t.hour * 60 + t.minute for t in best)
    median = minutes[len(minutes) // 2]
    return f"{best[0]:%A}s around {median // 60:02d}:{median % 60:02d} ({len(best)} times)"


def _pattern_leads(correlation: CaseAnalysis, identity: _Identity, by_id: dict[str, Evidence]) -> list[Lead]:
    leads = []
    for cluster in correlation.clusters:
        ids = list(dict.fromkeys(p.evidence_id for p in cluster.points))
        if len(ids) < 2:
            continue
        # Clusters on a sensitive place are already exposure leads.
        if touches_sensitive(cluster.latitude, cluster.longitude, cluster.radius_km * 1000, identity.places):
            continue
        sources = sorted({p.source for p in cluster.points})
        centre = f"{cluster.latitude:.5f},{cluster.longitude:.5f}"
        named = cluster.place_names
        where = named[0] if named else centre
        score = PATTERN_WEIGHT * len(ids) + SOURCE_DIVERSITY_BONUS * (len(sources) - 1)
        reasons = [f"{_plural(len(ids), 'evidence item')} within {cluster.radius_km * 1000:.0f} m across "
                   f"{_plural(len(sources), 'source')} ({', '.join(sources)}): {PATTERN_WEIGHT:g} × {len(ids)} + "
                   f"{SOURCE_DIVERSITY_BONUS:g} × {len(sources) - 1}",
                   "not a registered sensitive place"]
        times = [t for t in (_when(by_id[eid]) for eid in ids if eid in by_id) if t]
        routine = _routine(times)
        if routine:
            score += ROUTINE_BONUS
            reasons.append(f"routine: {routine} (+{ROUTINE_BONUS:g})")
        reasons += [f"{eid}: \"{_excerpt(by_id[eid].content)}\"" for eid in ids if eid in by_id]
        reasons.append(f"Fix: {FIXES['pattern']}")
        if routine:
            reasons.append(f"Fix: {FIXES['routine']}")
        kind = "Routine" if routine else "Pattern of life"
        leads.append(Lead(
            identifier=f"geo-{centre}",  # same id as the map's cluster, so reviews carry over as it drifts
            summary=f"{kind}: {_plural(len(ids), 'item')} place the principal at {where}"
                    + (f", {routine.split(' (')[0]}" if routine else ""),
            evidence_ids=ids,
            score=round(score, 2),
            reasons=reasons,
            entities=[Entity(kind="coords", value=centre)],
            source="analysis",
        ))
    return leads


def _actor(item: Evidence, identity: _Identity) -> tuple[str, str]:
    """Who sent an item, as (key, display name): its sender, else the first non-principal contact in it."""
    sender = " ".join(str((item.metadata or {}).get("sender") or "").split())
    if sender:
        return sender.lower(), sender
    for entity in entities_of(item):
        if entity.kind in ("phone", "email", "handle") and (entity.kind, entity.value) not in identity.keys:
            return f"{entity.kind}:{entity.value}", entity.value
    return f"item:{item.identifier}", f"an unattributed message ({item.identifier})"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9@._:+-]+", "-", text.lower()).strip("-") or "unknown"


def _threat_leads(evidence: Sequence[Evidence], identity: _Identity) -> list[Lead]:
    by_actor: dict[str, list[tuple[Evidence, list[ThreatHit]]]] = {}
    display: dict[str, str] = {}
    for item in evidence:
        hits = threat_hits(scan_text(item))
        if hits:
            key, name = _actor(item, identity)
            display.setdefault(key, name)
            by_actor.setdefault(key, []).append((item, hits))

    leads = []
    for actor, messages in by_actor.items():
        dated = [(when, item, hits) for item, hits in messages if (when := _when(item))]
        dated.sort(key=lambda row: row[0])
        ordered = [(item, hits) for _, item, hits in dated] + [m for m in messages if not _when(m[0])]
        total = sum(h.weight for _, hits in ordered for h in hits)
        categories = sorted({h.category for _, hits in ordered for h in hits}, key=_CATEGORY_ORDER.get)
        score = THREAT_BASE + total
        reasons = [f"{_plural(len(ordered), 'threatening message')}: {THREAT_BASE:g} base + {total:g} from the "
                   f"phrases ({', '.join(categories)})"]

        known = _known_places(ordered, identity)
        if known:
            score += KNOWS_LOCATION_BONUS
            reasons.append(f"KNOWS A SENSITIVE LOCATION: {', '.join(known)} (+{KNOWS_LOCATION_BONUS:g})")
        escalation = _escalation([(when, hits) for when, _, hits in dated])
        if escalation:
            score += ESCALATION_BONUS
            reasons.append(f"ESCALATING: {escalation} (+{ESCALATION_BONUS:g})")

        for item, hits in ordered:
            when = _when(item)
            stamp = f"{when:%Y-%m-%d %H:%M} " if when and f"{when:%Y-%m-%d %H:%M}" not in str(item.content) else ""
            phrases = "; ".join(f"{h.category}: \"{h.phrase}\"" for h in hits)
            reasons.append(f"{item.identifier}: {stamp}[{phrases}] \"{_excerpt(item.content)}\"")
        reasons.append(f"Fix: {FIXES['threat']}")
        if known:
            reasons.append(f"Fix: {FIXES['threat_location']}")

        who = display[actor]
        flags = [flag for flag, on in (("knows a sensitive location", known), ("escalating", escalation)) if on]
        leads.append(Lead(
            identifier=f"threat-{_slug(actor)}",
            summary=f"Threats from {who}: {_plural(len(ordered), 'message')} ({', '.join(categories)})"
                    + (f", {', '.join(flags)}" if flags else ""),
            evidence_ids=[item.identifier for item, _ in ordered],
            score=round(score, 2),
            reasons=reasons,
            entities=[Entity(kind="actor", value=actor)],
            source="analysis",
        ))
    return leads


def _known_places(messages: Iterable[tuple[Evidence, list[ThreatHit]]], identity: _Identity) -> list[str]:
    """Sensitive places a threatening message names, or is geotagged at."""
    known: dict[str, None] = {}
    for item, _ in messages:
        for place, _raw in identity.find_places(scan_text(item)):
            known[place.name] = None
        for lat, lon in _coords(item):
            for place in identity.places:
                if haversine_km(place.latitude, place.longitude, lat, lon) * 1000 <= place.radius_m:
                    known[place.name] = None
    return list(known)


def _escalation(dated: Sequence[tuple[datetime, list[ThreatHit]]]) -> str:
    """Spot severity climbing or messages coming faster, across a sender's dated threats."""
    if len(dated) < 3:
        return ""
    severity = [max(h.weight for h in hits) for _, hits in dated]
    half = len(severity) // 2
    early, late = severity[:half], severity[half:]
    notes = []
    if max(late) > max(early):
        top = max(dated[half:], key=lambda row: max(h.weight for h in row[1]))
        worst = max(top[1], key=lambda h: h.weight)
        notes.append(f"severity rose from {_worst_category(dated[:half])} to {worst.category}")
    gaps = [(b[0] - a[0]).total_seconds() for a, b in zip(dated, dated[1:])]
    if gaps[0] > 0 and gaps[-1] < gaps[0] / 2:
        notes.append(f"gap between messages shrank from {_span(gaps[0])} to {_span(gaps[-1])}")
    return "; ".join(notes)


def _worst_category(rows: Sequence[tuple[datetime, list[ThreatHit]]]) -> str:
    return max((h for _, hits in rows for h in hits), key=lambda h: h.weight).category


def _span(seconds: float) -> str:
    if seconds >= 86400 * 2:
        return f"{seconds / 86400:.0f} days"
    if seconds >= 3600 * 2:
        return f"{seconds / 3600:.0f} hours"
    return f"{max(seconds / 60, 1):.0f} min"


def risk_level(leads: Sequence[Lead], statuses: Optional[dict[str, str]] = None) -> tuple[str, str]:
    """An overall risk level and the reason for it, from the leads still open or verified.

    ``statuses`` maps lead ids to review status; dismissed leads don't count.
    """
    statuses = statuses or {}
    live = [lead for lead in leads if statuses.get(lead.identifier, "open") != "dismissed"]
    threats = [lead for lead in live if lead.identifier.startswith("threat-")]
    knows = [lead for lead in threats if any(r.startswith("KNOWS A SENSITIVE LOCATION") for r in lead.reasons)]
    if knows:
        return "CRITICAL", f"a threatening sender knows a sensitive location ({knows[0].summary})"
    violent = [lead for lead in threats if "violence" in lead.summary or "ESCALATING" in " ".join(lead.reasons)]
    if violent:
        return "HIGH", f"violent or escalating threats ({violent[0].summary})"
    places = [lead for lead in live if lead.identifier.startswith("exp-place-")]
    if threats or places:
        top = (threats or places)[0]
        return "ELEVATED", top.summary
    if live:
        return "GUARDED", f"{_plural(len(live), 'exposure')} to clean up"
    return "LOW", "nothing open"
