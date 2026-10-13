from ark_angel.analysis.correlate import analyze_case, cluster_points, GeoPoint, haversine_km
from ark_angel.models import Evidence


def _text(identifier: str, content: str, source: str) -> Evidence:
    return Evidence(identifier=identifier, type="text", content=content, metadata={"source": source})


def test_same_phone_across_sources_becomes_top_lead() -> None:
    evidence = [
        _text("tips:1", "call 303-555-0147", "tips"),
        _text("notes:1", "texts from (303) 555-0147", "notes"),
        _text("notes:2", "nothing useful here", "notes"),
    ]
    analysis = analyze_case(evidence)

    top = analysis.leads[0]
    assert top.identifier == "corr-phone-3035550147"
    assert top.evidence_ids == ["tips:1", "notes:1"]
    assert top.source == "analysis"
    assert any("written 2 different ways" in reason for reason in top.reasons)


def test_single_mentions_do_not_become_correlated_leads() -> None:
    analysis = analyze_case([_text("a:1", "jane@example.com", "a"), _text("b:1", "bob@example.com", "b")])
    assert not [lead for lead in analysis.leads if lead.identifier.startswith("corr-")]


def test_more_sources_scores_higher_than_same_source() -> None:
    spread = analyze_case([_text("a:1", "@owl_1", "a"), _text("b:1", "@owl_1", "b")]).leads[0]
    same = analyze_case([_text("a:1", "@owl_1", "a"), _text("a:2", "@owl_1", "a")]).leads[0]
    assert spread.score > same.score


def test_username_hit_metadata_counts_as_handle_mention() -> None:
    evidence = [
        _text("tips:1", "met someone called @rt_owl", "tips"),
        Evidence(identifier="username:github:rt_owl", type="username-hit", content="profile exists",
                 metadata={"source": "username-lookup", "handle": "rt_owl"}),
    ]
    lead = analyze_case(evidence).leads[0]
    assert lead.identifier == "corr-handle-rt_owl"
    assert "username:github:rt_owl" in lead.evidence_ids


def test_nearby_points_cluster_and_far_points_do_not() -> None:
    evidence = [
        _text("tips:1", "pin 39.7489, -104.9985", "tips"),
        Evidence(identifier="image:a.jpg", type="image", content="GPS 39.752400, -104.999600",
                 metadata={"source": "photos", "latitude": 39.7524, "longitude": -104.9996}),
        Evidence(identifier="image:b.jpg", type="image", content="far away",
                 metadata={"source": "photos", "latitude": 40.015, "longitude": -105.2705}),
    ]
    analysis = analyze_case(evidence)

    assert len(analysis.points) == 3  # image text coords are not double counted
    geo = [lead for lead in analysis.leads if lead.identifier.startswith("geo-")]
    assert len(geo) == 1
    assert sorted(geo[0].evidence_ids) == ["image:a.jpg", "tips:1"]


def test_cluster_points_radius() -> None:
    a = GeoPoint("a", "s", 0.0, 0.0, "")
    b = GeoPoint("b", "s", 0.0, 0.005, "")  # ~0.56 km east
    assert len(cluster_points([a, b], radius_km=1.0)) == 1
    assert len(cluster_points([a, b], radius_km=0.1)) == 2


def test_haversine_known_distance() -> None:
    assert abs(haversine_km(0, 0, 0, 1) - 111.19) < 0.1


def test_timeline_is_sorted_and_latest_lead_points_at_newest() -> None:
    analysis = analyze_case([
        _text("t:2", "2026-10-02 10:00 second", "t"),
        _text("t:1", "2026-10-01 09:00 first", "t"),
    ])
    assert [event.evidence_id for event in analysis.timeline] == ["t:1", "t:2"]
    latest = next(lead for lead in analysis.leads if lead.identifier == "timeline-latest")
    assert latest.evidence_ids == ["t:2"]
