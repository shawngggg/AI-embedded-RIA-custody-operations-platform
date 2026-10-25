"""
Ongoing screening: the flagged list in force, the updates the sanctions team adds, rescreening runs, and the open
alerts (app/screening.py). The sanctions team adds entries and runs a rescreen; AML compliance and the operations
supervisor can read it.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, screening
from ..access import require_capability
from ..db import get_db
from ..orm import AuditEvent, Case, FlaggedListEntry, User, iso

router = APIRouter(prefix="/api/screening", tags=["screening"])


class NewEntry(BaseModel):
    kind: str = Field(pattern="^(individual|entity|institution)$")
    name: str = Field(min_length=3, max_length=200)
    aliases: list[str] = Field(default_factory=list)
    date_of_birth: str | None = Field(default=None, max_length=10)
    country: str | None = Field(default=None, max_length=60)
    program: str | None = Field(default=None, max_length=120)
    identifiers: dict[str, str] = Field(default_factory=dict)


def _acts(user: User) -> bool:
    return user.role == "sanctions"


def _view(db: Session, user: User) -> dict:
    added = {e.entry_id: e for e in db.scalars(select(FlaggedListEntry)).all()}
    entries = [{"entry_id": e.entry_id, "kind": e.kind.value, "name": e.name, "aliases": list(e.aliases),
                "date_of_birth": e.date_of_birth, "country": e.country, "program": e.program,
                "identifiers": e.identifiers, "added": e.entry_id in added,
                "added_by": added[e.entry_id].added_by if e.entry_id in added else None,
                "added_at": iso(added[e.entry_id].added_at) if e.entry_id in added else None}
               for e in screening.current_list(db)]
    runs = [{"at": iso(e.at), "by": e.actor_name, **{k: e.detail.get(k) for k in
             ("trigger", "parties", "targets", "list_size", "alerts")}}
            for e in db.scalars(select(AuditEvent).where(AuditEvent.action == "rescreen_run")
                                .order_by(AuditEvent.id.desc()).limit(10)).all()]
    alerts = [{"id": c.id, "title": c.title, "status": c.status, "target_id": c.target_id,
               "trigger": (c.application or {}).get("trigger"), "created_at": iso(c.created_at),
               "hits": [f"{h['subject']} ~ {h['matched']}" for h in (c.application or {}).get("hits", [])]}
              for c in db.scalars(select(Case).where(Case.kind == "screening").order_by(Case.id.desc())).all()]
    return {"entries": entries, "runs": runs, "alerts": alerts, "can_act": _acts(user),
            "population": len(screening.population(db).parties)}


@router.get("")
def view(user: User = Depends(require_capability("screening")), db: Session = Depends(get_db)):
    return _view(db, user)


@router.post("/entries")
def add(body: NewEntry, user: User = Depends(require_capability("screening")), db: Session = Depends(get_db)):
    if not _acts(user):
        audit.refuse(db, user, "flagged_entry_added", "Only the sanctions team updates the flagged list",
                     target_type="screening", target_id="entries")
    _entry, run = screening.add_entry(db, user, body.model_dump())
    db.commit()
    return {"run": run, **_view(db, user)}


@router.post("/run")
def rescreen(user: User = Depends(require_capability("screening")), db: Session = Depends(get_db)):
    if not _acts(user):
        audit.refuse(db, user, "rescreen_run", "Only the sanctions team runs a rescreen by hand",
                     target_type="screening", target_id="run")
    run = screening.run(db, user, "nightly")
    db.commit()
    return {"run": run, **_view(db, user)}
