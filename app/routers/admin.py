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
from ..orm import (DECISION_ROLES, LICENSED_ROLES, OPS_ROLES, OPS_TEAMS, NEEDS_SERIES_7, REGISTRATIONS, ROLES, SHORT_REGISTRATION,
                   Account, Firm, RoleGrant, User, utcnow)
from ..security import hash_password, require_internal, require_roles
from ..risk_register import log_access_event
from ..views import iso, user_dict

router = APIRouter(prefix="/api/admin", tags=["admin"])
admins = require_roles("platform_admin", "ria_admin")
RIA_ADMIN_MAY_GRANT = {"ria_user", "ria_admin", "client"}


def _scope(db: Session, actor: User, target: User | None = None, role: str | None = None,
           firm_id: int | None = None) -> None:
    if actor.role == "platform_admin":
        return
    if role and role not in RIA_ADMIN_MAY_GRANT:
        audit.refuse(db, actor, "role_grant", "An RIA administrator can grant only RIA user, RIA administrator, or "
                     "client access", target_type="user", target_id=target.id if target else "-", role=role)
    if target is not None and target.firm_id != actor.firm_id and target.role != "client":
        audit.refuse(db, actor, "role_grant", "Only your firm's users", target_type="user", target_id=target.id)
    if firm_id is not None and firm_id != actor.firm_id:
        audit.refuse(db, actor, "role_grant", "Only your firm", target_type="firm", target_id=firm_id)


def _grant_dict(db: Session, g: RoleGrant) -> dict:
    u, by = db.get(User, g.user_id), db.get(User, g.requested_by)
    dec = db.get(User, g.decided_by) if g.decided_by else None
    return {"id": g.id, "user": u.display_name, "username": u.username, "role": g.role,
            "role_label": ROLES[g.role], "status": g.status, "requested_by": by.display_name,
            "registrations": [SHORT_REGISTRATION.get(r, r) for r in g.registrations] if g.registrations is not None
            else None, "individual_crd": g.individual_crd,
            "requested_by_id": g.requested_by, "decided_by": dec.display_name if dec else None,
            "created_at": iso(g.created_at), "decided_at": iso(g.decided_at)}


@router.get("/roles")
def roles(user: User = Depends(admins)):
    allowed = ROLES if user.role == "platform_admin" else {k: v for k, v in ROLES.items() if k in RIA_ADMIN_MAY_GRANT}
    return [{"role": k, "label": v, "decision_role": k in DECISION_ROLES, "licensed": k in LICENSED_ROLES}
            for k, v in allowed.items()]


@router.get("/registrations")
def registrations(user: User = Depends(require_roles("platform_admin"))):
    return [{"code": k, "label": v, "needs_series_7": k in NEEDS_SERIES_7} for k, v in REGISTRATIONS.items()]


def _check_registrations(role: str, regs: list[str], crd: str | None) -> None:
    if not regs:
        if role in LICENSED_ROLES:
            raise HTTPException(422, "The RIA service team is licensed: record at least Series 7")
        return
    if role not in LICENSED_ROLES:
        raise HTTPException(422, "Only the RIA service team carries FINRA registrations; operations needs none")
    unknown = set(regs) - set(REGISTRATIONS)
    if unknown:
        raise HTTPException(422, f"Unknown registration: {', '.join(sorted(unknown))}")
    if NEEDS_SERIES_7 & set(regs) and "S7" not in regs:
        raise HTTPException(422, "A principal or options principal registration builds on Series 7")
    if not crd or not crd.isdigit():
        raise HTTPException(422, "Record the person's individual CRD number (digits)")


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
    registrations: list[str] = Field(default_factory=list)   # FINRA registrations, RIA service team only
    individual_crd: str | None = None
    team: str | None = None                                    # operations team (OPS_TEAMS), operations only


def _check_team(role: str, team: str | None) -> None:
    if role in OPS_ROLES and team not in OPS_TEAMS:
        raise HTTPException(422, "Operations users belong to a team: " + ", ".join(OPS_TEAMS.values()))
    if role not in OPS_ROLES and team:
        raise HTTPException(422, "Only operations users have a team")


