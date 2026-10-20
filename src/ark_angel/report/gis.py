"""Map exports: KML for Google Earth and Google My Maps, GeoJSON for QGIS, ArcGIS and geojson.io.

Both are built from the same plain-data report as the other renderers, so the
map shows what the case file shows: each location cluster with its lead rank
and review status, every geotagged or place-named evidence item, the case
gazetteer and, when a canvass ran, the cameras near each spot.

Nothing here touches the network. Importing a file into Google My Maps uploads
it to Google, so ``include_content=False`` leaves evidence text out.
"""
from __future__ import annotations

import csv
import io
import json
import math
import xml.etree.ElementTree as ET  # nosec B405: only builds XML, never parses untrusted input
from typing import Any, Optional, Sequence

KML_NS = "http://www.opengis.net/kml/2.2"
_ICONS = "https://maps.google.com/mapfiles/kml/shapes/"
# KML colours are aabbggrr. Amber matches the HTML report's accent.
_STYLES = {
    "lead": ("ff5cb2e0", "target.png", 1.3),
    "dismissed": ("ff71848d", "target.png", 1.0),
    "evidence": ("ff71848d", "placemark_circle.png", 0.8),
    "evidence-hot": ("ff5cb2e0", "placemark_circle.png", 0.9),
    "place": ("ffd8e6ec", "placemark_square.png", 0.8),
    "camera": ("ffffffff", "camera.png", 0.9),
    "alpr": ("ff3c3cf0", "camera.png", 1.0),
    "target": ("ff5cb2e0", "star.png", 1.1),
}
_CIRCLE_MIN_M = 50.0
_FACING_WORDS = {True: "yes", False: "no", None: "unknown"}


def google_maps_url(lat: float, lon: float) -> str:
    """A Google Maps pin; no API key needed."""
    return f"https://www.google.com/maps/search/?api=1&query={lat:.6f},{lon:.6f}"


def street_view_url(lat: float, lon: float) -> str:
    """Google Street View at the nearest panorama."""
    return f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={lat:.6f},{lon:.6f}"


def circle(lat: float, lon: float, radius_m: float, steps: int = 48) -> list[tuple[float, float]]:
    """A closed ring of ``(lon, lat)`` points ``radius_m`` from the centre."""
    d = max(radius_m, _CIRCLE_MIN_M) / 6_371_000
    phi, lmb = math.radians(lat), math.radians(lon)
    ring = []
    for i in range(steps + 1):
        theta = 2 * math.pi * i / steps
        phi2 = math.asin(math.sin(phi) * math.cos(d) + math.cos(phi) * math.sin(d) * math.cos(theta))
        lmb2 = lmb + math.atan2(math.sin(theta) * math.sin(d) * math.cos(phi),
                                math.cos(d) - math.sin(phi) * math.sin(phi2))
        ring.append((round(math.degrees(lmb2), 6), round(math.degrees(phi2), 6)))
    return ring


def _features(report: dict[str, Any], canvass: Sequence[dict[str, Any]], include_content: bool) -> list[dict]:
    """Every map item as ``{layer, name, style, lat, lon, radius_m?, props}``, in layer order."""
    leads = {lead["identifier"]: lead for lead in report["leads"]}
    evidence = {item["identifier"]: item for item in report["evidence"]}
    hot = {eid for lead in leads.values() if lead["status"] != "dismissed" and lead["correlated"]
           for eid in lead["evidence_ids"]}
    features = []

    for cluster in report["clusters"]:
        lead = leads.get(cluster.get("lead_id", ""))
        if lead is None:
            continue
        lat, lon = cluster["latitude"], cluster["longitude"]
        features.append({
            "layer": "Location leads", "name": f"#{lead['rank']} {lead['summary']}",
            "style": "dismissed" if lead["status"] == "dismissed" else "lead",
            "lat": lat, "lon": lon, "radius_m": cluster["radius_m"],
            "props": {"lead_id": lead["identifier"], "rank": lead["rank"], "score": lead["score"],
                      "status": lead["status"] + (" (resurfaced)" if lead["resurfaced"] else ""),
                      "items": len(cluster["points"]), "spread_m": cluster["radius_m"],
                      "places": ", ".join(cluster["places"]), "google_maps": google_maps_url(lat, lon),
                      "street_view": street_view_url(lat, lon)},
        })

    for cluster in report["clusters"]:
        for point in cluster["points"]:
            item = evidence.get(point["evidence_id"], {})
            props = {"exhibit": item.get("exhibit", ""), "evidence_id": point["evidence_id"],
                     "source": point["source"], "timestamp": item.get("timestamp", ""),
                     "located_by": f"names {point['place']}" if point.get("place") else "coordinates"}
            if include_content:
                props["content"] = item.get("content", "")
            features.append({
                "layer": "Evidence", "name": f"{props['exhibit'] or point['evidence_id']} · {point['source']}",
                "style": "evidence-hot" if point["evidence_id"] in hot else "evidence",
                "lat": point["latitude"], "lon": point["longitude"], "props": props,
            })

    for place in report["places"]:
        features.append({
            "layer": "Places", "name": place["name"], "style": "place", "lat": place["latitude"],
            "lon": place["longitude"],
            "props": {"radius_m": place["radius_m"], "aliases": ", ".join(place["aliases"]),
                      "source": place["source"]},
        })

    seen: set[str] = set()
    for target in canvass:
        features.append({
            "layer": "Video canvass", "name": f"Canvass: {target['label']}", "style": "target",
            "lat": target["latitude"], "lon": target["longitude"], "radius_m": target["radius_m"],
            "props": {"search_radius_m": target["radius_m"], "cameras_found": len(target["cameras"]),
                      "latest_evidence_here": target["latest"], "lead_id": target["lead_id"]},
        })
        for cam in target["cameras"]:
            if cam["osm"] in seen:
                continue
            seen.add(cam["osm"])
            features.append({
                "layer": "Video canvass", "name": f"{cam['kind']}" + (f" · {cam['operator']}" if cam["operator"] else ""),
                "style": "alpr" if cam["kind"] == "ALPR" else "camera", "lat": cam["latitude"],
                "lon": cam["longitude"],
                "props": {"kind": cam["kind"], "operator": cam["operator"], "zone": cam["zone"],
                          "direction": "" if cam["direction"] is None else f"{cam['direction']:g}",
                          "near": target["label"], "distance_m": cam["distance_m"],
                          "facing_spot": _FACING_WORDS[cam["facing"]],
                          "osm": cam["osm_url"], "street_view": street_view_url(cam["latitude"], cam["longitude"])},
            })
    return features


