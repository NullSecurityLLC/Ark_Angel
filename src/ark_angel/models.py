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
class Place:
    """A named location in a case's gazetteer.

    Attributes:
        name: Canonical name, e.g. ``"Union Station"``.
        latitude: Latitude of the place's centre.
        longitude: Longitude of the place's centre.
        aliases: Other ways the place is written in evidence.
        radius_m: How precise a mention is. A landmark is a few hundred metres;
            a city is kilometres. Mentions of precise places join geo clusters,
            and coarse places instead collect geotagged evidence inside them.
        source: Where the entry came from (``"manual"``, ``"import"``, ``"nominatim"``).
    """

    name: str
    latitude: float
    longitude: float
    aliases: List[str] = field(default_factory=list)
    radius_m: float = 250.0
    source: str = "manual"


@dataclass
class Review:
    """One investigator decision about a lead. Reviews are append-only; the latest wins.

    Attributes:
        lead_id: Identifier of the lead reviewed.
        status: ``"verified"``, ``"dismissed"`` or ``"open"`` (reopened).
        note: Why. Required for dismissals.
        reviewer: Who made the call.
        reviewed_at: ISO-8601 timestamp.
        evidence_ids: The lead's evidence at review time, so new corroboration
            arriving later can be spotted.
    """

    lead_id: str
    status: str
    note: str = ""
    reviewer: str = ""
    reviewed_at: str = ""
    evidence_ids: List[str] = field(default_factory=list)


@dataclass
class Protocol:
    """God's Eye: a principal-defender case's standby emergency protocol.

    It sits on standby until activated by a missed check-in, a duress code or by
    hand. Then it seals a fingerprint of the case and produces an emergency
    briefing for police and the protection team. It only ever reads the case's
    own evidence: it tracks nobody.

    Attributes:
        state: ``"off"``, ``"standby"`` (armed) or ``"active"``.
        checkin_hours: Hours allowed between check-ins before it activates by
            itself; 0 means no deadline (manual and duress activation only).
        last_checkin: ISO-8601 UTC time of the latest check-in (or arming).
        code_hash: Salted scrypt hash of the check-in code.
        duress_hash: Salted scrypt hash of the duress code: a check-in that
            looks normal on screen but activates the protocol.
        contacts: Emergency contacts' email addresses, alerted on activation.
        activated_at: ISO-8601 UTC time of the current activation.
        trigger: What activated it: ``"manual"``, ``"missed check-in"`` or ``"duress"``.
        reason: The note given on activation.
        snapshot_sha256: SHA-256 of the case as it stood at activation.
        events: Append-only log of everything that happened to the protocol.
    """

    state: str = "off"
    checkin_hours: float = 0
    last_checkin: str = ""
    code_hash: str = ""
    duress_hash: str = ""
    contacts: List[str] = field(default_factory=list)
    activated_at: str = ""
    trigger: str = ""
    reason: str = ""
    snapshot_sha256: str = ""
    events: List[dict] = field(default_factory=list)


@dataclass
class Principal:
    """The person a principal-defender case protects.

    A defender case turns the engine around: instead of finding someone, it
    shows how findable the principal is and who is threatening them.

    Attributes:
        name: The principal's name as it appears in evidence.
        aliases: Other names they go by (maiden name, nickname, initials).
        phones: Their phone numbers, normalized to digits.
        emails: Their email addresses, lowercased.
        handles: Their social handles, lowercased without ``@``.
        plates: Their vehicles' plates, normalized.
        places: Sensitive places (home, work, school, a gym) that must not
            leak. The name is only a label; the aliases (a street address, a
            building name) are what's searched for in evidence. Geotagged
            evidence inside the radius counts too.
        authorized_by: Who authorized protecting this person and how, e.g.
            "the principal, signed engagement 2026-10-12". Required: defender
            mode exists for people who asked for protection.
        created_at: ISO-8601 timestamp of when the profile was set up.
    """

    name: str
    authorized_by: str
    aliases: List[str] = field(default_factory=list)
    phones: List[str] = field(default_factory=list)
    emails: List[str] = field(default_factory=list)
    handles: List[str] = field(default_factory=list)
    plates: List[str] = field(default_factory=list)
    places: List[Place] = field(default_factory=list)
    created_at: str = ""
    protocol: Protocol = field(default_factory=Protocol)


@dataclass
class Case:
    """A case aggregates leads and evidence for an investigation.

    Attributes:
        identifier: Unique case identifier.
        title: Short title for the case.
        leads: Collected leads associated with the case.
        evidence: Evidence items linked to the case.
        places: The case's gazetteer of named locations.
        reviews: Investigator decisions on leads, oldest first.
        created_at: Timestamp indicating when the case was opened.
        principal: Set on principal-defender cases: the person being protected.
            ``None`` for ordinary missing-person cases.
    """

    identifier: str
    title: str
    leads: List[Lead] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)
    created_at: datetime = field(default_factory=_utcnow)
    places: List[Place] = field(default_factory=list)
    reviews: List[Review] = field(default_factory=list)
    principal: Optional[Principal] = None

    @property
    def defending(self) -> bool:
        """True for a principal-defender case."""
        return self.principal is not None

    def known_places(self) -> List[Place]:
        """The case's places plus, on a defender case, the principal's sensitive places.

        For filtering place suggestions only: sensitive places stay out of the
        gazetteer that reports and maps are built from.
        """
        return list(self.places) + (list(self.principal.places) if self.principal else [])