@router.get("/teams")
def teams(user: User = Depends(require_roles("platform_admin"))):
    return [{"team": k, "label": v} for k, v in OPS_TEAMS.items()]


class TeamChange(BaseModel):
    team: str


@router.post("/users/{user_id}/team")
def change_team(user_id: int, body: TeamChange, actor: User = Depends(require_roles("platform_admin")),
                db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "No such user")
    _check_team(target.role, body.team)
    before, target.team = target.team, body.team
    audit.record(db, actor, "team_changed", "user", target.id, username=target.username, team=body.team,
                 previous=before)
    db.commit()
    return user_dict(db, target)


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
    _check_registrations(body.role, body.registrations, body.individual_crd)
    _check_team(body.role, body.team)
    decision = body.role in DECISION_ROLES
    u = User(username=body.username, display_name=body.display_name, email=body.email, role=body.role,
             password_hash=hash_password(body.password), firm_id=firm_id, client_account_id=body.client_account_id,
             active=not decision, team=body.team if body.role in OPS_ROLES else None)
    db.add(u)
    db.flush()
    grant = None
    if decision:
        grant = RoleGrant(user_id=u.id, role=body.role, requested_by=actor.id,
                          registrations=body.registrations if body.role in LICENSED_ROLES else None,
                          individual_crd=body.individual_crd if body.role in LICENSED_ROLES else None)
        db.add(grant)
        db.flush()
    audit.record(db, actor, "user_created", "user", u.id, username=u.username, role=u.role,
                 waiting_for_approval=decision or None, registrations=body.registrations or None)
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


class RegistrationChange(BaseModel):
    registrations: list[str]
    individual_crd: str | None = None


@router.post("/users/{user_id}/registrations")
def change_registrations(user_id: int, body: RegistrationChange, actor: User = Depends(require_roles("platform_admin")),
                         db: Session = Depends(get_db)):
    """A change to someone's registrations waits for a second administrator, like any decision-role grant."""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "No such user")
    if target.id == actor.id:
        raise HTTPException(403, "You can't change your own registrations")
    _check_registrations(target.role, body.registrations, body.individual_crd or target.individual_crd)
    grant = RoleGrant(user_id=target.id, role=target.role, requested_by=actor.id, registrations=body.registrations,
                      individual_crd=body.individual_crd or target.individual_crd)
    db.add(grant)
    db.flush()
    audit.record(db, actor, "registration_change_requested", "user", target.id, registrations=body.registrations)
    db.commit()
    return {"user": user_dict(db, target), "grant": _grant_dict(db, grant)}


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
        target = db.get(User, g.user_id)
        log_access_event(db, actor, category="Grant self-approval attempt", target_id=target.username if target else None,
                         summary=f"{actor.display_name} tried to approve a grant they requested "
                                 f"({ROLES.get(g.role, g.role)} for {target.display_name if target else 'a user'}). "
                                 "Refused: a second administrator approves every grant of a decision role.",
                         dedupe_key=f"access:grant-self:{g.id}")
        db.commit()
        raise HTTPException(403, "A second administrator must approve a grant you requested")
    g.status, g.decided_by, g.decided_at = ("approved" if approve else "rejected"), actor.id, utcnow()
    target = db.get(User, g.user_id)
    if approve:
        target.role, target.active = g.role, True
        if g.registrations is not None:
            target.registrations, target.individual_crd = list(g.registrations), g.individual_crd
        elif g.role not in LICENSED_ROLES:
            target.registrations, target.individual_crd = [], None
    audit.record(db, actor, "role_grant_approved" if approve else "role_grant_rejected", "user", target.id,
                 role=g.role, registrations=g.registrations)
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


@router.get("/rules", dependencies=[Depends(require_internal)])
def rule_library():
    """The custodian's rule library, risk factors included: internal teams only."""
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
