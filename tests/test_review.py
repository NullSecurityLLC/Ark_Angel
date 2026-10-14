from pathlib import Path

import pytest

from ark_angel.cases.store import FileCaseStore
from ark_angel.models import Case, Lead, Review
from ark_angel.report import build_report
from ark_angel.report.html import render_html
from ark_angel.report.text import render_markdown, render_text
from ark_angel.review import make_review, ranked_leads, resolve_lead, review_states


def _lead(identifier: str, score: float, evidence: list[str]) -> Lead:
    return Lead(identifier=identifier, summary=f"lead {identifier}", score=score, evidence_ids=evidence,
                source="analysis")


def _review(lead_id: str, status: str, evidence: list[str], note: str = "why") -> Review:
    return Review(lead_id=lead_id, status=status, note=note, reviewer="tester",
                  reviewed_at="2026-10-14T00:00:00+00:00", evidence_ids=evidence)


def _case(leads: list[Lead], reviews: list[Review] = ()) -> Case:
    return Case(identifier="C1", title="t", leads=leads, reviews=list(reviews))


def test_latest_review_wins() -> None:
    lead = _lead("corr-phone-1", 9, ["a", "b"])
    states = review_states([lead], [_review("corr-phone-1", "verified", ["a", "b"]),
                                    _review("corr-phone-1", "open", ["a", "b"])])
    assert states["corr-phone-1"].status == "open"


def test_dismissed_lead_resurfaces_when_new_evidence_joins() -> None:
    lead = _lead("corr-place-X", 6, ["a", "b", "c"])
    state = review_states([lead], [_review("corr-place-X", "dismissed", ["a", "b"])])["corr-place-X"]
    assert state.status == "open"
    assert state.resurfaced
    assert state.new_evidence_ids == ["c"]


def test_dismissed_lead_stays_dismissed_without_new_evidence() -> None:
    lead = _lead("corr-place-X", 6, ["a", "b"])
    state = review_states([lead], [_review("corr-place-X", "dismissed", ["a", "b"])])["corr-place-X"]
    assert state.status == "dismissed" and not state.resurfaced


def test_verified_lead_stays_verified_but_flags_new_evidence() -> None:
    lead = _lead("corr-phone-1", 9, ["a", "b", "c"])
    state = review_states([lead], [_review("corr-phone-1", "verified", ["a", "b"])])["corr-phone-1"]
    assert state.status == "verified"
    assert state.new_evidence_ids == ["c"]


def test_drifted_geo_cluster_inherits_review_by_evidence_overlap() -> None:
    moved = _lead("geo-39.75100,-104.99900", 20, ["a", "b", "c"])
    other = _lead("geo-40.00000,-105.00000", 5, ["x", "y"])
    states = review_states([moved, other], [_review("geo-39.75000,-104.99800", "verified", ["a", "b"])])
    assert states[moved.identifier].status == "verified"
    assert states[moved.identifier].new_evidence_ids == ["c"]
    assert states[other.identifier].review is None


def test_existing_geo_cluster_does_not_lose_its_review_to_a_neighbour() -> None:
    kept = _lead("geo-1", 20, ["a", "b"])
    neighbour = _lead("geo-2", 10, ["b", "c"])
    states = review_states([kept, neighbour], [_review("geo-1", "dismissed", ["a", "b"])])
    assert states["geo-1"].status == "dismissed"
    assert states["geo-2"].review is None


def test_numbers_follow_score_and_survive_status_changes() -> None:
    leads = [_lead("a", 10, ["1"]), _lead("b", 8, ["2"]), _lead("c", 5, ["3"])]
    before = {item.lead.identifier: item.rank for item in ranked_leads(_case(leads))}
    after_items = ranked_leads(_case(leads, [_review("c", "verified", ["3"]), _review("a", "dismissed", ["1"])]))
    assert {item.lead.identifier: item.rank for item in after_items} == before
    assert [item.lead.identifier for item in after_items] == ["c", "b", "a"]  # verified, open, dismissed


def test_resolve_lead_by_number_id_and_prefix() -> None:
    case = _case([_lead("corr-phone-1", 10, ["1"]), _lead("corr-plate-X", 8, ["2"])])
    assert resolve_lead(case, "2").lead.identifier == "corr-plate-X"
    assert resolve_lead(case, "#1").lead.identifier == "corr-phone-1"
    assert resolve_lead(case, "corr-plate-X").lead.identifier == "corr-plate-X"
    assert resolve_lead(case, "corr-ph").lead.identifier == "corr-phone-1"
    with pytest.raises(ValueError, match="matches 2 leads"):
        resolve_lead(case, "corr-")
    with pytest.raises(ValueError, match="no lead #9"):
        resolve_lead(case, "9")
    with pytest.raises(ValueError, match="no lead matches"):
        resolve_lead(case, "nope")


def test_dismiss_requires_note_and_review_snapshots_evidence() -> None:
    lead = _lead("x", 1, ["a", "b"])
    with pytest.raises(ValueError, match="needs a --note"):
        make_review(lead, "dismissed", "  ")
    review = make_review(lead, "verified", reviewer="me")
    assert (review.status, review.reviewer, review.evidence_ids) == ("verified", "me", ["a", "b"])
    assert review.reviewed_at.endswith("+00:00")


def test_reviews_round_trip_through_store(tmp_path: Path) -> None:
    store = FileCaseStore(tmp_path)
    review = _review("x", "verified", ["a"])
    store.create_case(Case(identifier="C1", title="t", reviews=[review]))
    assert FileCaseStore(tmp_path).get_case("C1").reviews == [review]


def test_reports_group_by_status() -> None:
    case = _case(
        [_lead("corr-a", 10, ["1"]), _lead("corr-b", 8, ["2"]), _lead("corr-c", 5, ["3", "4"])],
        [_review("corr-b", "verified", ["2"], note="checked"), _review("corr-a", "dismissed", ["1"], note="noise"),
         _review("corr-c", "dismissed", ["3"], note="old")],
    )
    report = build_report(case)
    assert [lead["status"] for lead in report["leads"]] == ["verified", "open", "dismissed"]

    text = render_text(report)
    assert text.index("VERIFIED") < text.index("lead corr-b") < text.index("OPEN LEADS")
    assert "RESURFACED" in text and "1 new item(s) since: 4" in text
    assert "DISMISSED  1 hidden" in text and "lead corr-a" not in text
    assert "lead corr-a" in render_text(report, show_dismissed=True)

    assert "## Verified" in render_markdown(report) and "## Dismissed" in render_markdown(report)
    page = render_html(report)
    assert "<h3>Verified</h3>" in page and "Dismissed (1)" in page and 'class="review resurfaced"' in page


def test_superseded_geo_review_does_not_leak_to_neighbouring_cluster() -> None:
    # geo-old was reviewed, drifted to geo-new, and geo-new was then reviewed directly.
    current = _lead("geo-new", 20, ["a", "b", "c"])
    neighbour = _lead("geo-near", 10, ["b", "z"])
    reviews = [
        Review("geo-old", "dismissed", "noise", "t", "2026-10-01T00:00:00+00:00", ["a", "b"]),
        Review("geo-new", "verified", "confirmed", "t", "2026-10-02T00:00:00+00:00", ["a", "b", "c"]),
    ]
    states = review_states([current, neighbour], reviews)
    assert states["geo-new"].status == "verified"
    assert states["geo-near"].review is None
