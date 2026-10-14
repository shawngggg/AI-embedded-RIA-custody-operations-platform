"""The RIA and client portal: accounts, new account applications, and account changes."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from account_changes import ChangeType
from models import AccountGroup

from .. import audit
from ..db import get_db
from ..orm import Account, Firm, User
from ..runner import run_case
from ..security import current_user
from ..documents import FIELD_DOCS, attach_at_intake
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


ATTENTION = {"PENDING_ITEMS": "Items requested from you", "RETURNED": "Returned for correction",
             "OPEN_RESTRICTED": "Open, restricted until items arrive"}
CLOSED_STATES = {"ACTIVE", "OPEN", "APPLIED", "DECLINED", "BLOCKED", "REJECTED", "REDIRECTED"}


@router.get("/home")
def home(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """The portal's landing page: the firm's (or client's) relationship with the custodian, and what needs them."""
    from ..orm import Case, Restriction
    from ..views import iso
    if user.role not in ("ria_admin", "ria_user", "client"):
        raise HTTPException(404, "The portal home is for RIA users and clients")
    if user.role == "client":
        accounts = [a for a in [db.get(Account, user.client_account_id)] if a]
        cases = db.scalars(select(Case).where(Case.account_id == user.client_account_id, Case.kind == "change")).all()
    else:
        accounts = db.scalars(select(Account).where(Account.firm_id == user.firm_id)).all()
        cases = db.scalars(select(Case).where(Case.firm_id == user.firm_id, Case.kind.in_(("account", "change")))).all()
    firm = db.get(Firm, user.firm_id) if user.firm_id else (db.get(Firm, accounts[0].firm_id) if accounts else None)
    restricted = {r.target_id for r in db.scalars(select(Restriction).where(
        Restriction.active.is_(True), Restriction.target_id.in_([a.id for a in accounts]))).all()}
    attention = [{"case_id": c.id, "title": c.title, "status": c.status, "why": ATTENTION[c.status],
                  "items": (c.result or {}).get("requested_items") or [], "since": iso(c.queue_entered_at or c.updated_at)}
                 for c in sorted(cases, key=lambda c: c.id) if c.status in ATTENTION]
    lead = db.scalar(select(User).where(User.role == "ria_service_lead", User.active.is_(True)).order_by(User.id))
    return {
        "name": user.display_name.split()[0], "role": user.role,
        "firm": {"name": firm.legal_name, "crd": firm.crd, "status": firm.status, "since": iso(firm.activated_at),
                 "reliance_eligible": firm.reliance_eligible} if firm else None,
        "accounts": {"total": len(accounts), "open": sum(a.status == "OPEN" for a in accounts),
                     "pending": sum(a.status == "PENDING" for a in accounts), "restricted": len(restricted)},
        "requests_open": sum(c.status not in CLOSED_STATES for c in cases),
        "attention": attention,
        "contact": {"team": "RIA service team", "name": lead.display_name if lead else None,
                    "email": "riaservice@custodian.example", "hours": "Business days, 7 a.m. to 7 p.m. Eastern"},
    }


class NewAccount(BaseModel):
    application: dict
    firm_crd: str | None = None      # internal users name the firm; RIA users use their own
    # [{doc_type, sample: true} or {doc_type, filename, content_b64}]: the signed documents and tax form
    documents: list[dict] = Field(default_factory=list)


def _apply_document_fields(db: Session, case, user: User, items: list[dict], channel: str) -> None:
    """Attach intake documents and set the fields they back; the rest of the application is unchanged."""
    fields = attach_at_intake(db, case, user, items, channel)
    if fields:
        case.application = {**case.application, **fields}
        account = db.get(Account, case.account_id) if case.account_id else None
        if account is not None and case.kind == "account":
            account.application = {**account.application, **fields}


@router.post("/accounts")
def new_account(body: NewAccount, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.role in ("ria_admin", "ria_user"):
        firm = db.get(Firm, user.firm_id)
    elif user.role in ("ops_analyst", "ops_supervisor"):
        if user.team != "onboarding":
            audit.refuse(db, user, "account_application", "The onboarding team takes in new applications",
                         target_type="firm", target_id=body.firm_crd or "-")
        firm = db.scalar(select(Firm).where(Firm.crd == body.firm_crd))
    else:
        audit.refuse(db, user, "account_application", "RIA users and operations submit account applications",
                     target_type="firm", target_id="-")
    if firm is None:
        raise HTTPException(422, "Unknown firm")
    data = dict(body.application)
    # Signed documents and the tax form count only when the document itself is attached
    for f in FIELD_DOCS["account"]:
        data[f] = None if f == "tax_form" else False
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
    _apply_document_fields(db, case, user, body.documents, "portal" if user.role.startswith("ria") else "mail")
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
    documents: list[dict] = Field(default_factory=list)   # client-signed change form, medallion guarantee


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
        audit.refuse(db, user, "account_change", "RIA users, the client, or operations submit changes",
                     target_type="account", target_id=account.id)
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
        # The client signs their own request in the portal; anyone else attaches the client-signed form
        "client_signature": body.client_signature if user.role == "client" else False,
        "medallion_guarantee": False,
        "new_parties": body.new_parties, "new_bank": body.new_bank,
        "recent_changes": account.change_history or [], "path": account.path or "B",
    }
    case = create_change_case(db, change, account, user)
    _apply_document_fields(db, case, user, body.documents, "mail" if user.role == "ops_analyst" else "portal")
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
