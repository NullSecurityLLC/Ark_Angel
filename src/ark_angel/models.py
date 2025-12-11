from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, List, Optional


data_t = Any


@dataclass
class Evidence:
    """A piece of evidence collected during an investigation.

    Attributes:
        identifier: Unique ID for the evidence item.
        type: Category of evidence (e.g., "document", "image", "signal").
        content: Raw content or pointer to the evidence data.
        metadata: Optional structured metadata describing the evidence.
    """

    identifier: str
    type: str
    content: data_t
    metadata: Optional[dict[str, Any]] = None


@dataclass
class Lead:
    """A potential lead derived from ingestion or analysis.

    Attributes:
        identifier: Unique ID for the lead.
        summary: Human readable summary of the lead.
        evidence_ids: Identifiers for evidence items that support the lead.
    """

    identifier: str
    summary: str
    evidence_ids: List[str] = field(default_factory=list)


@dataclass
class Location:
    """A resolved geographic location.

    Attributes:
        name: Human-friendly location name.
        latitude: Latitude coordinate.
        longitude: Longitude coordinate.
        country_code: Optional ISO country code.
    """

    name: str
    latitude: float
    longitude: float
    country_code: Optional[str] = None


@dataclass
class Case:
    """A case aggregates leads and evidence for an investigation.

    Attributes:
        identifier: Unique case identifier.
        title: Short title for the case.
        leads: Collected leads associated with the case.
        evidence: Evidence items linked to the case.
        created_at: Timestamp indicating when the case was opened.
    """

    identifier: str
    title: str
    leads: List[Lead] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
