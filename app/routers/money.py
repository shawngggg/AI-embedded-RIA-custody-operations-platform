"""
Release 2 API: money movement and transfers (map 08), account balances from the three-layer ledger, the daily cash
settlement cycle (map 12), and position reconciliation (map 13).

Who may ask for what (MM-001): an RIA on its firm's pure RIA accounts; a client on their own self-directed account;
operations only enters what arrived another way, a client-signed request by mail or a receiving firm's ACATS request.
The engine decides everything else (codes, the flagged list, transferability, risk review, the controls gate), and
every request is a case with its own audit trail. The portal sees its own transfers in its own words, and never the
general ledger, the settlement bank, suspense, or reconciliation.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from custody import money as engine
from custody.ledger import CASH

from .. import audit, ledger, portal_view, protection, recon, settlement
from ..access import capabilities, require_capability
from ..config import settings
from ..db import get_db
from ..orm import PORTAL_ROLES, Account, BankLink, BankReceipt, Case, Firm, ReconRun, Security, SettlementRun, \
    StreetItem, User
from ..runner import run_case
from ..security import current_user
from ..portal_view import restrictions_for
from ..servicing import codes_on, new_case
from ..views import case_detail

router = APIRouter(prefix="/api", tags=["money"])

OPS_REQUESTERS = {"signed_form": "A client-signed request received by mail",
                  "receiving_firm": "A receiving firm's ACATS request (TIF)"}


# --------------------------------------------------------------------------- who sees which accounts
def _scope(db: Session, user: User):
    """The accounts this person may see: None for operations (all), else a set of ids."""
    if user.role in ("ria_admin", "ria_user"):
        return {a.id for a in db.scalars(select(Account).where(Account.firm_id == user.firm_id)).all()}
    if user.role == "client":
        return {user.client_account_id} if user.client_account_id else set()
    caps = capabilities(user)
    if caps.get("transfers") or caps.get("settlement"):
        return None
    audit.refuse(db, user, "access", "Money movement isn't part of your role", target_type="endpoint",
                 target_id="/api/money")


def _account_for(db: Session, user: User, account_id: str) -> Account:
    scope = _scope(db, user)
    a = db.get(Account, account_id)
    if a is None or (scope is not None and a.id not in scope):
        audit.refuse(db, user, "access", "That account isn't one of yours", status=404, target_type="account",
                     target_id=account_id)
    return a


def _can_work(user: User, team: str) -> bool:
    return user.role == "ops_supervisor" or (user.role == "ops_analyst" and user.team == team)


# --------------------------------------------------------------------------- transfers
def transfer_row(db: Session, c: Case, portal: bool) -> dict:
    req = c.application or {}
    summary = (c.result or {}).get("summary") or {}
    acct = db.get(Account, c.account_id) if c.account_id else None
    row = {"id": c.id, "type": req.get("type"), "label": engine.TYPES.get(req.get("type"), {}).get("label"),
           "account": c.account_id, "client": acct.display_name if acct else None,
           "amount": float(req.get("amount") or 0) or None,
           "positions": len(req.get("positions") or []), "rail": engine.TYPES.get(req.get("type"), {}).get("rail"),
           "counterparty": summary.get("Counterparty"), "created_at": c.created_at.isoformat(),
           "submitted": summary.get("Submitted"), "expected": summary.get("Expected to settle"),
           "requested_by": summary.get("Requested by")}
    if portal:
        row["status"] = portal_view.status(c.status, "transfer")
    else:
        row.update({"status": c.status, "queue": c.queue, "firm": db.get(Firm, c.firm_id).legal_name if c.firm_id
                    else None, "exception": summary.get("Exception")})
    return row


@router.get("/money/transfers")
def list_transfers(account: str | None = None, open_only: bool = False, limit: int = 200,
                   user: User = Depends(current_user), db: Session = Depends(get_db)):
    scope = _scope(db, user)
    q = select(Case).where(Case.kind == "transfer").order_by(Case.id.desc())
    if scope is not None:
        q = q.where(Case.account_id.in_(scope or {"-"}))
    if account:
        q = q.where(Case.account_id == account)
    if open_only:
        q = q.where(Case.closed_at.is_(None))
    portal = user.role in PORTAL_ROLES
    return [transfer_row(db, c, portal) for c in db.scalars(q.limit(min(limit, 500))).all()]


class Counterparty(BaseModel):
    owner_name: str | None = Field(default=None, max_length=200)
    owner_kind: str | None = Field(default=None, pattern="^(individual|entity)$")
    institution: str | None = Field(default=None, max_length=200)
    account_last4: str | None = Field(default=None, max_length=4)
    bic: str | None = Field(default=None, max_length=11)
    country: str | None = Field(default=None, max_length=60)


class PositionIn(BaseModel):
    security_id: str
    quantity: float = Field(gt=0)


class TransferRequest(BaseModel):
    type: str
    account_id: str
    amount: float | None = Field(default=None, ge=0)
    bank_link_id: str | None = None
    destination: str | None = None
    positions: list[PositionIn] = Field(default_factory=list)
    cash: float | None = Field(default=None, ge=0)
    counterparty: Counterparty | None = None
    requester: str | None = None                  # operations only: signed_form | receiving_firm
    sloa_id: str | None = Field(default=None, max_length=20)   # an RIA payment to a third party under the client's SLOA
    client_signed: bool = False
    title_on_form: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=500)
    idempotency_key: str | None = Field(default=None, max_length=80)


@router.post("/money/transfers")
def request_transfer(body: TransferRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.type not in engine.TYPES:
        raise HTTPException(422, f"Unknown request type; one of {', '.join(engine.TYPES)}")
    a = _account_for(db, user, body.account_id)
    if user.role in ("ria_admin", "ria_user"):
        role = "ria"
    elif user.role == "client":
        role = "client"
    else:
        if not _can_work(user, "asset_transfer"):
            audit.refuse(db, user, "transfer_request", "Only the asset transfer team enters requests that arrive by "
                         "mail or from a receiving firm", target_type="account", target_id=a.id)
        if body.requester not in OPS_REQUESTERS:
            raise HTTPException(422, "Operations doesn't originate money movement: enter a client-signed request "
                                "(signed_form) or a receiving firm's ACATS request (receiving_firm)")
        role = body.requester
    if body.idempotency_key:                     # API-2: a retry never creates a second transfer
        for c in db.scalars(select(Case).where(Case.kind == "transfer", Case.account_id == a.id)).all():
            if (c.application or {}).get("idempotency_key") == body.idempotency_key:
                return {"created": False, "case": case_detail(db, c, user)}
    spec = engine.TYPES[body.type]
    req: dict = {"type": body.type, "requester": {"role": role, "user": user.display_name}}
    if spec["cash"] and body.type != "journal" or (body.type == "journal" and not body.positions):
        if not body.amount:
            raise HTTPException(422, "Give an amount")
        req["amount"] = round(body.amount, 2)
    if body.type in ("ach_deposit", "ach_withdrawal"):
        link = db.get(BankLink, body.bank_link_id) if body.bank_link_id else db.scalar(
            select(BankLink).where(BankLink.account_id == a.id, BankLink.status == "verified"))
        if link is None or link.account_id != a.id:
            raise HTTPException(422, "Use a bank link on this account")
        req["bank_link_id"] = link.id
        req["counterparty"] = {"owner_name": link.owner, "institution": link.bank, "account_last4": link.last4}
    if body.type == "journal":
        dest = db.get(Account, body.destination or "")
        if dest is None or dest.status != "OPEN" or dest.id == a.id:
            raise HTTPException(422, "Journal to another open account")
        req["destination"] = dest.id
    if body.counterparty and body.type not in ("ach_deposit", "ach_withdrawal"):
        req["counterparty"] = body.counterparty.model_dump(exclude_none=True)
    if body.sloa_id and body.type == "wire_out":
        # MM-011: the payee comes from the client's SLOA as the client gave it; what the RIA typed doesn't count
        s = next((x for x in (a.application or {}).get("sloas") or [] if x.get("id") == body.sloa_id), None)
        if s is None:
            raise HTTPException(422, "No standing letter of authorization with that id on this account")
        req["sloa_id"] = s["id"]
        req["counterparty"] = {k: v for k, v in {
            "owner_name": s.get("payee"), "owner_kind": s.get("payee_kind") or "individual",
            "institution": s.get("institution"), "account_last4": s.get("account_last4")}.items() if v}
    if body.type == "wire_out" and not (req.get("counterparty") or {}).get("institution"):
        raise HTTPException(422, "Name the receiving bank")
    if body.positions:
        secs = {s.id: s for s in db.scalars(select(Security)).all()}
        req["positions"] = []
        for p in body.positions:
            s = secs.get(p.security_id)
            req["positions"].append({"security_id": p.security_id, "quantity": p.quantity,
                                     "description": s.name if s else p.security_id,
                                     "asset_type": s.asset_type if s else "equity",
                                     "market_value": round(p.quantity * s.price, 2) if s else 0})
    elif body.type in ("acats_in", "acats_out", "loi_in"):
        if body.type == "acats_out":                 # a full transfer out: everything the account holds settled
            cash, holdings = ledger.settled(db, a.id)
            secs = {s.id: s for s in db.scalars(select(Security)).all()}
            req["positions"] = [{"security_id": k, "quantity": q, "description": secs[k].name if k in secs else k,
                                 "asset_type": secs[k].asset_type if k in secs else "equity"}
                                for k, q in sorted(holdings.items()) if q > 0]
            req["cash"] = round(max(cash, 0), 2)
        else:
            raise HTTPException(422, "List the positions to transfer")
    if body.cash is not None and body.type == "acats_out":
        req["cash"] = round(body.cash, 2)
    if body.type == "acats_out" and role not in ("receiving_firm",):
        raise HTTPException(422, "An ACATS transfer out starts at the receiving firm, with the client's signature")
    if role == "signed_form":
        if not body.client_signed:
            raise HTTPException(422, "A request by mail needs the client's signature")
        req["client_signed"] = True
    for k in ("title_on_form", "note", "idempotency_key"):
        if getattr(body, k):
            req[k] = getattr(body, k)
    title = f"{spec['label']}: {a.display_name}"
    case = new_case(db, "transfer", title, {"account_id": a.id, **req}, user, account=a)
    run_case(db, case, user)
    db.commit()
    return {"created": True, "case": case_detail(db, case, user)}


# --------------------------------------------------------------------------- accounts and balances
def _bucketed(b: dict) -> dict:
    return {k: round(v, 6) for k, v in b.items()}


@router.get("/money/accounts")
def accounts(q: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Accounts with their cash and value (operations: every open account; the portal: its own)."""
    scope = _scope(db, user)
    rows = db.scalars(select(Account).order_by(Account.id)).all()
    out = []
    for a in rows:
        if scope is not None and a.id not in scope:
            continue
        if scope is None and a.status != "OPEN":
            continue
        if q and q.lower() not in f"{a.id} {a.display_name}".lower():
            continue
        cash = ledger.account_balances(db, a.id).get(CASH, {})
        out.append({"id": a.id, "name": a.display_name, "status": a.status if scope is None
                    else portal_view.account_status(a.status), "group": a.group,
                    "registration": a.registration_type, "firm": db.get(Firm, a.firm_id).legal_name,
                    "cash": round(cash.get("settled", 0), 2), "cash_pending": round(cash.get("pending", 0), 2),
                    "cash_restricted": round(cash.get("restricted", 0), 2), "value": ledger.account_value(db, a.id),
                    "sloas": [{"id": x["id"], "payee": x.get("payee"), "institution": x.get("institution"),
                               "account_last4": x.get("account_last4")}
                              for x in (a.application or {}).get("sloas") or [] if x.get("status") == "active"]})
    return out


