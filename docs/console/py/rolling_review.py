"""
Rolling review (map 03) for the MVP.

Reviews come due by risk tier (demo policy: high risk every 6 months, others
every 12; the CDD rule sets no fixed cadence). A due review asks the RIA for
updated information with a response deadline. A missed deadline places the
periodic review team's no-new-activity code (KYCR); a completed review removes
it, records the outcome, and sets the next date from the new risk tier.
Ownership changes and new screening hits start an early review.

The periodic review team owns this process; the code it places can only be
removed by that team.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from restrictions import Function, RestrictionRegistry
from risk import REVIEW_MONTHS, add_months

RESPONSE_DAYS = 30          # demo policy: time the RIA has to send updated information
DUE_WINDOW_DAYS = 30        # reviews due within this window show in the queue


@dataclass
class Review:
    subject_id: str                 # account or firm id
    subject_name: str
    risk_tier: str
    next_review: date
    status: str = "scheduled"       # scheduled | requested | restricted | complete
    requested_on: date | None = None
    response_due: date | None = None
    trigger: str = "scheduled"      # scheduled | ownership_change | screening_hit
    restriction_id: str | None = None
    history: list[dict] = field(default_factory=list)

    def due(self, as_of: date) -> bool:
        return self.status in ("scheduled",) and self.next_review <= as_of + timedelta(days=DUE_WINDOW_DAYS)

    def overdue(self, as_of: date) -> bool:
        return self.status == "requested" and self.response_due is not None and as_of > self.response_due

    def to_dict(self) -> dict:
        return {"subject_id": self.subject_id, "subject_name": self.subject_name, "risk_tier": self.risk_tier,
                "next_review": self.next_review.isoformat(), "status": self.status,
                "requested_on": self.requested_on.isoformat() if self.requested_on else None,
                "response_due": self.response_due.isoformat() if self.response_due else None,
                "trigger": self.trigger, "history": list(self.history)}


def _stamp(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 12, 0, tzinfo=timezone.utc)


def schedule(subject_id: str, name: str, risk_tier: str, opened_on: date) -> Review:
    return Review(subject_id, name, risk_tier, add_months(opened_on, REVIEW_MONTHS[risk_tier]))


def trigger_early(review: Review, reason: str, as_of: date) -> Review:
    """Ownership changes and new screening hits bring the review forward to today."""
    review.next_review, review.trigger = as_of, reason
    if review.status == "complete":
        review.status = "scheduled"
    review.history.append({"on": as_of.isoformat(), "event": f"early review: {reason}"})
    return review


def request_refresh(review: Review, as_of: date) -> Review:
    """Ask the RIA for updated KYC, KYB, CIP, CDD, EDD, and PEP information."""
    if review.status != "scheduled":
        raise ValueError(f"Review for {review.subject_id} is {review.status}, not scheduled")
    review.status, review.requested_on = "requested", as_of
    review.response_due = as_of + timedelta(days=RESPONSE_DAYS)
    review.history.append({"on": as_of.isoformat(), "event": "refresh requested",
                           "response_due": review.response_due.isoformat()})
    return review


def enforce_deadline(review: Review, registry: RestrictionRegistry, as_of: date,
                     by: str = "review.scheduler") -> Review:
    """A missed response deadline restricts new activity until the refresh arrives."""
    if not review.overdue(as_of):
        return review
    r = registry.place(review.subject_id, "KYCR", f"Refresh not received by {review.response_due.isoformat()}",
                       by, Function.PERIODIC_REVIEW, _stamp(as_of))
    review.status, review.restriction_id = "restricted", r.restriction_id
    review.history.append({"on": as_of.isoformat(), "event": "restricted: no new activity (KYCR)"})
    return review


def complete(review: Review, outcome: str, new_tier: str, registry: RestrictionRegistry, as_of: date,
             by: str = "review.analyst") -> Review:
    """
    Record the outcome (no_change | profile_changed | risk_increased | exit),
    lift the review's restriction, and set the next date from the new tier.
    """
    if outcome not in ("no_change", "profile_changed", "risk_increased", "exit"):
        raise ValueError("outcome must be no_change, profile_changed, risk_increased, or exit")
    if review.restriction_id:
        registry.remove(review.restriction_id, by, Function.PERIODIC_REVIEW, "Refresh received; review complete",
                        at=_stamp(as_of))
        review.restriction_id = None
    review.risk_tier = new_tier
    review.status = "complete"
    review.next_review = add_months(as_of, REVIEW_MONTHS[new_tier])
    review.history.append({"on": as_of.isoformat(), "event": f"complete: {outcome}",
                           "next_review": review.next_review.isoformat()})
    return review
