"""
Rolling review (periodic KYC refresh): deadline enforcement and completion.

- Deadlines (walkthrough note 16, Nov. 1, 2026): once a refresh's response date passes, the account gets the
  no-new-activity code (KYCR) until the review completes. The nightly batch runs this every evening (app/nightly.py);
  the Reviews page can run it by hand.
- Completion (note 17): a review leaves its basis on file. The reviewer writes what they checked and found, and either
  records that the RIA's refresh came back or says why it's skipped this time. A medium- or high-risk review (before
  or after) is a proposal until AML compliance approves it: maker-checker, never the same person. The refresh
  documents themselves aren't uploaded to the review yet: the note is the record.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from risk import REVIEW_MONTHS, add_months

from . import audit
from .config import settings
from .orm import Restriction, Review, User, utcnow

OUTCOMES = ("no_change", "profile_changed", "risk_increased", "exit")
CHECKED_TIERS = {"MEDIUM", "HIGH"}
CHECKERS = {"aml_compliance", "bsa_officer"}
MIN_NOTE, MIN_REASON = 30, 20


def enforce_deadlines(db: Session, actor: User | None) -> list[str]:
    """Place the no-new-activity code on every review whose refresh deadline has passed."""
    today = settings.today()
    restricted = []
    for r in db.scalars(select(Review).where(Review.status == "requested")).all():
        if r.response_due and today > r.response_due:
            code = Restriction(target_id=r.subject_id, code="KYCR", owner="periodic_review", source="review",
                               reason=f"Refresh not received by {r.response_due.isoformat()}",
                               placed_by=actor.display_name if actor else "Nightly batch")
            db.add(code)
            db.flush()
            r.status, r.restriction_id = "restricted", code.id
            r.history = list(r.history or []) + [{"on": today.isoformat(), "event": "restricted: no new activity"}]
            audit.record(db, actor, "restriction_placed", "restriction", code.id, code="KYCR", target=r.subject_id,
                         owner="periodic_review", reason=code.reason, source="review")
            restricted.append(r.subject_id)
    return restricted


def needs_checker(r: Review, new_tier: str) -> bool:
    return r.risk_tier in CHECKED_TIERS or new_tier in CHECKED_TIERS


def propose(db: Session, user: User, r: Review, outcome: str, new_tier: str, note: str, basis: str,
            skip_reason: str | None) -> str:
    """Complete a low-risk review, or propose a medium- or high-risk one for AML compliance. Returns the status."""
    if r.status in ("complete", "awaiting_approval"):
        raise HTTPException(409, "This review is already complete" if r.status == "complete"
                            else "This review is waiting for AML compliance's approval")
    if outcome not in OUTCOMES:
        raise HTTPException(422, "Unknown outcome")
    if new_tier not in REVIEW_MONTHS:
        raise HTTPException(422, "Risk tier must be LOW, MEDIUM, or HIGH")
    note, skip_reason = (note or "").strip(), (skip_reason or "").strip()
    if len(note) < MIN_NOTE:
        raise HTTPException(422, "Say what you checked and what you found (a couple of sentences)")
    if basis not in ("refresh_received", "skipped"):
        raise HTTPException(422, "Record that the refresh came back, or skip it with a reason")
    if basis == "refresh_received" and r.status == "scheduled":
        raise HTTPException(422, "No refresh was requested from the RIA: request it, or give a reason to skip it")
    if basis == "skipped" and len(skip_reason) < MIN_REASON:
        raise HTTPException(422, "Say why the refresh isn't needed this time")
    proposal = {"outcome": outcome, "new_tier": new_tier, "note": note, "basis": basis,
                "skip_reason": skip_reason or None, "by": user.display_name, "by_id": user.id,
                "on": settings.today().isoformat(), "from_status": r.status}
    if needs_checker(r, new_tier):
        r.proposal, r.status = proposal, "awaiting_approval"
        r.history = list(r.history or []) + [{"on": proposal["on"], "event": f"proposed complete: {outcome}, "
                                              f"{new_tier} (AML compliance approves)", "by": user.display_name}]
        audit.record(db, user, "review_proposed", "review", r.subject_id, **{k: v for k, v in proposal.items()
                                                                             if k != "by_id"})
        return r.status
    _complete(db, user, r, proposal)
    return r.status


def decide(db: Session, user: User, r: Review, approve: bool, note: str) -> None:
    """AML compliance approves a medium- or high-risk review's completion, or sends it back."""
    if r.status != "awaiting_approval" or not r.proposal:
        raise HTTPException(409, "No completion is waiting for approval")
    if user.role not in CHECKERS:
        audit.refuse(db, user, "review_approval", "AML compliance approves a medium- or high-risk review",
                     target_type="review", target_id=r.subject_id)
    if r.proposal.get("by_id") == user.id:
        audit.refuse(db, user, "review_approval", "The person who proposed the completion can't approve it",
                     target_type="review", target_id=r.subject_id)
    note = (note or "").strip()
    if len(note) < 10:
        raise HTTPException(422, "Say what you checked (a sentence is enough)")
    today = settings.today().isoformat()
    if approve:
        _complete(db, user, r, {**r.proposal, "approved_by": user.display_name, "approval_note": note})
        return
    r.status, r.proposal = r.proposal.get("from_status") or "requested", None
    r.history = list(r.history or []) + [{"on": today, "event": f"sent back by AML compliance: {note}",
                                          "by": user.display_name}]
    audit.record(db, user, "review_sent_back", "review", r.subject_id, note=note)


def _complete(db: Session, actor: User, r: Review, p: dict) -> None:
    today = settings.today()
    if r.restriction_id:
        code = db.get(Restriction, r.restriction_id)
        if code and code.active:
            code.active, code.removed_by, code.removed_at = False, actor.display_name, utcnow()
            code.removal_reason = "Refresh received; review complete" if p["basis"] == "refresh_received" \
                else f"Review complete without a refresh: {p['skip_reason']}"
            audit.record(db, actor, "restriction_removed", "restriction", code.id, code="KYCR", target=r.subject_id,
                         reason=code.removal_reason)
        r.restriction_id = None
    r.status, r.risk_tier, r.proposal = "complete", p["new_tier"], None
    r.next_review = add_months(today, REVIEW_MONTHS[p["new_tier"]])
    event = {"on": today.isoformat(), "event": f"complete: {p['outcome']}", "next_review": r.next_review.isoformat(),
             "note": p["note"], "basis": p["basis"], "skip_reason": p.get("skip_reason"), "by": p["by"]}
    if p.get("approved_by"):
        event["approved_by"] = p["approved_by"]
    r.history = list(r.history or []) + [event]
    audit.record(db, actor, "review_completed", "review", r.subject_id, outcome=p["outcome"], new_tier=p["new_tier"],
                 next_review=r.next_review.isoformat(), basis=p["basis"], skip_reason=p.get("skip_reason"),
                 note=p["note"], proposed_by=p["by"], approved_by=p.get("approved_by"))
