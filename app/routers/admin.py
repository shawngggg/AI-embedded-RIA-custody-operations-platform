"""
User and entitlement administration, the rule library, and the demo reset.

Platform administrators add team members and assign roles. An RIA
administrator adds its own firm's users, never beyond the RIA's own
permissions. A grant of a decision role waits for a second platform
administrator.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from account_rules import ACCOUNT_RULES
from firm_rules import FIRM_RULES, RELIANCE_RELIEF
from flagged_list import SANCTIONED_JURISDICTIONS
from risk import ACCOUNT_FACTORS, FATF_LISTS, FIRM_FACTORS
from transferability import XFER_RULES
from account_changes import CHANGE_RULES

from .. import audit
from ..config import settings
from ..db import SessionLocal, get_db
from ..orm import DECISION_ROLES, ROLES, Account, Firm, RoleGrant, User, utcnow
from ..security import current_user, hash_password, require_roles
from ..views import iso, user_dict

router = APIRouter(prefix="/api/admin", tags=["admin"])
admins = require_roles("platform_admin", "ria_admin")
RIA_ADMIN_MAY_GRANT = {"ria_user", "ria_admin", "client"}


def _scope(db: Session, actor: User, target: User | None = None, role: str | None = None,
           firm_id: int | None = None) -> None:
    if actor.role == "platform_admin":
        return
    if role and role not in RIA_ADMIN_MAY_GRANT:
        raise HTTPException(403, "An RIA administrator can grant only RIA user, RIA administrator, or client access")
    if target is not None and target.firm_id != actor.firm_id and target.role != "client":
        raise HTTPException(403, "Only your firm's users")
    if firm_id is not None and firm_id != actor.firm_id:
        raise HTTPException(403, "Only your firm")


def _grant_dict(db: Session, g: RoleGrant) -> dict:
    u, by = db.get(User, g.user_id), db.get(User, g.requested_by)
    dec = db.get(User, g.decided_by) if g.decided_by else None
    return {"id": g.id, "user": u.display_name, "username": u.username, "role": g.role,
            "role_label": ROLES[g.role], "status": g.status, "requested_by": by.display_name,
            "requested_by_id": g.requested_by, "decided_by": dec.display_name if dec else None,
            "created_at": iso(g.created_at), "decided_at": iso(g.decided_at)}


@router.get("/roles")
def roles(user: User = Depends(admins)):
    allowed = ROLES if user.role == "platform_admin" else {k: v for k, v in ROLES.items() if k in RIA_ADMIN_MAY_GRANT}
    return [{"role": k, "label": v, "decision_role": k in DECISION_ROLES} for k, v in allowed.items()]


@router.get("/users")
def users(user: User = Depends(admins), db: Session = Depends(get_db)):
    q = select(User).order_by(User.id)
    if user.role == "ria_admin":
        q = q.where(User.firm_id == user.firm_id)
    return [user_dict(db, u) for u in db.scalars(q).all()]


class NewUser(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[a-z0-9._-]+$")
    display_name: str = Field(min_length=2)
    email: str | None = None
    role: str
    password: str = Field(min_length=10)
    firm_crd: str | None = None
    client_account_id: str | None = None


@router.post("/users")
def create_user(body: NewUser, actor: User = Depends(admins), db: Session = Depends(get_db)):
    if body.role not in ROLES:
        raise HTTPException(422, "Unknown role")
    if db.scalar(select(User).where(User.username == body.username)):
        raise HTTPException(409, "That username is taken")
    firm_id = None
    if body.role in ("ria_user", "ria_admin"):
        firm = db.scalar(select(Firm).where(Firm.crd == body.firm_crd)) if body.firm_crd else (
            db.get(Firm, actor.firm_id) if actor.firm_id else None)
        if firm is None or firm.status != "ACTIVE":
            raise HTTPException(422, "RIA users need an active firm (CRD)")
        firm_id = firm.id
    if body.role == "client":
        account = db.get(Account, body.client_account_id) if body.client_account_id else None
        if account is None:
            raise HTTPException(422, "Client access needs the client's account id")
        if account.group != "self_directed":
            raise HTTPException(422, "Clients sign in to their self-directed account")
        if actor.role == "ria_admin" and account.firm_id != actor.firm_id:
            raise HTTPException(403, "Only accounts under your firm")
    _scope(db, actor, role=body.role, firm_id=firm_id if body.role in ("ria_user", "ria_admin") else None)
    decision = body.role in DECISION_ROLES
    u = User(username=body.username, display_name=body.display_name, email=body.email, role=body.role,
             password_hash=hash_password(body.password), firm_id=firm_id, client_account_id=body.client_account_id,
             active=not decision)
    db.add(u)
    db.flush()
    grant = None
    if decision:
        grant = RoleGrant(user_id=u.id, role=body.role, requested_by=actor.id)
        db.add(grant)
        db.flush()
    audit.record(db, actor, "user_created", "user", u.id, username=u.username, role=u.role,
                 waiting_for_approval=decision or None)
    db.commit()
    return {"user": user_dict(db, u), "grant": _grant_dict(db, grant) if grant else None}


class RoleChange(BaseModel):
    role: str


@router.post("/users/{user_id}/role")
def change_role(user_id: int, body: RoleChange, actor: User = Depends(admins), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if target is None or body.role not in ROLES:
        raise HTTPException(404, "No such user or role")
    _scope(db, actor, target=target, role=body.role)
    if target.id == actor.id:
        raise HTTPException(403, "You can't change your own role")
    if body.role in DECISION_ROLES:
        grant = RoleGrant(user_id=target.id, role=body.role, requested_by=actor.id)
        db.add(grant)
        db.flush()
        audit.record(db, actor, "role_grant_requested", "user", target.id, role=body.role)
        db.commit()
        return {"user": user_dict(db, target), "grant": _grant_dict(db, grant)}
    old = target.role
    target.role = body.role
    audit.record(db, actor, "role_changed", "user", target.id, old_role=old, new_role=body.role)
    db.commit()
    return {"user": user_dict(db, target), "grant": None}


@router.post("/users/{user_id}/deactivate")
def deactivate(user_id: int, actor: User = Depends(admins), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "No such user")
    _scope(db, actor, target=target)
    if target.id == actor.id:
        raise HTTPException(403, "You can't deactivate yourself")
    target.active = False
    audit.record(db, actor, "user_deactivated", "user", target.id)
    db.commit()
    return user_dict(db, target)


@router.get("/grants")
def grants(status: str = "pending", actor: User = Depends(require_roles("platform_admin")),
           db: Session = Depends(get_db)):
    rows = db.scalars(select(RoleGrant).where(RoleGrant.status == status).order_by(RoleGrant.created_at)).all()
    return [{**_grant_dict(db, g), "can_approve": g.requested_by != actor.id} for g in rows]


def _decide_grant(grant_id: int, approve: bool, actor: User, db: Session):
    g = db.get(RoleGrant, grant_id)
    if g is None or g.status != "pending":
        raise HTTPException(404, "No pending grant with that id")
    if g.requested_by == actor.id:
        audit.record(db, actor, "role_grant_refused", "user", g.user_id, role=g.role,
                     reason="requester cannot approve")
        db.commit()
        raise HTTPException(403, "A second administrator must approve a grant you requested")
    g.status, g.decided_by, g.decided_at = ("approved" if approve else "rejected"), actor.id, utcnow()
    target = db.get(User, g.user_id)
    if approve:
        target.role, target.active = g.role, True
    audit.record(db, actor, "role_grant_approved" if approve else "role_grant_rejected", "user", target.id,
                 role=g.role)
    db.commit()
    return _grant_dict(db, g)


@router.post("/grants/{grant_id}/approve")
def approve_grant(grant_id: int, actor: User = Depends(require_roles("platform_admin")),
                  db: Session = Depends(get_db)):
    return _decide_grant(grant_id, True, actor, db)


@router.post("/grants/{grant_id}/reject")
def reject_grant(grant_id: int, actor: User = Depends(require_roles("platform_admin")),
                 db: Session = Depends(get_db)):
    return _decide_grant(grant_id, False, actor, db)


@router.post("/reset")
def reset_demo(actor: User = Depends(require_roles("platform_admin")), db: Session = Depends(get_db)):
    if not settings.demo_mode:
        raise HTTPException(403, "Reset is only available in demo mode")
    from ..seed import reset_database, seed
    # End this request's own read transaction first: on Postgres, DROP TABLE waits on any open
    # transaction that has touched the table, including the one that loaded the signed-in admin.
    db.rollback()
    db.close()
    reset_database()
    with SessionLocal() as fresh:
        seed(fresh)
    return {"ok": True}


def _rule(r: dict) -> dict:
    return {"rule_id": r["rule_id"], "version": r["version"], "effective_date": r["effective_date"],
            "expiry_date": r.get("expiry_date"), "source": r.get("source", ""),
            "reason": r.get("reason") or r.get("description", ""),
            "disposition": r["disposition"].value if "disposition" in r else None, "points": r.get("points")}


@router.get("/rules", dependencies=[Depends(current_user)])
def rule_library():
    return {
        "Firm rules": [_rule(r) for r in FIRM_RULES],
        "Account rules": [_rule(r) for r in ACCOUNT_RULES],
        "Account change rules": [_rule(r) for r in CHANGE_RULES],
        "Transferability rules": [_rule(r) for r in XFER_RULES],
        "Account risk factors": [_rule(r) for r in ACCOUNT_FACTORS],
        "Firm risk factors": [_rule(r) for r in FIRM_FACTORS],
        "Reliance relief": [_rule({**r, "reason": "SEC staff reliance relief in force"}) for r in RELIANCE_RELIEF],
        "Sanctioned jurisdictions": [_rule({**r, "reason": ", ".join(r["countries"] + r["regions"])})
                                     for r in SANCTIONED_JURISDICTIONS],
        "FATF lists": [_rule({**r, "reason": "Call for action: " + ", ".join(r["call_for_action"])}) for r in FATF_LISTS],
    }
