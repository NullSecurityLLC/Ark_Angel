"""Cross-source correlation: the part that actually surfaces leads.

Every evidence item is broken into normalized entities. Anything that shows up
in more than one place (the same phone written three ways, a handle in a tip
and in a username hit, two photos taken a few hundred metres apart, a tip
naming a landmark next to a geotagged photo) becomes a ranked, explained lead.
Nothing here is a black box: every score comes with the reasons behind it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Sequence

from ark_angel.analysis.base import Analyzer
from ark_angel.analysis.extract import extract_entities, parse_datetime
from ark_angel.analysis.places import PlaceMatcher
from ark_angel.models import Case, Entity, Evidence, Lead, Place

# How much a single corroborating evidence item is worth, by entity kind.
KIND_WEIGHTS = {"phone": 3.0, "email": 3.0, "plate": 3.0, "handle": 2.5, "place": 2.0, "url": 1.5}
GEO_WEIGHT = 2.5
SOURCE_DIVERSITY_BONUS = 2.0
LATEST_ACTIVITY_SCORE = 2.0
DEFAULT_CLUSTER_RADIUS_KM = 1.0
# Lead identifier prefixes produced by cross-source correlation.
CORRELATED_PREFIXES = ("corr-", "geo-", "timeline-")

_KIND_LABELS = {"phone": "Phone", "email": "Email", "plate": "Plate", "handle": "Handle", "place": "Place",
                "url": "URL"}


@dataclass
class Mention:
    """One place an entity was seen."""

    evidence_id: str
    source: str
    excerpt: str


@dataclass
class GeoPoint:
    evidence_id: str
    source: str
    latitude: float
    longitude: float
    label: str
    place: Optional[str] = None  # set when the point comes from a named place, not raw coordinates
    matched: Optional[str] = None  # the text that matched the place, e.g. an alias like "the station"


@dataclass
class GeoCluster:
    points: list[GeoPoint] = field(default_factory=list)

    @property
    def latitude(self) -> float:
        return sum(p.latitude for p in self.points) / len(self.points)

    @property
    def longitude(self) -> float:
        return sum(p.longitude for p in self.points) / len(self.points)

    @property
    def radius_km(self) -> float:
        return max(haversine_km(self.latitude, self.longitude, p.latitude, p.longitude) for p in self.points)

    @property
    def place_names(self) -> list[str]:
        """Named places in the cluster, most-mentioned first."""
        counts: dict[str, int] = {}
        for point in self.points:
            if point.place:
                counts[point.place] = counts.get(point.place, 0) + 1
        return sorted(counts, key=lambda name: (-counts[name], name))


@dataclass
class TimelineEvent:
    when: datetime
    evidence_id: str
    source: str
    text: str


@dataclass
class CaseAnalysis:
    """Everything the correlation pass learned about a case."""

    leads: list[Lead]
    entity_index: dict[tuple[str, str], list[Mention]]
    clusters: list[GeoCluster]
    points: list[GeoPoint]
    timeline: list[TimelineEvent]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in kilometres."""
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def source_of(item: Evidence) -> str:
    """Where an evidence item came from (file, photo set, lookup...)."""
    meta = item.metadata or {}
    return str(meta.get("source") or item.identifier.split(":", 1)[0])


def entities_of(item: Evidence) -> list[Entity]:
    """Entities in an evidence item's text plus any carried in its metadata."""
    entities = extract_entities(str(item.content))
    meta = item.metadata or {}
    handle = meta.get("handle")
    if handle and not any(e.kind == "handle" and e.value == handle.lower() for e in entities):
        entities.append(Entity(kind="handle", value=str(handle).lower(), raw=str(handle)))
    return entities


def cluster_points(points: Sequence[GeoPoint], radius_km: float = DEFAULT_CLUSTER_RADIUS_KM) -> list[GeoCluster]:
    """Greedy clustering: a point joins the first cluster whose centre is within ``radius_km``."""
    clusters: list[GeoCluster] = []
    for point in points:
        for cluster in clusters:
            if haversine_km(cluster.latitude, cluster.longitude, point.latitude, point.longitude) <= radius_km:
                cluster.points.append(point)
                break
        else:
            clusters.append(GeoCluster(points=[point]))
    return clusters


