"""File-backed :class:`CaseStore` implementation for local, persistent storage."""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, TypeVar

from ark_angel.cases.base import CaseStore
from ark_angel.models import Case, Entity, Evidence, Lead, Place, Review

CASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

_T = TypeVar("_T", Lead, Evidence)


class CaseNotFoundError(KeyError):
    """Raised when a case identifier has no stored case."""


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
        case.leads = _upsert(case.leads, leads)
        self._write(case)

    def add_evidence(self, case_id: str, evidence: Iterable[Evidence]) -> None:
        case = self.get_case(case_id)
        case.evidence = _upsert(case.evidence, evidence)
        self._write(case)

    def save_case(self, case: Case) -> None:
        """Overwrite a stored case with ``case``."""
        if not self._path(case.identifier).exists():
            raise CaseNotFoundError(case.identifier)
        self._write(case)

    def delete_case(self, case_id: str) -> None:
        self._path(case_id).unlink(missing_ok=True)

    def get_case(self, case_id: str) -> Case:
        path = self._path(case_id)
        if not path.exists():
            raise CaseNotFoundError(case_id)
        return _case_from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_cases(self) -> list[str]:
        if not self.data_dir.exists():
            return []
        return sorted(path.stem for path in self.data_dir.glob("*.json"))

    def _path(self, case_id: str) -> Path:
        if not CASE_ID_PATTERN.match(case_id):
            raise ValueError(
                f"invalid case id {case_id!r}: use letters, digits, '.', '_' or '-' (max 64 chars)"
            )
        return self.data_dir / f"{case_id}.json"

    def _write(self, case: Case) -> None:
        """Write via a temporary file and a rename, so a crash mid-write never leaves half a case."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self._path(case.identifier)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            tmp.write_text(json.dumps(_case_to_dict(case), indent=2), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)


def _upsert(existing: list[_T], incoming: Iterable[_T]) -> list[_T]:
    """Merge items by identifier; incoming items replace existing ones in place."""
    merged = {item.identifier: item for item in existing}
    for item in incoming:
        merged[item.identifier] = item
    return list(merged.values())


def _case_to_dict(case: Case) -> dict:
    data = asdict(case)
    data["created_at"] = case.created_at.isoformat()
    return data


def _lead_from_dict(data: dict) -> Lead:
    entities = [Entity(**entity) for entity in data.pop("entities", [])]
    return Lead(**data, entities=entities)


def _case_from_dict(data: dict) -> Case:
    return Case(
        identifier=data["identifier"],
        title=data["title"],
        leads=[_lead_from_dict(lead) for lead in data.get("leads", [])],
        evidence=[Evidence(**item) for item in data.get("evidence", [])],
        created_at=datetime.fromisoformat(data["created_at"]),
        places=[Place(**place) for place in data.get("places", [])],
        reviews=[Review(**review) for review in data.get("reviews", [])],
    )
