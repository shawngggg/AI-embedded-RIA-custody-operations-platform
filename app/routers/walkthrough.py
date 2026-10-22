"""The timed reviewer walkthrough (app/walkthrough.py): start one, follow its clock, read the gate's measure."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import walkthrough
from ..db import get_db
from ..orm import Case, User
from ..security import require_internal

router = APIRouter(prefix="/api/walkthrough", tags=["walkthrough"])


class Start(BaseModel):
    first_time: bool = False


@router.post("")
def start(body: Start, user: User = Depends(require_internal), db: Session = Depends(get_db)):
    case = walkthrough.start(db, user, body.first_time)
    db.commit()
    return walkthrough.state(case)


@router.get("/summary")
def summary(user: User = Depends(require_internal), db: Session = Depends(get_db)):
    return walkthrough.summary(db)


@router.get("/{case_id}")
def state(case_id: int, user: User = Depends(require_internal), db: Session = Depends(get_db)):
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(404, "No such case")
    return walkthrough.state(case)
