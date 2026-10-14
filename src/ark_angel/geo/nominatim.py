"""Opt-in place lookup via OpenStreetMap Nominatim.

This is the only part of place handling that touches the network. It sends just
the query text the investigator typed, never case evidence, and results are
saved to the case gazetteer only when the investigator picks one. Nominatim's
usage policy allows about one request per second with an identifying User-Agent.
"""
from __future__ import annotations

import requests

from ark_angel.analysis.correlate import haversine_km
from ark_angel.models import Place

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_HEADERS = {"User-Agent": "ArkAngel-OSINT/0.1 (open-source missing persons research tool)"}
_TIMEOUT = 10
MIN_RADIUS_M = 100.0


def _radius_from_bbox(bbox) -> float:
    """Half the diagonal of Nominatim's ``[south, north, west, east]`` box, in metres."""
    try:
        south, north, west, east = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return 250.0
    return max(haversine_km(south, west, north, east) * 1000 / 2, MIN_RADIUS_M)


def search(query: str, limit: int = 5) -> list[tuple[Place, str]]:
    """Return ``(place, display name)`` candidates for ``query``.

    Each place is named after ``query`` so it matches the way the evidence
    writes it, not OpenStreetMap's full address string.

    Raises:
        ValueError: on network failure or an unexpected response.
    """
    try:
        resp = requests.get(NOMINATIM_URL, params={"q": query, "format": "jsonv2", "limit": limit},
                            headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        results = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise ValueError(f"place lookup failed: {exc}") from exc
    candidates = []
    for result in results:
        try:
            place = Place(name=query, latitude=float(result["lat"]), longitude=float(result["lon"]),
                          radius_m=round(_radius_from_bbox(result.get("boundingbox"))), source="nominatim")
        except (KeyError, TypeError, ValueError):
            continue
        candidates.append((place, str(result.get("display_name", ""))))
    return candidates
