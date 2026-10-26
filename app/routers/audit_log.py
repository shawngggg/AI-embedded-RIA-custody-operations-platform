"""Audit trail: per case, or the whole log as JSON."""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, audit_chain
from ..audit import to_dict
from ..db import get_db
from ..orm import AuditEvent, User
from ..access import require_capability
from ..views import get_case_for

router = APIRouter(prefix="/api", tags=["audit"])


@router.get("/cases/{case_id}/audit")
def case_audit(case_id: int, user: User = Depends(require_capability("cases")), db: Session = Depends(get_db)):
    """A case's audit trail is the custodian's record: internal teams only, never the portal."""
    get_case_for(db, user, case_id)
    rows = db.scalars(select(AuditEvent).where(AuditEvent.case_id == case_id).order_by(AuditEvent.id)).all()
    return [to_dict(e) for e in rows]


@router.get("/audit")
def audit_log(limit: int = 200, action: str | None = None, user: User = Depends(require_capability("audit")),
              db: Session = Depends(get_db)):
    q = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(min(limit, 2000))
    if action:
        q = q.where(AuditEvent.action == action)
    return [to_dict(e) for e in db.scalars(q).all()]


@router.get("/audit/export")
def export(user: User = Depends(require_capability("audit")), db: Session = Depends(get_db)):
    """The whole log, hash-chained (app/audit_chain.py). The head is recorded, so the next export can prove nothing
    before it changed."""
    data = audit_chain.export(db)
    audit.record(db, user, "audit_exported", "audit_log", data["head"]["id"] or "-", head_id=data["head"]["id"],
                 head_hash=data["head"]["hash"], events=len(data["events"]), checkpoints_ok=data["verified"])
    db.commit()
    return JSONResponse(data, headers={"Content-Disposition": "attachment; filename=audit-log.json"})
