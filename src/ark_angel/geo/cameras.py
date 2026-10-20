"""Video canvass: mapped cameras near a case's location leads, via OpenStreetMap.

Footage is often the fastest way to confirm a sighting, and most recorders
overwrite themselves within weeks. This lists the cameras volunteers have
mapped in OpenStreetMap (``man_made=surveillance``: shop and street cameras,
traffic cameras, licence-plate readers) around each location lead, nearest
first, so someone can go and ask the owner for footage while it still exists.

It never connects to a camera. It reads the public OSM map only. Coverage is
whatever volunteers have mapped: no camera listed does not mean no camera.

The query is opt-in. It sends the search circles (centre and radius) to the
Overpass API and nothing else, but those circles are the places the case cares
about, so ``endpoint`` can point at a self-hosted Overpass instead.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

import requests

from ark_angel.analysis.correlate import haversine_km

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_HEADERS = {"User-Agent": "ArkAngel-OSINT/0.1 (open-source missing persons research tool)"}
_TIMEOUT = 40
DEFAULT_RADIUS_M = 300.0
# A camera mapped this close to the spot is at it; which way it points says nothing.
_AT_SPOT_M = 25.0
# How far off its mapped direction a camera can be and still count as pointing at the spot.
_FACING_TOLERANCE = 60.0
RETENTION_NOTE = ("Many recorders overwrite footage within one to four weeks. "
                  "Ask for preservation first, viewing second.")

_CARDINALS = {name: i * 22.5 for i, name in enumerate(
    "N NNE NE ENE E ESE SE SSE S SSW SW WSW W WNW NW NNW".split())}


@dataclass
class Camera:
    """One mapped surveillance device from OpenStreetMap."""

    osm_type: str
    osm_id: int
    latitude: float
    longitude: float
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        """``ALPR`` for plate readers, ``guard`` for staffed posts, else the camera type."""
        kind = self.tags.get("surveillance:type", "camera")
        if kind.upper() == "ALPR":
            return "ALPR"
        if kind != "camera":
            return kind
        return f"{self.tags['camera:type']} camera" if self.tags.get("camera:type") else "camera"

    @property
    def operator(self) -> str:
        return self.tags.get("operator") or self.tags.get("brand") or self.tags.get("name") or ""

    @property
    def zone(self) -> str:
        """What it watches (``traffic``, ``parking``, ``shop``...) and whether it's public/outdoor/indoor."""
        return ", ".join(v for v in (self.tags.get("surveillance:zone"), self.tags.get("surveillance")) if v)

    @property
    def direction(self) -> Optional[float]:
        """Mapped viewing direction in degrees (``camera:direction``/``direction``), if any."""
        raw = (self.tags.get("camera:direction") or self.tags.get("direction") or "").split(";")[0].strip()
        if raw.upper() in _CARDINALS:
            return _CARDINALS[raw.upper()]
        try:
            return float(raw) % 360
        except ValueError:
            return None

    @property
    def osm_url(self) -> str:
        return f"https://www.openstreetmap.org/{self.osm_type}/{self.osm_id}"


@dataclass
class CanvassHit:
    camera: Camera
    distance_m: float
    facing: Optional[bool]  # None when the camera has no mapped direction or sits at the spot


@dataclass
class CanvassTarget:
    """A spot to canvass: a location lead, the latest geotagged activity, or a point given by hand."""

    label: str
    latitude: float
    longitude: float
    radius_m: float
    lead_rank: Optional[int] = None
    lead_id: str = ""
    latest: str = ""  # the most recent dated evidence at this spot
    hits: list[CanvassHit] = field(default_factory=list)


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial compass bearing from point 1 to point 2."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlmb = math.radians(lon2 - lon1)
    x = math.sin(dlmb) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlmb)
    return math.degrees(math.atan2(x, y)) % 360


def _facing(camera: Camera, target: CanvassTarget, distance_m: float) -> Optional[bool]:
    direction = camera.direction
    if direction is None or distance_m < _AT_SPOT_M:
        return None
    to_spot = bearing_deg(camera.latitude, camera.longitude, target.latitude, target.longitude)
    return abs((direction - to_spot + 180) % 360 - 180) <= _FACING_TOLERANCE