def _excerpt(text: str, limit: int = 90) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _add_mention(entity_index: dict, key: tuple[str, str], mention: Mention) -> None:
    mentions = entity_index.setdefault(key, [])
    if not any(m.evidence_id == mention.evidence_id for m in mentions):
        mentions.append(mention)


class _Scan:
    """Accumulates entities, geo points and dated events while walking evidence."""

    def __init__(self, places: Sequence[Place], radius_km: float) -> None:
        self.entity_index: dict[tuple[str, str], list[Mention]] = {}
        self.raw_forms: dict[tuple[str, str], set[str]] = {}
        self.points: list[GeoPoint] = []
        self.timeline: list[TimelineEvent] = []
        self.coarse: dict[str, Place] = {}
        self.matcher = PlaceMatcher(places)
        self.precise_m = radius_km * 1000

    def mention(self, key: tuple[str, str], raw: str, item: Evidence, source: str) -> None:
        _add_mention(self.entity_index, key, Mention(item.identifier, source, _excerpt(str(item.content))))
        self.raw_forms.setdefault(key, set()).add(raw)

    def add(self, item: Evidence) -> None:
        source = source_of(item)
        text = str(item.content)
        meta = item.metadata or {}
        has_meta_coords = "latitude" in meta and "longitude" in meta

        for entity in entities_of(item):
            if entity.kind != "coords":
                self.mention((entity.kind, entity.value), entity.raw, item, source)
            elif not has_meta_coords:  # image text repeats its metadata coords; count them once
                lat, lon = (float(v) for v in entity.value.split(","))
                self.points.append(GeoPoint(item.identifier, source, lat, lon, _excerpt(text, 60)))

        for place, raw in self.matcher.find(text):
            self.mention(("place", place.name), raw, item, source)
            if place.radius_m <= self.precise_m:
                self.points.append(GeoPoint(item.identifier, source, place.latitude, place.longitude,
                                            _excerpt(text, 60), place=place.name, matched=raw))
            else:
                self.coarse[place.name] = place

        if has_meta_coords:
            self.points.append(GeoPoint(item.identifier, source, float(meta["latitude"]),
                                        float(meta["longitude"]), item.identifier))

        when = parse_datetime(str(meta.get("timestamp", ""))) or parse_datetime(text)
        if when:
            self.timeline.append(TimelineEvent(when, item.identifier, source, _excerpt(text, 140)))

    def attach_coarse_places(self) -> None:
        """Geotagged evidence inside a coarse place the text mentions is another mention of it."""
        for place in self.coarse.values():
            for point in self.points:
                distance = haversine_km(place.latitude, place.longitude, point.latitude, point.longitude)
                if point.place is None and distance * 1000 <= place.radius_m:
                    _add_mention(self.entity_index, ("place", place.name), Mention(
                        point.evidence_id, point.source,
                        f"geotagged {distance:.1f} km from {place.name}, inside its "
                        f"{place.radius_m / 1000:g} km radius"))


def analyze_case(evidence_items: Sequence[Evidence], radius_km: float = DEFAULT_CLUSTER_RADIUS_KM,
                 places: Sequence[Place] = ()) -> CaseAnalysis:
    """Run entity correlation, geo clustering and timeline building over evidence.

    ``places`` is the case gazetteer. Mentions of a precise place (radius up to
    ``radius_km``) become geo points and join clusters. Mentions of a coarse
    place (a city) become ``place`` entities, and geotagged evidence inside that
    place's radius counts as another mention of it.
    """
    scan = _Scan(places, radius_km)
    for item in evidence_items:
        scan.add(item)
    scan.attach_coarse_places()
    entity_index, raw_forms, points, timeline = scan.entity_index, scan.raw_forms, scan.points, scan.timeline

    timeline.sort(key=lambda event: event.when)
    clusters = cluster_points(points, radius_km)

    precise = {("place", p.name) for p in places if p.radius_m <= radius_km * 1000}
    leads = (_entity_leads(entity_index, raw_forms, skip=precise) + _geo_leads(clusters)
             + _timeline_leads(timeline))
    leads.sort(key=lambda lead: lead.score, reverse=True)
    return CaseAnalysis(leads=leads, entity_index=entity_index, clusters=clusters, points=points, timeline=timeline)


