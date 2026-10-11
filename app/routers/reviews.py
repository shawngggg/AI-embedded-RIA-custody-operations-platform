"""Rolling review: due reviews, refresh requests, missed deadlines, completion."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from risk import REVIEW_MONTHS, add_months
from rolling_review import RESPONSE_DAYS

from .. import audit
from ..config import settings
from ..db import get_db
from ..orm import Restriction, Review, User, utcnow
from ..security import require_internal, require_roles
from ..views import review_dict

router = APIRouter(prefix="/api/reviews", tags=["reviews"])
reviewer = require_roles("periodic_review", "ops_supervisor")


@router.get("")
def list_reviews(user: User = Depends(require_internal), db: Session = Depends(get_db)):
    today = settings.today()
    rows = db.scalars(select(Review).order_by(Review.next_review)).all()
    return [review_dict(r, today) for r in rows]


@router.post("/{review_id}/request")
def request_refresh(review_id: int, user: User = Depends(reviewer), db: Session = Depends(get_db)):
    r = db.get(Review, review_id)
    if r is None:
        raise HTTPException(404, "No such review")
    if r.status not in ("scheduled", "complete"):
        raise HTTPException(409, f"This review is {r.status}")
    today = settings.today()
    r.status, r.requested_on, r.response_due = "requested", today, today + timedelta(days=RESPONSE_DAYS)
    r.history = list(r.history or []) + [{"on": today.isoformat(), "event": "refresh requested from the RIA",
                                          "response_due": r.response_due.isoformat()}]
    audit.record(db, user, "review_requested", "review", r.subject_id, response_due=r.response_due.isoformat())
    db.commit()
    return review_dict(r, today)


@router.post("/enforce-deadlines")
def enforce(user: User = Depends(reviewer), db: Session = Depends(get_db)):
    """Place the no-new-activity code on every review whose refresh deadline has passed."""
    today = settings.today()
    restricted = []
    for r in db.scalars(select(Review).where(Review.status == "requested")).all():
        if r.response_due and today > r.response_due:
            code = Restriction(target_id=r.subject_id, code="KYCR", owner="periodic_review", source="review",
                               reason=f"Refresh not received by {r.response_due.isoformat()}",
                               placed_by=user.display_name)
            db.add(code)
            db.flush()
            r.status, r.restriction_id = "restricted", code.id
            r.history = list(r.history or []) + [{"on": today.isoformat(), "event": "restricted: no new activity"}]
            audit.record(db, user, "restriction_placed", "restriction", code.id, code="KYCR", target=r.subject_id,
                         owner="periodic_review", reason=code.reason, source="review")
            restricted.append(r.subject_id)
    db.commit()
    return {"restricted": restricted}


class Complete(BaseModel):
    outcome: str
    new_tier: str


@router.post("/{review_id}/complete")
def complete(review_id: int, body: Complete, user: User = Depends(require_roles("periodic_review")),
             db: Session = Depends(get_db)):
    r = db.get(Review, review_id)
    if r is None:
        raise HTTPException(404, "No such review")
    if body.outcome not in ("no_change", "profile_changed", "risk_increased", "exit"):
        raise HTTPException(422, "Unknown outcome")
    if body.new_tier not in REVIEW_MONTHS:
        raise HTTPException(422, "Risk tier must be LOW, MEDIUM, or HIGH")
    today = settings.today()
    if r.restriction_id:
        code = db.get(Restriction, r.restriction_id)
        if code and code.active:
            code.active, code.removed_by, code.removed_at = False, user.display_name, utcnow()
            code.removal_reason = "Refresh received; review complete"
            audit.record(db, user, "restriction_removed", "restriction", code.id, code="KYCR", target=r.subject_id,
                         reason=code.removal_reason)
        r.restriction_id = None
    r.status, r.risk_tier = "complete", body.new_tier
    r.next_review = add_months(today, REVIEW_MONTHS[body.new_tier])
    r.history = list(r.history or []) + [{"on": today.isoformat(), "event": f"complete: {body.outcome}",
                                          "next_review": r.next_review.isoformat()}]
    audit.record(db, user, "review_completed", "review", r.subject_id, outcome=body.outcome, new_tier=body.new_tier,
                 next_review=r.next_review.isoformat())
    db.commit()
    return review_dict(r, today)
