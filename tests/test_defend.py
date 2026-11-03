"""Principal defender mode: exposures, pattern of life, threats, and keeping the principal off public pages."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from ark_angel import pipeline
from ark_angel.analysis.defend import analyze_principal, risk_level, threat_hits
from ark_angel.cases.store import FileCaseStore
from ark_angel.cli import cli
from ark_angel.models import Evidence, Place, Principal

HOME = Place("Home", 39.70600, -104.95300, ["1187 Demo Lane"], 150.0, source="sensitive")


def _principal(**kwargs) -> Principal:
    base = dict(name="Dana Whitlock", authorized_by="the principal", phones=["3035550182"],
                emails=["dana.w.demo@example.com"], handles=["dana_runs_demo"], plates=["DMO4821"], places=[HOME])
    base.update(kwargs)
    return Principal(**base)


def _text(identifier: str, content: str, source: str = "listings", **meta) -> Evidence:
    return Evidence(identifier=identifier, type="text", content=content, metadata={"source": source, **meta})


def _leads(evidence, principal=None):
    return {lead.identifier: lead for lead in analyze_principal(principal or _principal(), evidence).leads}


# Threat language


@pytest.mark.parametrize("text, category", [
    ("I know where you live", "location knowledge"),
    ("i'm coming for you", "approach"),
    ("you can't hide forever", "approach"),
    ("I will kill you", "violence"),
    ("you'll regret this", "fixation"),
    ("I saw you at the gym", "location knowledge"),
])
def test_threat_phrases_are_caught(text: str, category: str) -> None:
    assert category in {hit.category for hit in threat_hits(text)}


@pytest.mark.parametrize("text", [
    "you killed it at the presentation today!",
    "that workout is going to kill me",
    "see you at the meeting",
    "I know where you got that jacket, Target",
])
def test_ordinary_slang_is_not_a_threat(text: str) -> None:
    assert threat_hits(text) == []


def test_threats_group_by_sender_and_escalate() -> None:
    evidence = [
        _text("m:1", "you ruined my life", "dms", sender="Rook", timestamp="2026-09-01 22:00:00"),
        _text("m:2", "you'll regret this", "dms", sender="Rook", timestamp="2026-09-10 22:00:00"),
        _text("m:3", "I know where you live", "dms", sender="Rook", timestamp="2026-09-15 01:00:00"),
        _text("m:4", "i'm coming for you", "dms", sender="Rook", timestamp="2026-09-15 01:30:00"),
        _text("m:5", "drinks friday?", "dms", sender="Pat", timestamp="2026-09-15 09:00:00"),
    ]
    leads = _leads(evidence)
    rook = leads["threat-rook"]
    assert rook.evidence_ids == ["m:1", "m:2", "m:3", "m:4"]
    assert "escalating" in rook.summary
    assert any(r.startswith("ESCALATING: severity rose from fixation to location knowledge") for r in rook.reasons)
    assert any("shrank from 9 days to 30 min" in r for r in rook.reasons)
    assert not any(lid.startswith("threat-pat") for lid in leads)


def test_a_threat_naming_a_real_sensitive_place_is_critical() -> None:
    evidence = [_text("m:1", "I know where you live. 1187 Demo Lane.", "dms", sender="Rook")]
    leads = _leads(evidence)
    assert any(r.startswith("KNOWS A SENSITIVE LOCATION: Home") for r in leads["threat-rook"].reasons)
    level, why = risk_level(list(leads.values()))
    assert level == "CRITICAL" and "Rook" in why
    # The same threat dismissed as handled no longer drives the risk level.
    assert risk_level(list(leads.values()), {"threat-rook": "dismissed"})[0] != "CRITICAL"


def test_unattributed_threats_fall_back_to_a_contact_in_the_text() -> None:
    leads = _leads([_text("t:1", "From 720-555-0199: I know where you live", "tips")])
    assert "threat-phone:7205550199" in leads
    assert leads["threat-phone:7205550199"].summary.startswith("Threats from 7205550199")


# Exposures


def test_identifiers_are_exposed_however_they_are_written() -> None:
    evidence = [
        _text("l:1", "Dana Whitlock, phone (303) 555-0182"),
        _text("l:2", "303.555.0182 | dana.w.demo@example.com", "broker2"),
        _text("l:3", "Someone else entirely, 720-555-0100"),
    ]
    leads = _leads(evidence)
    phone = leads["exp-phone-3035550182"]
    assert phone.evidence_ids == ["l:1", "l:2"]
    # 4 × 2 items + 2 × (2 sources − 1) + 1 × 2 linked (l:1 names her; l:2 also has her email)
    assert phone.score == 12.0
    assert any(r.startswith("Fix: ") for r in phone.reasons)
    assert "exp-email-dana.w.demo@example.com" in leads
    assert not any("7205550100" in lid for lid in leads)


def test_a_single_listing_is_already_an_exposure() -> None:
    leads = _leads([_text("l:1", "D. Whitlock, plate DMO-4821")])
    assert leads["exp-plate-DMO4821"].evidence_ids == ["l:1"]


def test_sensitive_places_match_by_alias_and_geotag_not_by_label() -> None:
    evidence = [
        _text("l:1", "Current address 1187 Demo Lane"),
        Evidence("photo.jpg", "image", "EXIF", {"source": "photos", "latitude": 39.70612, "longitude": -104.95288}),
        _text("c:1", "heading home from work now", "chat"),  # the label "Home" is not text to look for
        Evidence("far.jpg", "image", "EXIF", {"source": "photos", "latitude": 39.75, "longitude": -104.99}),
    ]
    home = _leads(evidence)["exp-place-Home"]
    assert home.evidence_ids == ["l:1", "photo.jpg"]
    assert any("geotagged 17 m from Home" in r for r in home.reasons)
    fixes = [r for r in home.reasons if r.startswith("Fix: ")]
    assert len(fixes) == 2  # strip the photo's location, and get the address off the listing


def test_routine_at_an_unregistered_place() -> None:
    posts = [
        _text(f"p:{n}", f"{day} 06:{minute} @dana_runs_demo loop done 39.71840, -104.95120", "posts")
        for n, (day, minute) in enumerate((("2026-09-08", "14"), ("2026-09-15", "05"), ("2026-09-22", "20")))
    ]
    pattern = [lead for lead in _leads(posts).values() if lead.identifier.startswith("geo-")]
    assert len(pattern) == 1
    assert pattern[0].summary.startswith("Routine: 3 items place the principal at")
    assert "Tuesdays around 06:14" in pattern[0].summary


def test_clusters_on_a_sensitive_place_are_not_double_counted() -> None:
    photos = [Evidence(f"p{n}.jpg", "image", "EXIF",
                       {"source": "photos", "latitude": 39.7060 + n * 0.0001, "longitude": -104.9530})
              for n in range(3)]
    leads = _leads(photos)
    assert "exp-place-Home" in leads
    assert not any(lid.startswith("geo-") for lid in leads)


# Pipeline, CLI and review


def _store(tmp_path: Path) -> FileCaseStore:
    return FileCaseStore(tmp_path / "cases")


def test_defender_case_round_trips_and_needs_authorization(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="authorized"):
        pipeline.create_defender_case(store, "D1", "Dana", "  ")
    pipeline.create_defender_case(store, "D1", "Dana Whitlock", "the principal",
                                  identifiers=[("phone", "+1 (303) 555-0182"), ("handle", "@Dana_Runs")])
    pipeline.add_sensitive_place(store, "D1", HOME)
    case = store.get_case("D1")
    assert case.principal.phones == ["3035550182"] and case.principal.handles == ["dana_runs"]
    assert case.principal.places[0].aliases == ["1187 Demo Lane"]
    assert case.places == []  # sensitive places stay out of the gazetteer that maps are built from
    with pytest.raises(ValueError, match="doesn't look like a phone"):
        pipeline.add_principal_identifiers(store, "D1", identifiers=[("phone", "12")])


def test_dismissed_exposure_resurfaces_when_relisted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    pipeline.create_defender_case(store, "D1", "Dana Whitlock", "the principal",
                                  identifiers=[("phone", "303-555-0182")])
    store.add_evidence("D1", [_text("l:1", "Dana Whitlock (303) 555-0182")])
    pipeline.enrich(store, "D1")
    pipeline.review_lead(store, "D1", "exp-phone-3035550182", "dismissed", "opt-out filed, listing removed", "me")
    store.add_evidence("D1", [_text("l:2", "relisted: 303 555 0182", "broker2")])
    pipeline.enrich(store, "D1")

    from ark_angel.review import resolve_lead

    item = resolve_lead(store.get_case("D1"), "exp-phone-3035550182")
    assert item.state.resurfaced and item.state.new_evidence_ids == ["l:2"]


def test_missing_person_cases_are_unchanged(tmp_path: Path) -> None:
    store = _store(tmp_path)
    from ark_angel.models import Case

    store.create_case(Case("C1", "Missing"))
    store.add_evidence("C1", [_text("a", "call 303-555-0147"), _text("b", "(303) 555-0147", "notes")])
    leads, _ = pipeline.enrich(store, "C1")
    assert {lead.identifier for lead in leads} >= {"corr-phone-3035550147"}
    assert not any(lead.identifier.startswith(("exp-", "threat-")) for lead in leads)
    with pytest.raises(ValueError, match="isn't a principal-defender case"):
        pipeline.defender_case(store, "C1")


def _run(tmp_path: Path, *args: str):
    return CliRunner().invoke(cli, ["--no-color", "--data-dir", str(tmp_path / "cases"), *args])


def test_cli_workflow(tmp_path: Path) -> None:
    listings = tmp_path / "listings.txt"
    listings.write_text("Dana Whitlock, 1187 Demo Lane, (303) 555-0182\n")
    assert _run(tmp_path, "defend", "init", "D1", "Dana Whitlock").exit_code != 0  # --authorized-by is required
    result = _run(tmp_path, "defend", "init", "D1", "Dana Whitlock", "--authorized-by", "the principal",
                  "--phone", "303-555-0182")
    assert result.exit_code == 0, result.output
    result = _run(tmp_path, "defend", "place", "D1", "Home", "39.706", "-104.953", "--alias", "1187 Demo Lane")
    assert result.exit_code == 0, result.output
    _run(tmp_path, "ingest", "D1", str(listings))
    result = _run(tmp_path, "enrich", "D1")
    assert "defender case" in result.output and "defend brief" in result.output

    brief = _run(tmp_path, "defend", "brief", "D1")
    assert brief.exit_code == 0, brief.output
    assert "RISK  ELEVATED" in brief.output
    assert "Sensitive place Home is exposed in 1 item" in brief.output
    assert "→ request removal of the address" in brief.output

    data = json.loads(_run(tmp_path, "defend", "brief", "D1", "--format", "json").output)
    assert data["risk"]["level"] == "ELEVATED"
    assert {lead["kind"] for lead in data["leads"]} == {"exposure"}
    assert "**Fix:**" in _run(tmp_path, "defend", "brief", "D1", "--format", "md").output
    assert "1187 Demo Lane" in _run(tmp_path, "defend", "show", "D1").output
    assert "Home" not in json.loads(_run(tmp_path, "report", "D1", "--format", "json").output)["places"]


def test_defend_demo(tmp_path: Path) -> None:
    result = _run(tmp_path, "defend", "demo")
    assert result.exit_code == 0, result.output
    out = result.output
    assert "RISK  CRITICAL" in out
    assert "Threats from Rook: 5 messages" in out and "Pat" not in out.split("THREATS")[1].split("EXPOSURES")[0]
    assert "RESURFACED" in out
    assert "Routine: 3 items" in out and "Tuesdays around 06:14" in out


# Website


def test_defender_cases_can_never_get_a_public_profile(tmp_path: Path) -> None:
    pytest.importorskip("flask")
    from web_helpers import Site, post

    site = Site(tmp_path)
    owner = site.member("owner")
    pipeline.create_defender_case(site.store, "D1", "Dana Whitlock", "the principal")
    site.db.register_case("D1", "owner")

    page = owner.get("/app/cases/D1").get_data(as_text=True)
    assert "Public profile" not in page
    assert owner.get("/app/cases/D1/profile").status_code == 403

    # Even a draft slipped in around the form is refused at approval.
    site.db.save_draft("D1", {"name": "Dana Whitlock", "police_report": "x", "attest": "yes"})
    site.db.request_publication("D1")
    admin = site.admin()
    version = site.db.case("D1")["draft_version"]
    resp = post(admin, "/admin/publications/D1", {"decision": "approve", "version": version},
                form_page="/admin/publications")
    assert resp.status_code == 403
    assert site.db.case("D1")["published"] is None


def test_maps_leave_out_locations_next_to_sensitive_places(tmp_path: Path) -> None:
    store = _store(tmp_path)
    pipeline.create_defender_case(store, "D1", "Dana Whitlock", "the principal")
    pipeline.add_sensitive_place(store, "D1", HOME)
    store.add_evidence("D1", [
        Evidence("couch.jpg", "image", "EXIF", {"source": "photos", "latitude": 39.70612, "longitude": -104.95288}),
        Evidence("run.jpg", "image", "EXIF", {"source": "photos", "latitude": 39.7184, "longitude": -104.9512}),
    ])
    pipeline.enrich(store, "D1")
    out = tmp_path / "d.geojson"
    result = _run(tmp_path, "map", "D1", "--out", str(out))
    assert result.exit_code == 0, result.output
    assert "Left out 1 location(s)" in result.output
    text = out.read_text()
    assert "run.jpg" in text and "couch.jpg" not in text and "39.706" not in text
