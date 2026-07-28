"""File-backed :class:`CaseStore` implementation for local, persistent storage."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

from ark_angel.cases.base import CaseStore
from ark_angel.models import Case, Evidence, Lead


class FileCaseStore(CaseStore):
    """Persists each case as a JSON file in a local directory."""

    def __init__(self, data_dir: str | Path = ".ark_angel/cases") -> None:
        self.data_dir = Path(data_dir)

    def create_case(self, case: Case) -> Case:
        path = self._path(case.identifier)
        if path.exists():
            raise FileExistsError(f"case already exists: {case.identifier}")
        self._write(case)
        return case

    def add_leads(self, case_id: str, leads: Iterable[Lead]) -> None:
        case = self.get_case(case_id)
        case.leads.extend(leads)
        self._write(case)

    def add_evidence(self, case_id: str, evidence: Iterable[Evidence]) -> None:
        case = self.get_case(case_id)
        case.evidence.extend(evidence)
        self._write(case)

    def get_case(self, case_id: str) -> Case:
        path = self._path(case_id)
        if not path.exists():
            raise KeyError(case_id)
        return _case_from_dict(json.loads(path.read_text()))

    def list_cases(self) -> list[str]:
        if not self.data_dir.exists():
            return []
        return sorted(path.stem for path in self.data_dir.glob("*.json"))

    def _path(self, case_id: str) -> Path:
        return self.data_dir / f"{case_id}.json"

    def _write(self, case: Case) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._path(case.identifier).write_text(json.dumps(_case_to_dict(case), indent=2))


def _case_to_dict(case: Case) -> dict:
    data = asdict(case)
    data["created_at"] = case.created_at.isoformat()
    return data


def _case_from_dict(data: dict) -> Case:
    return Case(
        identifier=data["identifier"],
        title=data["title"],
        leads=[Lead(**lead) for lead in data.get("leads", [])],
        evidence=[Evidence(**item) for item in data.get("evidence", [])],
        created_at=datetime.fromisoformat(data["created_at"]),
    )
