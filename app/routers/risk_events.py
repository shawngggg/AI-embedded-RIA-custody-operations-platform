"""
Risk events: operational, regulatory, and financial events, and the remediation path the owning team agrees.
Each person sees the events in their own area (risk_register.areas_for), plus any they reported themselves.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_db
from ..orm import Case, OpsRiskEvent, User, utcnow
from ..risk_register import AREAS, OWNERS, RISK_TYPES, SOURCES, areas_for, log_event, sweep_sla_breaches, visible
from ..security import require_internal
from ..views import iso

router = APIRouter(prefix="/api/risk-events", tags=["risk events"])
LEADS = ("ops_supervisor", "ria_service_lead")
REMEDIATION = {"training": "Additional training", "technology": "Technology enhancement",
               "procedure": "Procedure update", "report": "Regulatory report or notice filed",
               "none": "No action needed"}
STATUSES = {"open": "Open", "remediation_agreed": "Remediation agreed", "closed": "Closed"}


def can_update(user: User, e: OpsRiskEvent) -> bool:
    """In the event's own area, the team lead or the owning team records its remediation."""
    return e.status != "closed" and e.area in areas_for(user) and (user.role in LEADS or user.role == e.owner)


def event_dict(db: Session, e: OpsRiskEvent, user: User) -> dict:
    case = db.get(Case, e.case_id) if e.case_id else None
    return {"id": e.id, "ref": f"ORE-{e.id:04d}", "category": e.category, "case_id": e.case_id,
            "risk_type": e.risk_type, "risk_type_label": RISK_TYPES.get(e.risk_type, e.risk_type),
            "area": e.area, "area_label": AREAS.get(e.area, e.area), "reported_by_me": e.reported_by == user.id,
            "source": e.source, "source_label": SOURCES.get(e.source, e.source),
            "case_title": case.title if case else None, "target_id": e.target_id, "summary": e.summary,
            "detected_by": e.detected_by, "approved_by": e.approved_by, "owner": e.owner,
            "owner_label": OWNERS.get(e.owner, e.owner), "status": e.status, "status_label": STATUSES[e.status],
            "remediation": e.remediation, "remediation_label": REMEDIATION.get(e.remediation or "", None),
            "remediation_note": e.remediation_note, "created_at": iso(e.created_at), "updated_by": e.updated_by,
            "closed_at": iso(e.closed_at), "can_update": can_update(user, e)}


@router.get("")
def list_events(risk_type: str | None = None, status: str | None = None, user: User = Depends(require_internal),
                db: Session = Depends(get_db)):
    if sweep_sla_breaches(db):
        db.commit()
    q = select(OpsRiskEvent).order_by(OpsRiskEvent.id.desc())
    if risk_type:
        q = q.where(OpsRiskEvent.risk_type == risk_type)
    if status == "open":
        q = q.where(OpsRiskEvent.status != "closed")
    elif status:
        q = q.where(OpsRiskEvent.status == status)
    rows = [e for e in db.scalars(q).all() if visible(user, e)]
    mine = areas_for(user)
    return {"events": [event_dict(db, e, user) for e in rows], "remediation": REMEDIATION, "risk_types": RISK_TYPES,
            "owners": OWNERS, "areas": AREAS, "my_areas": [{"area": a, "label": AREAS[a]} for a in AREAS if a in mine]}


class Report(BaseModel):
    category: str = Field(min_length=3, max_length=60)
    risk_type: str
    area: str                           # the function it pertains to; that area's lead owns the follow-up
    summary: str = Field(min_length=10)
    case_id: int | None = None
    target_id: str | None = None


@router.post("")
def report(body: Report, user: User = Depends(require_internal), db: Session = Depends(get_db)):
    """Anyone on an internal team can report an event in any area; that area's team triages it, and the
    reporter keeps seeing it."""
    if body.risk_type not in RISK_TYPES or body.area not in AREAS:
        raise HTTPException(422, "Choose operational, regulatory, or financial, and the area it pertains to")
    case = db.get(Case, body.case_id) if body.case_id else None
    if body.case_id and case is None:
        raise HTTPException(404, "No such case")
    e = log_event(db, user, category=body.category.strip(), risk_type=body.risk_type, source="manual",
                  summary=body.summary.strip(), area=body.area, detected_by=user.display_name, case=case,
                  target_id=(body.target_id or "").strip() or None, reported_by=user)
    db.commit()
    return event_dict(db, e, user)


class Update(BaseModel):
    remediation: str
    note: str
    close: bool = False


@router.post("/{event_id}")
def update(event_id: int, body: Update, user: User = Depends(require_internal), db: Session = Depends(get_db)):
    e = db.get(OpsRiskEvent, event_id)
    if e is None:
        raise HTTPException(404, "No such event")
    if not visible(user, e):
        audit.refuse(db, user, "ops_risk_event_update", "No such event", status=404, target_type="ops_risk_event",
                     target_id=e.id, scope="outside the person's area")
    if e.status != "closed" and not can_update(user, e):
        audit.refuse(db, user, "ops_risk_event_update", "A team lead or the owning team records the remediation path",
                     target_type="ops_risk_event", target_id=e.id, case_id=e.case_id)
    if e.status == "closed":
        raise HTTPException(409, "This event is closed")
    if body.remediation not in REMEDIATION:
        raise HTTPException(422, "Choose training, a technology enhancement, a procedure update, a regulatory report, "
                                 "or no action")
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
