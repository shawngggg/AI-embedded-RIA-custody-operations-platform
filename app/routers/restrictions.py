"""Restriction codes: place, view, and remove. Only the owning function removes a code."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from restrictions import CATALOG

from .. import audit
from ..db import get_db
from ..orm import Restriction, User, utcnow
from ..security import current_user, require_internal
from ..views import restriction_dict

router = APIRouter(prefix="/api/restrictions", tags=["restrictions"])

ROLE_FUNCTION = {"ops_analyst": "operations", "ops_supervisor": "operations", "aml_compliance": "aml_compliance",
                 "sanctions": "sanctions", "periodic_review": "periodic_review"}
MANUAL_CODES = ["CIPV", "EDDP", "KYCR", "SANC"]


@router.get("")
def list_restrictions(active: bool | None = True, target: str | None = None,
                      user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(Restriction).order_by(Restriction.placed_at.desc())
    if active is not None:
        q = q.where(Restriction.active.is_(active))
    if target:
        q = q.where(Restriction.target_id == target)
    if user.role in ("ria_admin", "ria_user", "client"):
        from ..orm import Account
        ids = [a.id for a in db.scalars(select(Account).where(
            Account.firm_id == user.firm_id if user.role != "client" else Account.id == user.client_account_id)).all()]
        q = q.where(Restriction.target_id.in_(ids))
    return [restriction_dict(r) for r in db.scalars(q).all()]


@router.get("/catalog")
def catalog(user: User = Depends(current_user)):
    return [{"code": c.code, "description": c.description, "owner": c.owner.value,
             "blocks": sorted(a.value for a in c.blocks), "requires_authorization": c.requires_authorization,
             "source": c.source, "manual": c.code in MANUAL_CODES} for c in CATALOG.values()]


class Place(BaseModel):
    target_id: str
    code: str
    reason: str = Field(min_length=3)


@router.post("")
def place(body: Place, user: User = Depends(require_internal), db: Session = Depends(get_db)):
    if body.code not in MANUAL_CODES:
        raise HTTPException(422, f"{body.code} can't be placed by hand here")
    r = Restriction(target_id=body.target_id, code=body.code, reason=body.reason, owner=CATALOG[body.code].owner.value,
                    source="manual", placed_by=user.display_name)
    db.add(r)
    db.flush()
    audit.record(db, user, "restriction_placed", "restriction", r.id, code=r.code, target=r.target_id,
                 owner=r.owner, reason=r.reason, source="manual")
    db.commit()
    return restriction_dict(r)


class Remove(BaseModel):
    reason: str = Field(min_length=3)
    authorization: str | None = None


@router.post("/{restriction_id}/remove")
def remove(restriction_id: int, body: Remove, user: User = Depends(require_internal), db: Session = Depends(get_db)):
    r = db.get(Restriction, restriction_id)
    if r is None or not r.active:
        raise HTTPException(404, "No active restriction with that id")
    if r.source == "case":
        raise HTTPException(409, "This code belongs to a case; record the decision on the case instead")
    if ROLE_FUNCTION.get(user.role) != r.owner:
        audit.record(db, user, "restriction_removal_refused", "restriction", r.id, code=r.code,
                     reason=f"only {r.owner} can remove {r.code}")
        db.commit()
        raise HTTPException(403, f"Only {r.owner.replace('_', ' ')} can remove {r.code}")
    if CATALOG[r.code].requires_authorization and not (body.authorization or "").strip():
        raise HTTPException(422, f"Removing {r.code} needs an authorization reference")
    r.active, r.removed_by, r.removed_at = False, user.display_name, utcnow()
    r.removal_reason, r.authorization = body.reason, body.authorization
    audit.record(db, user, "restriction_removed", "restriction", r.id, code=r.code, target=r.target_id,
                 reason=body.reason, authorization=body.authorization)
    db.commit()
    return restriction_dict(r)
