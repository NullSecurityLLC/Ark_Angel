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
