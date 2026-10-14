from pathlib import Path

import pytest

from ark_angel.cases.store import FileCaseStore
from ark_angel.models import Case, Evidence, Lead


def test_create_and_get_case(tmp_path: Path) -> None:
    store = FileCaseStore(tmp_path)
    store.create_case(Case(identifier="C1", title="Case One"))

    case = store.get_case("C1")
    assert case.title == "Case One"
    assert case.leads == []
    assert case.evidence == []


def test_create_case_rejects_duplicate(tmp_path: Path) -> None:
    store = FileCaseStore(tmp_path)
    store.create_case(Case(identifier="C1", title="Case One"))

    with pytest.raises(FileExistsError):
        store.create_case(Case(identifier="C1", title="Duplicate"))


def test_get_case_missing_raises_key_error(tmp_path: Path) -> None:
    store = FileCaseStore(tmp_path)

    with pytest.raises(KeyError):
        store.get_case("missing")


def test_add_leads_and_evidence_persist_across_instances(tmp_path: Path) -> None:
    store = FileCaseStore(tmp_path)
    store.create_case(Case(identifier="C1", title="Case One"))

    store.add_leads("C1", [Lead(identifier="L1", summary="lead one")])
    store.add_evidence("C1", [Evidence(identifier="E1", type="text", content="hello")])

    reloaded = FileCaseStore(tmp_path).get_case("C1")
    assert [lead.identifier for lead in reloaded.leads] == ["L1"]
    assert [item.identifier for item in reloaded.evidence] == ["E1"]


def test_list_cases_sorted(tmp_path: Path) -> None:
    store = FileCaseStore(tmp_path)
    store.create_case(Case(identifier="B", title="B"))
    store.create_case(Case(identifier="A", title="A"))

    assert store.list_cases() == ["A", "B"]


def test_list_cases_empty_when_dir_missing(tmp_path: Path) -> None:
    store = FileCaseStore(tmp_path / "does-not-exist")

    assert store.list_cases() == []


@pytest.mark.parametrize("bad_id", ["../escape", "a/b", "", ".hidden", "x" * 65])
def test_invalid_case_ids_are_rejected(tmp_path: Path, bad_id: str) -> None:
    store = FileCaseStore(tmp_path / "cases")

    with pytest.raises(ValueError):
        store.create_case(Case(identifier=bad_id, title="nope"))
    assert not (tmp_path / "escape.json").exists()


def test_adding_same_identifiers_twice_does_not_duplicate(tmp_path: Path) -> None:
    store = FileCaseStore(tmp_path)
    store.create_case(Case(identifier="C1", title="Case One"))

    for _ in range(2):
        store.add_leads("C1", [Lead(identifier="L1", summary="lead one")])
        store.add_evidence("C1", [Evidence(identifier="E1", type="text", content="hello")])

    case = store.get_case("C1")
    assert len(case.leads) == 1
    assert len(case.evidence) == 1


def test_lead_scores_reasons_and_entities_round_trip(tmp_path: Path) -> None:
    from ark_angel.models import Entity

    store = FileCaseStore(tmp_path)
    store.create_case(Case(identifier="C1", title="Case One"))
    lead = Lead(identifier="L1", summary="s", score=4.5, reasons=["why"], source="analysis",
                entities=[Entity(kind="phone", value="3035550147", raw="303-555-0147")])
    store.add_leads("C1", [lead])

    assert FileCaseStore(tmp_path).get_case("C1").leads == [lead]


def test_legacy_case_files_still_load(tmp_path: Path) -> None:
    (tmp_path / "OLD.json").write_text(
        '{"identifier": "OLD", "title": "t", "created_at": "2025-01-01T00:00:00",'
        ' "leads": [{"identifier": "L", "summary": "email present", "evidence_ids": []}], "evidence": []}'
    )
    case = FileCaseStore(tmp_path).get_case("OLD")
    assert case.leads[0].score == 0.0
    assert case.leads[0].source == "ingest"


def test_delete_case(tmp_path: Path) -> None:
    store = FileCaseStore(tmp_path)
    store.create_case(Case(identifier="C1", title="Case One"))
    store.delete_case("C1")
    assert store.list_cases() == []
