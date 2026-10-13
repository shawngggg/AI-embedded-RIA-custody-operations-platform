"""The email outbox: every notice the custodian sends, its status, and the connection it goes through."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..access import require_capability
from ..db import get_db
from ..mail import _hold_reason, connection, send_m365
from ..orm import Case, OutboundEmail, User, utcnow
from ..views import iso

router = APIRouter(prefix="/api/outbox", tags=["email"])
STATUS = {"held": "Held", "queued": "Queued", "sent": "Sent", "failed": "Failed"}


def email_dict(db: Session, e: OutboundEmail, full: bool = False) -> dict:
    case = db.get(Case, e.case_id) if e.case_id else None
    out = {"id": e.id, "to": e.to_address, "to_name": e.to_name, "subject": e.subject, "template": e.template,
           "case_id": e.case_id, "case_title": case.title if case else None, "status": e.status,
           "status_label": STATUS.get(e.status, e.status), "provider": e.provider, "error": e.error,
           "created_at": iso(e.created_at), "sent_at": iso(e.sent_at)}
    if full:
        out["body"] = e.body
    return out


@router.get("")
def outbox(status: str | None = None, user: User = Depends(require_capability("outbox")), db: Session = Depends(get_db)):
    q = select(OutboundEmail).order_by(OutboundEmail.id.desc()).limit(300)
    if status:
        q = q.where(OutboundEmail.status == status)
    rows = db.scalars(q).all()
    return {"connection": connection(), "emails": [email_dict(db, e, full=True) for e in rows]}


@router.post("/{email_id}/retry")
def retry(email_id: int, user: User = Depends(require_capability("admin")), db: Session = Depends(get_db)):
    e = db.get(OutboundEmail, email_id)
    if e is None:
        raise HTTPException(404, "No such email")
    if e.status == "sent":
        raise HTTPException(409, "Already sent")
    why = _hold_reason(e.to_address)
    if why:
        raise HTTPException(409, why)
    try:
        send_m365(e)
        e.status, e.sent_at, e.error = "sent", utcnow(), None
    except Exception as exc:  # noqa: BLE001
        e.status, e.error = "failed", str(exc)[:500]
    audit.record(db, user, "email_retried", "email", e.id, status=e.status)
    db.commit()
    return email_dict(db, e, full=True)