@router.get("/money/accounts/{account_id}")
def account(account_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    a = _account_for(db, user, account_id)
    portal = user.role in PORTAL_ROLES
    positions = ledger.positions(db, a.id)
    links = [{"id": b.id, "bank": b.bank, "last4": b.last4, "owner": b.owner, "status": b.status}
             for b in db.scalars(select(BankLink).where(BankLink.account_id == a.id)).all()]
    entries = ledger.entries(db, account=a.id, limit=60)
    if portal:                                   # the client layer only: no GL, masters, or suspense
        activity = []
        for e in entries:
            legs = [x for x in e["legs"] if x["layer"] == "client" and x["account"] == a.id]
            if legs:
                activity.append({"date": e["effective_date"], "description": e["description"],
                                 "legs": [{"asset": x["asset"], "bucket": x["bucket"], "amount": x["amount"]}
                                          for x in legs]})
        entries = activity
    return {"id": a.id, "name": a.display_name, "group": a.group, "registration": a.registration_type,
            "status": portal_view.account_status(a.status) if portal else a.status,
            "firm": db.get(Firm, a.firm_id).legal_name, "value": round(sum(p["value"] for p in positions), 2),
            "positions": positions, "bank_links": links, "entries": entries,
            "restrictions": restrictions_for(db, a.id) if portal else [{"code": c} for c in codes_on(db, a.id)],
            "as_of": settings.today().isoformat()}


# --------------------------------------------------------------------------- the operations overview
@router.get("/money/overview")
def overview(user: User = Depends(require_capability("transfers")), db: Session = Depends(get_db)):
    cases = db.scalars(select(Case).where(Case.kind == "transfer")).all()
    by_status: dict[str, int] = {}
    for c in cases:
        by_status[c.status] = by_status.get(c.status, 0) + 1
    today = settings.today().isoformat()
    moved = [c for c in cases if c.status == "COMPLETED" and c.closed_at and c.closed_at.date().isoformat() == today]
    return {"as_of": today, "by_status": by_status, "in_transit": settlement.in_transit(db),
            "completed_today": len(moved), "can_enter": _can_work(user, "asset_transfer"),
            "requesters": OPS_REQUESTERS, "types": {k: v["label"] for k, v in engine.TYPES.items()}}


@router.get("/money/securities")
def securities(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [{"id": s.id, "ticker": s.ticker, "name": s.name, "asset_type": s.asset_type, "price": s.price,
             "location": s.location, "accepted": s.accepted} for s in db.scalars(select(Security)
                                                                               .order_by(Security.name)).all()]


# --------------------------------------------------------------------------- settlement (map 12)
def _settlement_view(db: Session) -> dict:
    today = settings.today()
    last = db.scalar(select(SettlementRun).order_by(SettlementRun.id.desc()))
    receipts = db.scalars(select(BankReceipt).order_by(BankReceipt.id.desc()).limit(50)).all()
    proof = ledger.proof(db)
    return {"as_of": today.isoformat(), "last_run": settlement.run_dict(last), "checks": proof["checks"],
            "trial_balance": ledger.trial_balance(db), "masters": ledger.masters(db),
            "receipts": [settlement.receipt_dict(r, today) for r in receipts],
            "in_transit": settlement.in_transit(db), "today_flows": settlement.net_flows(db, today),
            "protection": protection.compute(db)}


@router.get("/settlement")
def settlement_view(user: User = Depends(require_capability("settlement")), db: Session = Depends(get_db)):
    return _settlement_view(db) | {"can_act": _can_work(user, "settlement")}


def _settler(user: User, db: Session, what: str) -> None:
    if not _can_work(user, "settlement"):
        audit.refuse(db, user, what, "Only the settlement and reconciliation team works the settlement cycle",
                     target_type="endpoint", target_id=f"/api/settlement ({what})")


class ReserveDeposit(BaseModel):
    amount: float = Field(gt=0, le=100_000_000)
    note: str = Field(min_length=10, max_length=300)


@router.post("/settlement/reserve/deposit")
def reserve_deposit(body: ReserveDeposit, user: User = Depends(require_capability("settlement")),
                    db: Session = Depends(get_db)):
    """Rule 15c3-3: move cash from the settlement bank into the special reserve bank account."""
    _settler(user, db, "reserve_deposit")
    protection.deposit(db, user, body.amount, body.note)
    db.commit()
    return _settlement_view(db) | {"can_act": True}


@router.post("/settlement/run")
def run_settlement(user: User = Depends(require_capability("settlement")), db: Session = Depends(get_db)):
    _settler(user, db, "settlement_run")
    settlement.run_cycle(db, user)
    db.commit()
    return _settlement_view(db) | {"can_act": True}


class Receipt(BaseModel):
    channel: str = Field(pattern="^(Fedwire|ACH)$")
    amount: float = Field(gt=0, le=10_000_000)
    sender: str = Field(min_length=2, max_length=200)
    memo: str = Field(default="", max_length=300)


@router.post("/settlement/receipts")
def simulate_receipt(body: Receipt, user: User = Depends(require_capability("settlement")),
                     db: Session = Depends(get_db)):
    """The bank simulator (principle 8): money arrives with no request behind it."""
    _settler(user, db, "receipt_simulated")
    if not settings.demo_mode:
        raise HTTPException(404, "The bank simulator runs only in demo mode")
    settlement.receive(db, channel=body.channel, amount=body.amount, sender=body.sender, memo=body.memo, actor=user)
    db.commit()
    return _settlement_view(db) | {"can_act": True}


class ApplyReceipt(BaseModel):
    account_id: str
    note: str = Field(default="", max_length=500)


class Note(BaseModel):
    note: str = Field(default="", max_length=500)


@router.post("/settlement/receipts/{receipt_id}/apply")
def apply(receipt_id: int, body: ApplyReceipt, user: User = Depends(require_capability("settlement")),
          db: Session = Depends(get_db)):
    _settler(user, db, "receipt_apply")
    r = db.get(BankReceipt, receipt_id)
    if r is None:
        raise HTTPException(404, "No such receipt")
    settlement.apply_receipt(db, r, body.account_id, user, body.note)
    db.commit()
    return _settlement_view(db) | {"can_act": True}


@router.post("/settlement/receipts/{receipt_id}/return")
def send_back(receipt_id: int, body: Note, user: User = Depends(require_capability("settlement")),
              db: Session = Depends(get_db)):
    _settler(user, db, "receipt_return")
    r = db.get(BankReceipt, receipt_id)
    if r is None:
        raise HTTPException(404, "No such receipt")
    settlement.return_receipt(db, r, user, body.note)
    db.commit()
    return _settlement_view(db) | {"can_act": True}


@router.get("/ledger/entries")
def ledger_entries(account: str | None = None, case: int | None = None, limit: int = 100,
                   user: User = Depends(require_capability("settlement")), db: Session = Depends(get_db)):
    return ledger.entries(db, account=account, case_id=case, limit=min(limit, 500))


# --------------------------------------------------------------------------- reconciliation (map 13)
def _recon_view(db: Session) -> dict:
    today = settings.today()
    last = db.scalar(select(ReconRun).order_by(ReconRun.id.desc()))
    items = db.scalars(select(StreetItem).order_by(StreetItem.status != "resolved", StreetItem.id.desc())).all()
    return {"as_of": today.isoformat(), "last_run": recon.run_dict(last),
            "breaks": [recon.item_dict(db, i, today) for i in items], "locations": recon.by_location(db),
            "patterns": {k: v["label"] for k, v in recon.rules.PATTERNS.items()}}


@router.get("/recon")
def recon_view(user: User = Depends(require_capability("settlement")), db: Session = Depends(get_db)):
    return _recon_view(db) | {"can_act": _can_work(user, "settlement")}


@router.post("/recon/run")
def run_recon(user: User = Depends(require_capability("settlement")), db: Session = Depends(get_db)):
    _settler(user, db, "recon_run")
    recon.run(db, user)
    db.commit()
    return _recon_view(db) | {"can_act": True}


class Resolve(BaseModel):
    resolution: str = Field(min_length=10, max_length=500)
    account_id: str | None = None


@router.post("/recon/breaks/{item_id}/resolve")
def resolve_break(item_id: int, body: Resolve, user: User = Depends(require_capability("settlement")),
                  db: Session = Depends(get_db)):
    _settler(user, db, "recon_resolve")
    item = db.get(StreetItem, item_id)
    if item is None:
        raise HTTPException(404, "No such break")
    if body.account_id and db.get(Account, body.account_id) is None:
        raise HTTPException(422, "No such account")
    recon.resolve(db, item, user, resolution=body.resolution.strip(), account_id=body.account_id)
    db.commit()
    return _recon_view(db) | {"can_act": True}


class StreetReport(BaseModel):
    location: str = Field(pattern="^(1500|1510|1520|1010)$")
    asset: str
    difference: float
    pattern: str
    note: str = Field(min_length=5, max_length=300)
    account_id: str | None = None


@router.post("/recon/report")
def street_report(body: StreetReport, user: User = Depends(require_capability("settlement")),
                  db: Session = Depends(get_db)):
    """The street simulator (principle 8): DTC, an agent, a sponsor, or the bank reports a difference."""
    _settler(user, db, "street_report")
    if not settings.demo_mode:
        raise HTTPException(404, "The street simulator runs only in demo mode")
    if body.asset != CASH and db.get(Security, body.asset) is None:
        raise HTTPException(422, "No such security")
    recon.report(db, location=body.location, asset=body.asset, difference=body.difference, pattern=body.pattern,
                 note=body.note, account_id=body.account_id)
    db.commit()
    return _recon_view(db) | {"can_act": True}
