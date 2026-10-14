from unittest.mock import MagicMock, patch

import pytest

from ark_angel.analysis.correlate import analyze_case
from ark_angel.analysis.places import PlaceMatcher, suggest_places
from ark_angel.models import Evidence, Place

STATION = Place("Union Station", 39.7527, -104.9997, aliases=["the station"], radius_m=200)
BOULDER = Place("Boulder", 40.0150, -105.2705, radius_m=6000)


def _text(identifier: str, content: str, source: str) -> Evidence:
    return Evidence(identifier=identifier, type="text", content=content, metadata={"source": source})


def _photo(identifier: str, lat: float, lon: float) -> Evidence:
    return Evidence(identifier=identifier, type="image", content=f"GPS {lat}, {lon}",
                    metadata={"source": "photos", "latitude": lat, "longitude": lon})


def test_matcher_is_case_and_whitespace_insensitive_and_whole_word() -> None:
    matcher = PlaceMatcher([STATION])
    assert [p.name for p, _ in matcher.find("near union  STATION today")] == ["Union Station"]
    assert matcher.find("the stationary bike") == []


def test_matcher_prefers_longest_name() -> None:
    matcher = PlaceMatcher([Place("Station", 0, 0), STATION])
    assert [p.name for p, _ in matcher.find("at Union Station")] == ["Union Station"]


def test_empty_gazetteer_matches_nothing() -> None:
    assert not PlaceMatcher([])
    assert PlaceMatcher([]).find("Union Station") == []


def test_suggest_lists_unresolved_names_most_mentioned_first() -> None:
    evidence = [
        _text("t:1", "seen near Union Station on Monday", "tips"),
        _text("t:2", "she is in Boulder. Seen near the Cherry Creek trail", "tips"),
        _text("t:3", "maybe in Boulder", "tips"),
        _text("t:4", "walked toward Riley's school", "tips"),
    ]
    names = [s.name for s in suggest_places(evidence)]
    assert names == ["Boulder", "Cherry Creek", "Union Station"]

    resolved = [s.name for s in suggest_places(evidence, [STATION, Place("Cherry Creek Trail", 0, 0)])]
    assert resolved == ["Boulder"]


def test_named_place_joins_geo_cluster_with_photo() -> None:
    evidence = [
        _text("tips:1", "Caller saw her near Union Station", "tips"),
        _text("social:1", "meet me at the station tomorrow", "social"),
        _photo("image:a.jpg", 39.7524, -104.9996),
    ]
    analysis = analyze_case(evidence, places=[STATION])

    geo = [lead for lead in analysis.leads if lead.identifier.startswith("geo-")]
    assert len(geo) == 1
    assert geo[0].summary.startswith("Location cluster around Union Station: 3 items")
    assert sorted(geo[0].evidence_ids) == ["image:a.jpg", "social:1", "tips:1"]
    assert 'social:1 names Union Station as "the station"' in geo[0].reasons
    assert "tips:1 names Union Station" in geo[0].reasons
    # Precise places score through the cluster only, not a second "Place" lead.
    assert not [lead for lead in analysis.leads if lead.identifier.startswith("corr-place-")]


def test_coarse_place_collects_geotagged_evidence_inside_it() -> None:
    evidence = [
        _text("tips:5", "Caller is sure she is in Boulder", "tips"),
        _photo("image:old.jpg", 40.0100, -105.2700),
        _photo("image:far.jpg", 39.7524, -104.9996),
    ]
    analysis = analyze_case(evidence, places=[BOULDER])

    lead = next(lead for lead in analysis.leads if lead.identifier == "corr-place-Boulder")
    assert lead.evidence_ids == ["tips:5", "image:old.jpg"]
    assert any("inside its 6 km radius" in reason for reason in lead.reasons)
    # A city mention is too vague to be a geo point.
    assert len(analysis.points) == 2


def test_coarse_place_never_mentioned_creates_no_lead() -> None:
    evidence = [_photo("image:a.jpg", 40.01, -105.27), _photo("image:b.jpg", 40.02, -105.27)]
    analysis = analyze_case(evidence, places=[BOULDER])
    assert not [lead for lead in analysis.leads if lead.identifier.startswith("corr-place-")]


def test_nominatim_results_are_parsed_and_named_after_query() -> None:
    from ark_angel.geo.nominatim import search

    resp = MagicMock()
    resp.json.return_value = [
        {"lat": "39.7527", "lon": "-104.9997", "display_name": "Union Station, Denver",
         "boundingbox": ["39.7520", "39.7534", "-105.0005", "-104.9989"]},
        {"lat": "bad"},
    ]
    with patch("ark_angel.geo.nominatim.requests.get", return_value=resp) as get:
        results = search("Union Station")

    assert get.call_args.kwargs["params"]["q"] == "Union Station"
    assert len(results) == 1
    place, display = results[0]
    assert (place.name, place.source, display) == ("Union Station", "nominatim", "Union Station, Denver")
    assert 100 <= place.radius_m < 200


def test_nominatim_network_error_becomes_value_error() -> None:
    import requests

    from ark_angel.geo.nominatim import search

    with patch("ark_angel.geo.nominatim.requests.get", side_effect=requests.ConnectionError("blocked")):
        with pytest.raises(ValueError, match="place lookup failed"):
            search("Union Station")
