import json
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from ark_angel.cli import cli
from ark_angel.geo.cameras import (Camera, CanvassTarget, bearing_deg, canvass, canvass_dicts, fetch_cameras,
                                   overpass_query, targets_from_report)
from ark_angel.models import Case, Evidence, Place
from ark_angel.pipeline import enrich
from ark_angel.cases.store import FileCaseStore
from ark_angel.report import build_report
from ark_angel.report.gis import circle, render_canvass_csv, render_geojson, render_kml
from ark_angel.report.html import render_html
from ark_angel.report.text import render_markdown

KML = "{http://www.opengis.net/kml/2.2}"
SPOT = (39.7527, -104.9997)

# Overpass-shaped response around SPOT: a camera north of it pointing south (at it), a plate reader
# east of it pointing east (away), a way-mapped shop camera with no direction, one far off, one broken.
OVERPASS = {"elements": [
    {"type": "node", "id": 1, "lat": 39.75324, "lon": -104.9997,
     "tags": {"man_made": "surveillance", "surveillance": "public", "camera:direction": "S"}},
    {"type": "node", "id": 2, "lat": 39.7527, "lon": -104.99795,
     "tags": {"man_made": "surveillance", "surveillance:type": "ALPR", "operator": "Denver Police Department",
              "camera:direction": "90", "surveillance:zone": "traffic"}},
    {"type": "way", "id": 3, "center": {"lat": 39.7518, "lon": -104.9997},
     "tags": {"man_made": "surveillance", "camera:type": "dome", "brand": "Corner Market", "surveillance": "outdoor",
              "surveillance:zone": "shop"}},
    {"type": "node", "id": 4, "lat": 39.80, "lon": -104.9997, "tags": {"man_made": "surveillance"}},
    {"type": "node", "id": 5},
]}


def _case() -> Case:
    return Case(
        identifier="C1",
        title="Case <One> & Co",
        places=[Place("Union Station", *SPOT, aliases=["the station"])],
        evidence=[
            Evidence("photos:1", "image", "photo", {"source": "photos", "latitude": 39.7524, "longitude": -104.9996,
                                                    "timestamp": "2026:10:01 18:00:00"}),
            Evidence("tips:1", "text", "2026-10-02 saw her at the station <b>", {"source": "tips"}),
            Evidence("notes:1", "text", "Union Station again", {"source": "notes"}),
            Evidence("tips:2", "text", "dropped pin 39.6000,-105.1000 on 2026-10-05", {"source": "tips"}),
        ],
    )


def _stored(tmp_path: Path) -> FileCaseStore:
    store = FileCaseStore(tmp_path / "cases")
    store.create_case(_case())
    enrich(store, "C1")
    return store


def _report(tmp_path: Path) -> dict:
    return build_report(_stored(tmp_path).get_case("C1"))


def test_clusters_carry_their_lead_id(tmp_path: Path) -> None:
    report = _report(tmp_path)
    lead_ids = {lead["identifier"] for lead in report["leads"]}
    big = max(report["clusters"], key=lambda c: len(c["points"]))
    assert big["lead_id"] in lead_ids and big["lead_id"].startswith("geo-")


