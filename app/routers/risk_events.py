"""Operational risk events: errors a control let through, and the remediation path the team agrees."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_db
from ..orm import ROLES, Case, OpsRiskEvent, User, utcnow
from ..security import require_internal
from ..views import iso

router = APIRouter(prefix="/api/risk-events", tags=["risk events"])
LEADS = ("ops_supervisor", "ria_service_lead")
REMEDIATION = {"training": "Additional training", "technology": "Technology enhancement",
               "procedure": "Procedure update", "none": "No action needed"}
STATUSES = {"open": "Open", "remediation_agreed": "Remediation agreed", "closed": "Closed"}


def event_dict(db: Session, e: OpsRiskEvent, user: User) -> dict:
    case = db.get(Case, e.case_id) if e.case_id else None
    return {"id": e.id, "ref": f"ORE-{e.id:04d}", "category": e.category, "case_id": e.case_id,
            "case_title": case.title if case else None, "target_id": e.target_id, "summary": e.summary,
            "detected_by": e.detected_by, "approved_by": e.approved_by, "owner": e.owner,
            "owner_label": ROLES.get(e.owner, e.owner), "status": e.status, "status_label": STATUSES[e.status],
            "remediation": e.remediation, "remediation_label": REMEDIATION.get(e.remediation or "", None),
            "remediation_note": e.remediation_note, "created_at": iso(e.created_at), "updated_by": e.updated_by,
            "closed_at": iso(e.closed_at), "can_update": user.role in LEADS and e.status != "closed"}


@router.get("")
def list_events(user: User = Depends(require_internal), db: Session = Depends(get_db)):
    rows = db.scalars(select(OpsRiskEvent).order_by(OpsRiskEvent.id.desc())).all()
    return {"events": [event_dict(db, e, user) for e in rows], "remediation": REMEDIATION}


class Update(BaseModel):
    remediation: str
    note: str
    close: bool = False


@router.post("/{event_id}")
def update(event_id: int, body: Update, user: User = Depends(require_internal), db: Session = Depends(get_db)):
    e = db.get(OpsRiskEvent, event_id)
    if e is None:
        raise HTTPException(404, "No such event")
    if user.role not in LEADS:
        audit.refuse(db, user, "ops_risk_event_update", "A team lead records the remediation path",
                     target_type="ops_risk_event", target_id=e.id, case_id=e.case_id)
    if e.status == "closed":
        raise HTTPException(409, "This event is closed")
    if body.remediation not in REMEDIATION:
        raise HTTPException(422, "Choose training, a technology enhancement, a procedure update, or no action")
    if len(body.note.strip()) < 10:
        raise HTTPException(422, "Describe the remediation (at least a sentence)")
    e.remediation, e.remediation_note, e.updated_by = body.remediation, body.note.strip(), user.display_name
    e.status = "closed" if body.close else "remediation_agreed"
    if body.close:
        e.closed_at = utcnow()
    audit.record(db, user, "ops_risk_event_closed" if body.close else "ops_risk_event_updated", "ops_risk_event", e.id,
                 case_id=e.case_id, remediation=body.remediation, note=e.remediation_note)
    db.commit()
    return event_dict(db, e, user)
