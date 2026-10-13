from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional


data_t = Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Evidence:
    """A piece of evidence collected during an investigation.

    Attributes:
        identifier: Unique ID for the evidence item.
        type: Category of evidence (e.g., "document", "image", "signal").
        content: Raw content or pointer to the evidence data.
        metadata: Optional structured metadata describing the evidence, such as
            ``source``, ``latitude``/``longitude`` or ``timestamp``.
    """

    identifier: str
    type: str
    content: data_t
    metadata: Optional[dict[str, Any]] = None


@dataclass
class Entity:
    """A normalized value pulled out of evidence (a phone, email, handle...).

    Attributes:
        kind: Entity category, e.g. ``"phone"``, ``"email"``, ``"handle"``.
        value: Normalized value used for matching across sources.
        raw: The text as it originally appeared.
    """

    kind: str
    value: str
    raw: str = ""


@dataclass
class Lead:
    """A potential lead derived from ingestion or analysis.

    Attributes:
        identifier: Unique ID for the lead.
        summary: Human readable summary of the lead.
        evidence_ids: Identifiers for evidence items that support the lead.
        score: Priority score; higher means look at it sooner.
        reasons: Human readable explanations of why the lead scored as it did.
        entities: Entities the lead is built around.
        source: What produced the lead (``"ingest"`` or ``"analysis"``).
    """

    identifier: str
    summary: str
    evidence_ids: List[str] = field(default_factory=list)
    score: float = 0.0
    reasons: List[str] = field(default_factory=list)
    entities: List[Entity] = field(default_factory=list)
    source: str = "ingest"


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
    created_at: datetime = field(default_factory=_utcnow)