def targets_from_report(report: dict[str, Any], radius_m: float = DEFAULT_RADIUS_M,
                        lead_ids: Optional[Iterable[str]] = None) -> list[CanvassTarget]:
    """Spots worth canvassing, in lead order.

    By default: every location cluster that is an open or verified lead, plus
    the latest dated geotagged evidence if no cluster covers it. With
    ``lead_ids``, only those clusters (dismissed or not).
    """
    leads = {lead["identifier"]: lead for lead in report["leads"]}
    wanted = set(lead_ids) if lead_ids is not None else None
    when = {event["evidence_id"]: event["when"] for event in report["timeline"]}
    targets = []
    for cluster in report["clusters"]:
        lead = leads.get(cluster.get("lead_id", ""))
        if lead is None or (wanted is not None and lead["identifier"] not in wanted) \
                or (wanted is None and lead["status"] == "dismissed"):
            continue
        dated = [when[p["evidence_id"]] for p in cluster["points"] if p["evidence_id"] in when]
        targets.append(CanvassTarget(
            label=f"#{lead['rank']} " + (cluster["places"][0] if cluster["places"] else "location cluster"),
            latitude=cluster["latitude"], longitude=cluster["longitude"],
            radius_m=radius_m + cluster["radius_m"], lead_rank=lead["rank"], lead_id=lead["identifier"],
            latest=max(dated, default="")))
    targets.sort(key=lambda t: t.lead_rank)
    if wanted is not None:
        return targets

    dated_points = [(when[p["evidence_id"]], p) for c in report["clusters"] for p in c["points"]
                    if p["evidence_id"] in when]
    if dated_points:
        latest, point = max(dated_points, key=lambda item: item[0])
        covered = any(haversine_km(t.latitude, t.longitude, point["latitude"], point["longitude"]) * 1000
                      <= t.radius_m for t in targets)
        if not covered:
            targets.append(CanvassTarget(f"latest geotagged activity ({point['evidence_id']})",
                                         point["latitude"], point["longitude"], radius_m, latest=latest))
    return targets


def overpass_query(targets: Sequence[CanvassTarget]) -> str:
    """One Overpass QL query covering every target's search circle."""
    clauses = "".join(f'nwr["man_made"="surveillance"](around:{t.radius_m:.0f},{t.latitude:.6f},{t.longitude:.6f});'
                      for t in targets)
    return f"[out:json][timeout:25];({clauses});out center tags;"


def fetch_cameras(targets: Sequence[CanvassTarget], endpoint: str = OVERPASS_URL) -> list[Camera]:
    """Mapped cameras within any target's circle.

    Raises:
        ValueError: on network failure or an unexpected response.
    """
    if not targets:
        return []
    try:
        resp = requests.post(endpoint, data={"data": overpass_query(targets)}, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        elements = resp.json().get("elements", [])
    except (requests.RequestException, ValueError, AttributeError) as exc:
        raise ValueError(f"camera lookup failed: {exc}") from exc
    cameras = []
    for element in elements:
        where = element if "lat" in element else element.get("center") or {}
        try:
            cameras.append(Camera(str(element["type"]), int(element["id"]), float(where["lat"]),
                                  float(where["lon"]), dict(element.get("tags") or {})))
        except (KeyError, TypeError, ValueError):
            continue
    return cameras


def canvass(targets: Sequence[CanvassTarget], cameras: Iterable[Camera]) -> list[CanvassTarget]:
    """Attach each camera to every target it is within range of: nearest first, pointing away last."""
    cameras = list(cameras)
    for target in targets:
        target.hits = []
        for camera in cameras:
            distance = haversine_km(target.latitude, target.longitude, camera.latitude, camera.longitude) * 1000
            if distance <= target.radius_m:
                target.hits.append(CanvassHit(camera, round(distance), _facing(camera, target, distance)))
        target.hits.sort(key=lambda hit: (hit.facing is False, hit.distance_m))
    return list(targets)


def canvass_dicts(targets: Sequence[CanvassTarget]) -> list[dict[str, Any]]:
    """Plain-data form of a canvass, for the map exports."""
    return [
        {"label": t.label, "latitude": t.latitude, "longitude": t.longitude, "radius_m": round(t.radius_m),
         "lead_rank": t.lead_rank, "lead_id": t.lead_id, "latest": t.latest,
         "cameras": [{"osm": f"{h.camera.osm_type}/{h.camera.osm_id}", "osm_url": h.camera.osm_url,
                      "latitude": h.camera.latitude, "longitude": h.camera.longitude, "kind": h.camera.kind,
                      "operator": h.camera.operator, "zone": h.camera.zone, "direction": h.camera.direction,
                      "distance_m": h.distance_m, "facing": h.facing} for h in t.hits]}
        for t in targets
    ]
