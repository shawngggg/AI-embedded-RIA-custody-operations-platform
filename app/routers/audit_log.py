"""Audit trail: per case, or the whole log as JSON."""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import to_dict
from ..db import get_db
from ..orm import AuditEvent, User
from ..security import current_user, require_internal
from ..views import get_case_for

router = APIRouter(prefix="/api", tags=["audit"])


@router.get("/cases/{case_id}/audit")
def case_audit(case_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_case_for(db, user, case_id)
    rows = db.scalars(select(AuditEvent).where(AuditEvent.case_id == case_id).order_by(AuditEvent.id)).all()
    return [to_dict(e) for e in rows]


@router.get("/audit")
def audit_log(limit: int = 200, action: str | None = None, user: User = Depends(require_internal),
              db: Session = Depends(get_db)):
    q = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(min(limit, 2000))
    if action:
        q = q.where(AuditEvent.action == action)
    return [to_dict(e) for e in db.scalars(q).all()]


@router.get("/audit/export")
def export(user: User = Depends(require_internal), db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditEvent).order_by(AuditEvent.id)).all()
    return JSONResponse([to_dict(e) for e in rows],
                        headers={"Content-Disposition": "attachment; filename=audit-log.json"})
