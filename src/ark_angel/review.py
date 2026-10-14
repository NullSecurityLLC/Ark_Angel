"""Investigator review of leads: verify, dismiss, reopen.

Analysis leads are rebuilt on every ``enrich``, so review decisions live in an
append-only log on the case (``Case.reviews``) and are matched back onto
leads each time. Two rules keep that honest:

- A location cluster's identifier follows its centre, which drifts as evidence
  arrives. A review of a cluster that no longer exists carries over to the
  current cluster sharing the most evidence with it.
- A dismissed lead that gains evidence it didn't have when dismissed comes
  back as open, flagged as resurfaced. New corroboration is exactly when a
  dismissal deserves a second look.
"""
from __future__ import annotations

import getpass
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Sequence

from ark_angel.analysis.rules import RuleBasedAnalyzer
from ark_angel.models import Case, Lead, Review

STATUSES = ("open", "verified", "dismissed")
_GROUP_ORDER = {"verified": 0, "open": 1, "dismissed": 2}
_rules = RuleBasedAnalyzer()


def lead_score(lead: Lead) -> float:
    """A lead's stored score, falling back to rule weights for older case files."""
    return lead.score or _rules.score_lead(lead)


@dataclass
class ReviewState:
    """Where a lead stands after applying the review log."""

    status: str = "open"
    review: Optional[Review] = None
    new_evidence_ids: list[str] = field(default_factory=list)

    @property
    def resurfaced(self) -> bool:
        """Dismissed earlier, but new evidence has arrived since."""
        return self.review is not None and self.review.status == "dismissed" and self.status == "open"


@dataclass
class RankedLead:
    rank: int
    lead: Lead
    state: ReviewState


def latest_reviews(reviews: Sequence[Review]) -> dict[str, Review]:
    latest: dict[str, Review] = {}
    for review in reviews:
        latest[review.lead_id] = review
    return latest


def review_states(leads: Sequence[Lead], reviews: Sequence[Review]) -> dict[str, ReviewState]:
    """Apply the review log to the current leads."""
    latest = latest_reviews(reviews)
    carried = _carry_over_geo_reviews(leads, latest)
    return {lead.identifier: _state(lead, latest.get(lead.identifier) or carried.get(lead.identifier))
            for lead in leads}


def _carry_over_geo_reviews(leads: Sequence[Lead], latest: dict[str, Review]) -> dict[str, Review]:
    """Map reviews of clusters whose identifier drifted onto the cluster that replaced them.

    Each old review goes only to the current cluster it overlaps most, and
    only if that cluster has no review of its own. A superseded review can't
    leak onto a neighbouring cluster. Newer reviews get first pick.
    """
    geo_leads = [lead for lead in leads if lead.identifier.startswith("geo-")]
    current_ids = {lead.identifier for lead in leads}
    orphans = [r for lead_id, r in latest.items() if lead_id.startswith("geo-") and lead_id not in current_ids]
    carried: dict[str, Review] = {}
    for review in sorted(orphans, key=lambda r: r.reviewed_at, reverse=True):
        snapshot = set(review.evidence_ids)
        overlaps = [(len(snapshot & set(lead.evidence_ids)), lead) for lead in geo_leads]
        overlap, best = max(overlaps, key=lambda item: item[0], default=(0, None))
        if overlap and best.identifier not in latest and best.identifier not in carried:
            carried[best.identifier] = review
    return carried


def _state(lead: Lead, review: Optional[Review]) -> ReviewState:
    if review is None or review.status == "open":
        return ReviewState(review=review)
    new_ids = [eid for eid in lead.evidence_ids if eid not in set(review.evidence_ids)]
    if review.status == "dismissed" and new_ids:
        return ReviewState(status="open", review=review, new_evidence_ids=new_ids)
    return ReviewState(status=review.status, review=review, new_evidence_ids=new_ids)


def ranked_leads(case: Case) -> list[RankedLead]:
    """Every lead in display order: verified, then open, then dismissed.

    Numbers come from score order alone, so verifying or dismissing a lead
    never renumbers the others. ``#3`` stays ``#3`` until evidence changes.
    """
    states = review_states(case.leads, case.reviews)
    by_score = sorted(case.leads, key=lambda lead: (-lead_score(lead), lead.identifier))
    ranked = [RankedLead(rank, lead, states[lead.identifier]) for rank, lead in enumerate(by_score, start=1)]
    return sorted(ranked, key=lambda item: (_GROUP_ORDER[item.state.status], item.rank))


def resolve_lead(case: Case, ref: str) -> RankedLead:
    """Find a lead by rank number (``3`` or ``#3``), exact identifier, or unique identifier prefix.

    Raises:
        ValueError: if nothing or more than one lead matches.
    """
    ranked = ranked_leads(case)
    number = ref.lstrip("#")
    if number.isdigit():
        index = int(number)
        for item in ranked:
            if item.rank == index:
                return item
        raise ValueError(f"no lead #{index}; case {case.identifier!r} has {len(ranked)} lead(s)")
    exact = [item for item in ranked if item.lead.identifier == ref]
    if exact:
        return exact[0]
    matches = [item for item in ranked if item.lead.identifier.startswith(ref)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"no lead matches {ref!r} (see `ark-angel lead list {case.identifier}`)")
    shown = ", ".join(item.lead.identifier for item in matches[:5])
    raise ValueError(f"{ref!r} matches {len(matches)} leads ({shown}{', …' if len(matches) > 5 else ''})")


def default_reviewer() -> str:
    try:
        return os.environ.get("ARK_ANGEL_REVIEWER") or getpass.getuser()
    except Exception:
        return "unknown"


def make_review(lead: Lead, status: str, note: str = "", reviewer: str = "") -> Review:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    if status == "dismissed" and not note.strip():
        raise ValueError("dismissing a lead needs a --note saying why")
    return Review(
        lead_id=lead.identifier,
        status=status,
        note=note.strip(),
        reviewer=reviewer or default_reviewer(),
        reviewed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        evidence_ids=list(lead.evidence_ids),
    )
