"""The RIA and client portal: accounts, new account applications, and account changes."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from account_changes import ChangeType
from models import AccountGroup

from ..db import get_db
from ..orm import Account, Firm, User
from ..runner import run_case
from ..security import current_user
from ..seed import create_account_case, create_change_case
from ..serialize import account_from
from ..views import case_detail

router = APIRouter(prefix="/api/portal", tags=["portal"])


def _account_dict(db: Session, a: Account) -> dict:
    firm = db.get(Firm, a.firm_id)
    holders = a.application.get("holders", [])
    return {"id": a.id, "display_name": a.display_name, "group": a.group, "registration_type": a.registration_type,
            "status": a.status, "path": a.path, "risk_tier": a.risk_tier, "firm": firm.legal_name,
            "firm_crd": firm.crd, "address": holders[0].get("address") if holders else None,
            "bank_instructions": a.application.get("bank_instructions", []),
            "beneficiaries": a.application.get("beneficiaries"), "change_history": a.change_history or []}


@router.get("/accounts")
def accounts(user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(Account).order_by(Account.id)
    if user.role in ("ria_admin", "ria_user"):
        q = q.where(Account.firm_id == user.firm_id)
    elif user.role == "client":
        q = q.where(Account.id == user.client_account_id)
    return [_account_dict(db, a) for a in db.scalars(q).all()]


class NewAccount(BaseModel):
    application: dict
    firm_crd: str | None = None      # internal users name the firm; RIA users use their own


@router.post("/accounts")
def new_account(body: NewAccount, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.role in ("ria_admin", "ria_user"):
        firm = db.get(Firm, user.firm_id)
    elif user.role in ("ops_analyst", "ops_supervisor"):
        firm = db.scalar(select(Firm).where(Firm.crd == body.firm_crd))
    else:
        raise HTTPException(403, "RIA users and operations submit account applications")
    if firm is None:
        raise HTTPException(422, "Unknown firm")
    data = dict(body.application)
    data.setdefault("application_id", f"APP-{uuid.uuid4().hex[:6].upper()}")
    data.setdefault("account_id", f"ACC-{uuid.uuid4().hex[:6].upper()}")
    if db.get(Account, data["account_id"]):
        raise HTTPException(409, "That account id is taken")
    try:
        app = account_from(data)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(422, f"Application not readable: {exc}")
    if not app.holders:
        raise HTTPException(422, "Name at least one account holder")
    case = create_account_case(db, app, firm, user)
    run_case(db, case, user)
    db.commit()
    return case_detail(db, case, user)


class NewChange(BaseModel):
    account_id: str
    change_type: str
    details: dict = Field(default_factory=dict)
    client_signature: bool = False
    medallion_guarantee: bool = False
    new_parties: list[dict] = Field(default_factory=list)
    new_bank: dict | None = None


@router.post("/changes")
def new_change(body: NewChange, user: User = Depends(current_user), db: Session = Depends(get_db)):
    account = db.get(Account, body.account_id)
    if account is None or account.status != "OPEN":
        raise HTTPException(404, "No open account with that id")
    if user.role in ("ria_admin", "ria_user") and account.firm_id != user.firm_id:
        raise HTTPException(404, "No open account with that id")
    if user.role == "client" and account.id != user.client_account_id:
        raise HTTPException(404, "No open account with that id")
    if user.role not in ("ria_admin", "ria_user", "client", "ops_analyst"):
        raise HTTPException(403, "RIA users, the client, or operations submit changes")
    try:
        ChangeType(body.change_type)
    except ValueError:
        raise HTTPException(422, "Unknown change type")
    firm = db.get(Firm, account.firm_id)
    change = {
        "request_id": f"CHG-{uuid.uuid4().hex[:6].upper()}", "account_id": account.id,
        "account_group": account.group, "account_firm": firm.crd, "change_type": body.change_type,
        "submitted_by_role": user.role,
        "submitted_by_firm": db.get(Firm, user.firm_id).crd if user.firm_id else None,
        "submitted_by_client_of": user.client_account_id, "details": body.details,
        "client_signature": body.client_signature, "medallion_guarantee": body.medallion_guarantee,
        "new_parties": body.new_parties, "new_bank": body.new_bank,
        "recent_changes": account.change_history or [], "path": account.path or "B",
    }
    case = create_change_case(db, change, account, user)
    run_case(db, case, user)
    db.commit()
    return case_detail(db, case, user)


@router.get("/change-types")
def change_types(user: User = Depends(current_user)):
    from account_changes import LABELS, OWNER_LEVEL
    return [{"value": t.value, "label": LABELS[t].capitalize(), "owner_level": t in OWNER_LEVEL} for t in ChangeType]


@router.get("/groups")
def groups():
    return [g.value for g in AccountGroup]