def render_geojson(report: dict[str, Any], canvass: Sequence[dict[str, Any]] = (),
                   include_content: bool = True) -> str:
    """A GeoJSON FeatureCollection. Areas (cluster spread, canvass radius) are extra Polygon features."""
    out = []
    for f in _features(report, canvass, include_content):
        props = {"layer": f["layer"], "name": f["name"], **f["props"]}
        out.append({"type": "Feature", "properties": props,
                    "geometry": {"type": "Point", "coordinates": [f["lon"], f["lat"]]}})
        if "radius_m" in f:
            out.append({"type": "Feature", "properties": {"layer": f["layer"], "name": f"{f['name']} (area)"},
                        "geometry": {"type": "Polygon", "coordinates": [circle(f["lat"], f["lon"], f["radius_m"])]}})
    case = report["case"]
    return json.dumps({"type": "FeatureCollection",
                       "properties": {"case": case["identifier"], "title": case["title"],
                                      "generated": report.get("generated", {}).get("at", "")},
                       "features": out}, indent=2)


def _sub(parent: ET.Element, tag: str, text: Optional[str] = None, **attrs: str) -> ET.Element:
    element = ET.SubElement(parent, tag, attrs)
    if text is not None:
        element.text = text
    return element


def render_kml(report: dict[str, Any], canvass: Sequence[dict[str, Any]] = (), include_content: bool = True) -> str:
    """A KML document with one folder per layer; properties go in ExtendedData (My Maps shows them as columns)."""
    root = ET.Element("kml", xmlns=KML_NS)
    doc = _sub(root, "Document")
    case = report["case"]
    _sub(doc, "name", f"{case['title']} · ArkAngel {case['identifier']}")
    _sub(doc, "description", "Leads point at where a human should look next. They are not findings.")
    for style_id, (color, icon, scale) in _STYLES.items():
        style = _sub(doc, "Style", id=style_id)
        icon_style = _sub(style, "IconStyle")
        _sub(icon_style, "color", color)
        _sub(icon_style, "scale", f"{scale:g}")
        _sub(_sub(icon_style, "Icon"), "href", _ICONS + icon)
        line = _sub(style, "LineStyle")
        _sub(line, "color", color)
        _sub(line, "width", "2")
        _sub(_sub(style, "PolyStyle"), "color", "33" + color[2:])

    folders: dict[str, ET.Element] = {}
    for f in _features(report, canvass, include_content):
        if f["layer"] not in folders:
            folders[f["layer"]] = _sub(doc, "Folder")
            _sub(folders[f["layer"]], "name", f["layer"])
        folder = folders[f["layer"]]
        mark = _sub(folder, "Placemark")
        _sub(mark, "name", f["name"])
        _sub(mark, "styleUrl", f"#{f['style']}")
        data = _sub(mark, "ExtendedData")
        for key, value in f["props"].items():
            _sub(_sub(data, "Data", name=key), "value", str(value))
        _sub(_sub(mark, "Point"), "coordinates", f"{f['lon']},{f['lat']}")
        if "radius_m" in f:
            area = _sub(folder, "Placemark")
            _sub(area, "name", f"{f['name']} (area)")
            _sub(area, "styleUrl", f"#{f['style']}")
            ring = _sub(_sub(_sub(area, "Polygon"), "outerBoundaryIs"), "LinearRing")
            _sub(ring, "coordinates", " ".join(f"{x},{y}" for x, y in circle(f["lat"], f["lon"], f["radius_m"])))
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def render_canvass_csv(canvass: Sequence[dict[str, Any]]) -> str:
    """One row per camera per spot, with blank columns for the canvass team to fill in."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["spot", "lead_rank", "latest_evidence_here", "camera", "operator", "zone", "distance_m",
                     "facing_spot", "latitude", "longitude", "osm", "street_view", "contacted", "footage_requested",
                     "notes"])
    for target in canvass:
        for cam in target["cameras"]:
            writer.writerow([target["label"], target["lead_rank"] or "", target["latest"], cam["kind"],
                             cam["operator"], cam["zone"], cam["distance_m"],
                             _FACING_WORDS[cam["facing"]], cam["latitude"],
                             cam["longitude"], cam["osm_url"], street_view_url(cam["latitude"], cam["longitude"]),
                             "", "", ""])
    return buffer.getvalue()
