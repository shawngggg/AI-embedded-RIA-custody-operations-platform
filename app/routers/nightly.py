"""The nightly batch (app/nightly.py): Cloud Scheduler with its token, or the operations supervisor by hand."""
import hmac

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, nightly
from ..config import settings
from ..db import get_db
from ..orm import AuditEvent, iso
from ..security import optional_user

router = APIRouter(prefix="/api/nightly", tags=["nightly"])


@router.post("/run")
def run(request: Request, x_nightly_token: str | None = Header(default=None), db: Session = Depends(get_db)):
    if x_nightly_token is not None:
        if not settings.nightly_token or not hmac.compare_digest(x_nightly_token, settings.nightly_token):
            audit.record(db, None, "access_refused", "endpoint", "POST /api/nightly/run",
                         reason="Wrong or unconfigured nightly token")
            db.commit()
            raise HTTPException(403, "Not allowed")
        actor = None
    else:
        actor = optional_user(request, db)
        if actor is None or actor.role != "ops_supervisor":
            if actor is not None:
                audit.refuse(db, actor, "nightly_run", "The operations supervisor runs the end of day by hand",
                             target_type="endpoint", target_id="POST /api/nightly/run")
            raise HTTPException(401, "Sign in as the operations supervisor, or send the scheduler's token")
    out = nightly.run(db, actor)
    db.commit()
    return out


@router.get("")
def recent(request: Request, db: Session = Depends(get_db)):
    actor = optional_user(request, db)
    if actor is None or actor.role not in ("ops_supervisor", "ops_analyst"):
        raise HTTPException(403, "Not allowed")
    rows = db.scalars(select(AuditEvent).where(AuditEvent.action == "nightly_run")
                      .order_by(AuditEvent.id.desc()).limit(10)).all()
    return {"scheduled": bool(settings.nightly_token), "can_run": actor.role == "ops_supervisor",
            "runs": [{"at": iso(e.at), "on": e.target_id, **e.detail} for e in rows]}
