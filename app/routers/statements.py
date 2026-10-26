"""Quarterly statements (app/statements.py): operations sees every account's, an RIA its firm's, a client their own."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, statements
from ..access import capabilities
from ..db import get_db
from ..orm import Account, Statement, User
from ..security import current_user, require_roles

router = APIRouter(prefix="/api/statements", tags=["statements"])


def _may_see(user: User, account: Account | None) -> bool:
    if account is None:
        return False
    if user.role in ("ria_admin", "ria_user"):
        return account.firm_id == user.firm_id
    if user.role == "client":
        return account.id == user.client_account_id
    return bool(capabilities(user).get("cases"))


@router.get("")
def list_statements(account_id: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(Statement).order_by(Statement.period_end.desc(), Statement.account_id)
    if account_id:
        q = q.where(Statement.account_id == account_id)
    rows = [s for s in db.scalars(q).all() if _may_see(user, db.get(Account, s.account_id))]
    return [statements.to_dict(s) for s in rows]


@router.get("/{statement_id}/file")
def statement_file(statement_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = db.get(Statement, statement_id)
    if s is None or not _may_see(user, db.get(Account, s.account_id)):
        raise HTTPException(404, "No such statement")
    audit.record(db, user, "statement_opened", "statement", s.id, account=s.account_id)
    db.commit()
    return Response(s.content, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{s.filename}"'})


class Generate(BaseModel):
    period_end: str | None = None


@router.post("/generate")
def generate(body: Generate = Generate(), user: User = Depends(require_roles("ops_supervisor")),
             db: Session = Depends(get_db)):
    """The last quarter's statements by hand (the nightly batch also makes them once the quarter ends)."""
    from datetime import date
    from custody.calendar import quarter_bounds
    period = quarter_bounds(date.fromisoformat(body.period_end)) if body.period_end else None
    out = statements.generate(db, user, period)
    db.commit()
    return out
