"""Regulation S-P incidents (app/privacy.py): the platform administrator, the operations supervisor, and AML compliance
open and work them; the steps are logged."""
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import privacy
from ..access import require_capability
from ..db import get_db
from ..orm import Account, PrivacyIncident, User

router = APIRouter(prefix="/api/privacy", tags=["privacy"])


class NewIncident(BaseModel):
    summary: str = Field(min_length=20, max_length=1000)
    aware_on: date
    accounts: list[str] = Field(default_factory=list)
    information: str = Field(min_length=3, max_length=500)
    service_provider: str | None = Field(default=None, max_length=200)
    provider_aware_at: datetime | None = None
    provider_notified_at: datetime | None = None


class Step(BaseModel):
    note: str = Field(min_length=10, max_length=1000)


class Determination(BaseModel):
    decision: str = Field(pattern="^(notify|no_notice)$")
    reason: str = Field(min_length=20, max_length=1000)


def _get(db: Session, incident_id: int) -> PrivacyIncident:
    i = db.get(PrivacyIncident, incident_id)
    if i is None:
        raise HTTPException(404, "No such incident")
    return i


@router.get("")
def incidents(user: User = Depends(require_capability("privacy")), db: Session = Depends(get_db)):
    rows = db.scalars(select(PrivacyIncident).order_by(PrivacyIncident.id.desc())).all()
    return {"incidents": [privacy.to_dict(i) for i in rows], "notice_days": privacy.NOTICE_DAYS,
            "provider_hours": privacy.PROVIDER_HOURS, "source": privacy.SOURCE}


@router.post("")
def open_incident(body: NewIncident, user: User = Depends(require_capability("privacy")), db: Session = Depends(get_db)):
    unknown = [a for a in body.accounts if db.get(Account, a) is None]
    if unknown:
        raise HTTPException(422, f"No such account: {', '.join(unknown)}")
    i = privacy.open_incident(db, user, **body.model_dump())
    db.commit()
    return privacy.to_dict(i)


@router.post("/{incident_id}/contain")
def contain(incident_id: int, body: Step, user: User = Depends(require_capability("privacy")),
            db: Session = Depends(get_db)):
    i = _get(db, incident_id)
    privacy.contain(db, user, i, body.note)
    db.commit()
    return privacy.to_dict(i)


@router.post("/{incident_id}/determine")
def determine(incident_id: int, body: Determination, user: User = Depends(require_capability("privacy")),
              db: Session = Depends(get_db)):
    i = _get(db, incident_id)
    if i.contained_on is None:
        raise HTTPException(409, "Contain it first: the notice says what was done")
    privacy.determine(db, user, i, body.decision, body.reason)
    db.commit()
    return privacy.to_dict(i)


@router.post("/{incident_id}/notify")
def notify(incident_id: int, user: User = Depends(require_capability("privacy")), db: Session = Depends(get_db)):
    i = _get(db, incident_id)
    if i.determination != "notify":
        raise HTTPException(409, "Record the determination to notify first")
    if i.notices_sent_on:
        raise HTTPException(409, "The notices already went out")
    privacy.send_notices(db, user, i)
    db.commit()
    return privacy.to_dict(i)