def test_geojson_layers_order_and_redaction(tmp_path: Path) -> None:
    report = _report(tmp_path)
    data = json.loads(render_geojson(report))
    assert data["type"] == "FeatureCollection" and data["properties"]["case"] == "C1"
    layers = [f["properties"]["layer"] for f in data["features"]]
    assert layers[0] == "Location leads" and "Evidence" in layers and "Places" in layers
    lead = data["features"][0]
    lon, lat = lead["geometry"]["coordinates"]
    assert 39 < lat < 40 and -106 < lon < -104  # GeoJSON is [lon, lat]
    assert lead["properties"]["google_maps"].startswith("https://www.google.com/maps/search/?api=1&query=39.75")
    area = data["features"][1]
    assert area["geometry"]["type"] == "Polygon"
    ring = area["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1]
    assert any("content" in f["properties"] for f in data["features"])

    redacted = json.loads(render_geojson(report, include_content=False))
    assert not any("content" in f["properties"] for f in redacted["features"])
    assert "saw her" not in json.dumps(redacted)


def test_kml_parses_escapes_and_has_folders(tmp_path: Path) -> None:
    text = render_kml(_report(tmp_path))
    root = ET.fromstring(text.encode())
    doc = root.find(f"{KML}Document")
    assert doc.find(f"{KML}name").text.startswith("Case <One> & Co")
    folders = [f.find(f"{KML}name").text for f in doc.iter(f"{KML}Folder")]
    assert folders == ["Location leads", "Evidence", "Places"]
    lead = doc.find(f"{KML}Folder/{KML}Placemark")
    assert lead.find(f"{KML}styleUrl").text == "#lead"
    data = {d.get("name"): d.find(f"{KML}value").text for d in lead.iter(f"{KML}Data")}
    assert data["rank"] == "1" and data["status"] == "open"
    lon, lat = (float(v) for v in lead.find(f"{KML}Point/{KML}coordinates").text.split(","))
    assert 39 < lat < 40 and -106 < lon < -104


def test_circle_radius_is_right() -> None:
    from ark_angel.analysis.correlate import haversine_km

    ring = circle(*SPOT, 500)
    assert all(abs(haversine_km(SPOT[0], SPOT[1], lat, lon) * 1000 - 500) < 2 for lon, lat in ring)


def test_reports_link_google_maps_and_street_view(tmp_path: Path) -> None:
    report = _report(tmp_path)
    page = render_html(report)
    assert "https://www.google.com/maps/search/?api=1&amp;query=39.75" in page
    assert "map_action=pano" in page
    assert "<script src" not in page  # links only; the report still opens offline
    assert "[Street View](https://www.google.com/maps/@?api=1&map_action=pano" in render_markdown(report)


def test_camera_tags() -> None:
    alpr = Camera("node", 2, 0, 0, {"surveillance:type": "ALPR", "operator": "PD", "camera:direction": "45;90"})
    assert (alpr.kind, alpr.operator, alpr.direction) == ("ALPR", "PD", 45.0)
    dome = Camera("way", 3, 0, 0, {"camera:type": "dome", "direction": "NW", "surveillance": "public",
                                   "surveillance:zone": "town"})
    assert (dome.kind, dome.direction, dome.zone) == ("dome camera", 315.0, "town, public")
    assert dome.osm_url == "https://www.openstreetmap.org/way/3"
    assert Camera("node", 4, 0, 0, {"camera:direction": "up"}).direction is None
    assert round(bearing_deg(0, 0, 1, 0)) == 0 and round(bearing_deg(0, 0, 0, 1)) == 90


def test_targets_skip_dismissed_and_add_latest_geotag(tmp_path: Path) -> None:
    report = _report(tmp_path)
    targets = targets_from_report(report, radius_m=200)
    assert targets[0].label == "#1 Union Station" and targets[0].lead_rank == 1
    assert targets[0].radius_m > 200  # search radius grows by the cluster's spread
    assert targets[0].latest.startswith("2026-10-02")
    assert targets[-1].label.startswith("latest geotagged activity (tips:2)")

    lead_id = targets[0].lead_id
    for lead in report["leads"]:
        if lead["identifier"] == lead_id:
            lead["status"] = "dismissed"
    assert [t.lead_id for t in targets_from_report(report)] == [""]
    assert [t.lead_id for t in targets_from_report(report, lead_ids=[lead_id])] == [lead_id]


def test_fetch_cameras_parses_overpass_and_canvass_ranks() -> None:
    resp = MagicMock()
    resp.json.return_value = OVERPASS
    target = CanvassTarget("spot", *SPOT, radius_m=300)
    with patch("ark_angel.geo.cameras.requests.post", return_value=resp) as post:
        found = fetch_cameras([target], endpoint="https://overpass.example/api")
    assert post.call_args.args[0] == "https://overpass.example/api"
    query = post.call_args.kwargs["data"]["data"]
    assert query == overpass_query([target])
    assert '"man_made"="surveillance"' in query and "around:300,39.752700,-104.999700" in query
    assert [c.osm_id for c in found] == [1, 2, 3, 4]

    (result,) = canvass([target], found)
    # Nearest first, cameras pointing away last; the far camera is out of range.
    assert [(h.camera.osm_id, h.facing) for h in result.hits] == [(1, True), (3, None), (2, False)]
    assert result.hits[0].distance_m == 60

    rows = render_canvass_csv(canvass_dicts([result])).splitlines()
    assert rows[0].startswith("spot,lead_rank,latest_evidence_here,camera,operator")
    assert len(rows) == 4 and "Denver Police Department" in rows[3] and rows[3].endswith(",,,")


def test_fetch_cameras_errors_become_value_errors() -> None:
    import requests

    with patch("ark_angel.geo.cameras.requests.post", side_effect=requests.ConnectionError("blocked")):
        with pytest.raises(ValueError, match="camera lookup failed"):
            fetch_cameras([CanvassTarget("spot", *SPOT, 300)])
    assert fetch_cameras([]) == []


def _cli(tmp_path: Path, *args: str):
    return CliRunner().invoke(cli, ["--data-dir", str(tmp_path / "cases"), "--no-color", *args])


def test_cli_map_and_cameras(tmp_path: Path) -> None:
    _stored(tmp_path)
    out = tmp_path / "c1.kml"
    result = _cli(tmp_path, "map", "C1", "--out", str(out))
    assert result.exit_code == 0, result.output
    assert "Google My Maps" in result.output and ET.fromstring(out.read_bytes()) is not None
    assert _cli(tmp_path, "map", "C1", "--out", str(tmp_path / "c1.txt")).exit_code != 0

    result = _cli(tmp_path, "cameras", "C1", "--at", "39.7,-105.0")
    assert result.exit_code == 0, result.output
    assert "#1 Union Station" in result.output and "spot 1: 39.70000, -105.00000" in result.output
    assert "--online" in result.output
    assert "must name location-cluster" in _cli(tmp_path, "cameras", "C1", "--lead", "timeline-latest").output
    assert "LAT,LON" in _cli(tmp_path, "cameras", "C1", "--at", "nowhere").output

    resp = MagicMock()
    resp.json.return_value = OVERPASS
    csv_out, map_out = tmp_path / "canvass.csv", tmp_path / "canvass.geojson"
    with patch("ark_angel.geo.cameras.requests.post", return_value=resp):
        result = _cli(tmp_path, "cameras", "C1", "--online", "--lead", "1", "--out", str(csv_out))
        assert result.exit_code == 0, result.output
        assert "points at the spot" in result.output and "Plate readers nearby" in result.output
        assert "3 mapped camera(s)" in result.output and "Wrote canvass checklist" in result.output
        assert len(csv_out.read_text().splitlines()) == 4

        result = _cli(tmp_path, "cameras", "C1", "--online", "--out", str(map_out), "--no-content")
        assert result.exit_code == 0, result.output
    layers = {f["properties"]["layer"] for f in json.loads(map_out.read_text())["features"]}
    assert "Video canvass" in layers
