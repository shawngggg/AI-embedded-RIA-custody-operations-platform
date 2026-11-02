"""Rolling review: due reviews, refresh requests, missed deadlines, completion."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from rolling_review import RESPONSE_DAYS

from .. import audit, reviews
from ..config import settings
from ..db import get_db
from ..orm import Review, User
from ..access import require_capability
from ..security import require_roles
from ..views import review_dict

router = APIRouter(prefix="/api/reviews", tags=["reviews"])
reviewer = require_roles("periodic_review", "ops_supervisor")


@router.get("")
def list_reviews(user: User = Depends(require_capability("reviews")), db: Session = Depends(get_db)):
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
    """Place the no-new-activity code on every review whose refresh deadline has passed (the nightly batch does this
    every evening; this runs it now)."""
    restricted = reviews.enforce_deadlines(db, user)
    db.commit()
    return {"restricted": restricted}


class Complete(BaseModel):
    outcome: str
    new_tier: str
    note: str = Field(default="", max_length=2000)
    basis: str = "refresh_received"
    skip_reason: str | None = Field(default=None, max_length=1000)


def _get(db: Session, review_id: int) -> Review:
    r = db.get(Review, review_id)
    if r is None:
        raise HTTPException(404, "No such review")
    return r


@router.post("/{review_id}/complete")
def complete(review_id: int, body: Complete, user: User = Depends(require_roles("periodic_review")),
             db: Session = Depends(get_db)):
    """Complete a review with its basis; a medium- or high-risk one waits for AML compliance (walkthrough note 17)."""
    r = _get(db, review_id)
    reviews.propose(db, user, r, body.outcome, body.new_tier, body.note, body.basis, body.skip_reason)
    db.commit()
    return review_dict(r, settings.today())


class Decision(BaseModel):
    approve: bool
    note: str = Field(min_length=10, max_length=1000)


@router.post("/{review_id}/approve")
def approve(review_id: int, body: Decision, user: User = Depends(require_capability("reviews")),
            db: Session = Depends(get_db)):
    r = _get(db, review_id)
    reviews.decide(db, user, r, body.approve, body.note)
    db.commit()
    return review_dict(r, settings.today())
