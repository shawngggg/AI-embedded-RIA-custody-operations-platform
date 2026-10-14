"""Restriction codes: place, view, and remove. Only the owning function removes a code."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from restrictions import CATALOG

from .. import audit
from ..db import get_db
from ..access import require_capability
from ..orm import Account, Firm, Restriction, User, iso, utcnow
from ..security import require_internal
from ..views import restriction_dict

router = APIRouter(prefix="/api/restrictions", tags=["restrictions"])

ROLE_FUNCTION = {"ops_analyst": "operations", "ops_supervisor": "operations", "aml_compliance": "aml_compliance",
                 "sanctions": "sanctions", "periodic_review": "periodic_review", "ria_service": "ria_service",
                 "ria_service_lead": "ria_service"}
MANUAL_CODES = ["CIPV", "EDDP", "KYCR", "SANC"]


@router.get("")
def list_restrictions(active: bool | None = True, target: str | None = None,
                      user: User = Depends(require_capability("restricted_accounts")), db: Session = Depends(get_db)):
    """Restriction codes with their owners and reasons: internal. The portal sees its accounts' limits in plain
    words on each request and on the portal home."""
    q = select(Restriction).order_by(Restriction.placed_at.desc())
    if active is not None:
        q = q.where(Restriction.active.is_(active))
    if target:
        q = q.where(Restriction.target_id == target)
    return [restriction_dict(r) for r in db.scalars(q).all()]


@router.get("/accounts")
def restricted_accounts(user: User = Depends(require_capability("restricted_accounts")), db: Session = Depends(get_db)):
    """Accounts and firms with at least one active code: what's blocked, by whom, and since when."""
    rows = db.scalars(select(Restriction).where(Restriction.active.is_(True)).order_by(Restriction.placed_at)).all()
    by_target: dict[str, list[Restriction]] = {}
    for r in rows:
        by_target.setdefault(r.target_id, []).append(r)
    out = []
    for target, codes in by_target.items():
        item = {"target_id": target, "kind": "account", "name": target, "firm": None, "status": None, "group": None}
        if target.startswith("FIRM-"):
            firm = db.scalar(select(Firm).where(Firm.crd == target[5:]))
            item.update(kind="firm", name=firm.legal_name if firm else target, status=firm.status if firm else None)
        else:
            account = db.get(Account, target)
            if account:
                firm = db.get(Firm, account.firm_id)
                item.update(name=account.display_name, firm=firm.legal_name if firm else None, status=account.status,
                            group="Self-directed" if account.group == "self_directed" else "Pure RIA")
        blocks = sorted({a.value for r in codes for a in CATALOG[r.code].blocks})
        case_ids = sorted({r.case_id for r in codes if r.case_id})
        out.append({**item, "codes": [restriction_dict(r) for r in codes], "blocks": blocks,
                    "owners": sorted({r.owner for r in codes}), "since": iso(codes[0].placed_at),
                    "case_ids": case_ids})
    out.sort(key=lambda x: x["since"], reverse=True)
    return out


@router.get("/catalog")
def catalog(user: User = Depends(require_capability("restricted_accounts"))):
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
    if r.code == "RSCN":
        raise HTTPException(409, "RSCN comes off through the rescission decision: the team lead approves or rejects it")
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