def _entity_leads(entity_index: dict, raw_forms: dict, skip: frozenset | set = frozenset()) -> list[Lead]:
    leads = []
    for (kind, value), mentions in entity_index.items():
        # Precise places are scored through their geo cluster instead, so they aren't counted twice.
        if len(mentions) < 2 or (kind, value) in skip:
            continue
        sources = sorted({m.source for m in mentions})
        weight = KIND_WEIGHTS.get(kind, 1.0)
        score = weight * len(mentions) + SOURCE_DIVERSITY_BONUS * (len(sources) - 1)
        label = _KIND_LABELS.get(kind, kind.title())
        reasons = [
            f"{_plural(len(mentions), 'evidence item')} across {_plural(len(sources), 'source')} "
            f"({', '.join(sources)}): {weight:g} × {len(mentions)} + {SOURCE_DIVERSITY_BONUS:g} × {len(sources) - 1}"
        ]
        forms = sorted(raw_forms.get((kind, value), set()))
        if len(forms) > 1:
            reasons.append(f"written {len(forms)} different ways: {' | '.join(forms)}")
        reasons += [f"{m.evidence_id}: \"{m.excerpt}\"" for m in mentions]
        leads.append(Lead(
            identifier=f"corr-{kind}-{value}",
            summary=f"{label} {value} appears in {_plural(len(mentions), 'item')} across {_plural(len(sources), 'source')}",
            evidence_ids=[m.evidence_id for m in mentions],
            score=round(score, 2),
            reasons=reasons,
            entities=[Entity(kind=kind, value=value)],
            source="analysis",
        ))
    return leads


def _geo_leads(clusters: Sequence[GeoCluster]) -> list[Lead]:
    leads = []
    for cluster in clusters:
        ids = list(dict.fromkeys(p.evidence_id for p in cluster.points))
        if len(ids) < 2:
            continue
        sources = sorted({p.source for p in cluster.points})
        score = GEO_WEIGHT * len(ids) + SOURCE_DIVERSITY_BONUS * (len(sources) - 1)
        centre = f"{cluster.latitude:.5f},{cluster.longitude:.5f}"
        named = cluster.place_names
        where = f"around {named[0]}" if named else f"near {centre}"
        reasons = [
            f"{_plural(len(ids), 'evidence item')} within {cluster.radius_km * 1000:.0f} m of each other "
            f"across {_plural(len(sources), 'source')} ({', '.join(sources)})"
        ]
        if named:
            reasons.append(f"named places: {', '.join(named)}")
        reasons += [_point_reason(p) for p in cluster.points]
        leads.append(Lead(
            identifier=f"geo-{centre}",
            summary=f"Location cluster {where}: {_plural(len(ids), 'item')} within "
                    f"{cluster.radius_km * 1000:.0f} m",
            evidence_ids=ids,
            score=round(score, 2),
            reasons=reasons,
            entities=[Entity(kind="coords", value=centre)],
            source="analysis",
        ))
    return leads


def _point_reason(point: GeoPoint) -> str:
    if not point.place:
        return f"{point.evidence_id} @ {point.latitude:.5f}, {point.longitude:.5f}"
    alias = point.matched and " ".join(point.matched.split()).lower() != point.place.lower()
    return f"{point.evidence_id} names {point.place}" + (f' as "{point.matched}"' if alias else "")


def _timeline_leads(timeline: Sequence[TimelineEvent]) -> list[Lead]:
    if not timeline:
        return []
    latest = timeline[-1]
    return [Lead(
        identifier="timeline-latest",
        summary=f"Most recent dated activity ({latest.when:%Y-%m-%d %H:%M}) from {latest.source}",
        evidence_ids=[latest.evidence_id],
        score=LATEST_ACTIVITY_SCORE,
        reasons=[f"latest of {_plural(len(timeline), 'dated item')}", f"{latest.evidence_id}: \"{latest.text}\""],
        source="analysis",
    )]


class CorrelationAnalyzer(Analyzer):
    """:class:`Analyzer` wrapper around :func:`analyze_case`."""

    def __init__(self, radius_km: float = DEFAULT_CLUSTER_RADIUS_KM) -> None:
        self.radius_km = radius_km
        self.last_analysis: Optional[CaseAnalysis] = None

    def analyze_evidence(self, case: Case, evidence_items: Sequence[Evidence]) -> Sequence[Lead]:
        self.last_analysis = analyze_case(evidence_items, self.radius_km, case.places)
        return self.last_analysis.leads

    def score_lead(self, lead: Lead) -> float:
        return lead.score
