"""
The synthetic book (synthetic/book.py) for the release 3 previews (trading and billing), built from the demo's open
accounts and the positions and cash the release 2 ledger holds for them, so every page tells the same story. Built once
per state of the ledger and the accounts, and cached.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from synthetic import book as synthetic_book

from .config import settings
from .orm import Account, Firm, Restriction

_cache: dict[tuple, dict] = {}


def open_accounts(db: Session) -> tuple[list[dict], dict[str, str]]:
    firms = {f.id: f for f in db.scalars(select(Firm)).all()}
    accounts = db.scalars(select(Account).where(Account.status == "OPEN").order_by(Account.id)).all()
    codes: dict[str, list[str]] = {}
    for r in db.scalars(select(Restriction).where(Restriction.active.is_(True))).all():
        codes.setdefault(r.target_id, []).append(r.code)
    # A trust joins the household of the individual account its trustee holds at the same RIA, so a tiered fee
    # schedule bills the family on its combined value
    individual = {(a.firm_id, a.display_name): a.client_key for a in accounts if a.registration_type != "trust"}
    rows = []
    for a in accounts:
        app = a.application or {}
        household = a.client_key
        if a.registration_type == "trust":
            for h in app.get("holders", []):
                if h.get("role") == "trustee" and (a.firm_id, h.get("name")) in individual:
                    household = individual[(a.firm_id, h["name"])]
                    break
        rows.append({"id": a.id, "name": a.display_name, "firm_crd": firms[a.firm_id].crd, "group": a.group,
                     "registration_type": a.registration_type, "path": a.path,
                     "opened_on": a.opened_at.date() if a.opened_at else settings.today(),
                     "codes": sorted(set(codes.get(a.id, []))), "household": household,
                     "purpose": app.get("account_purpose") or "",
                     "funding": float(app.get("expected_initial_funding") or 0)})
    return rows, {f.crd: f.legal_name for f in firms.values()}


def ledger_view(db: Session) -> dict:
    """Each open account's settled holdings and cash from the release 2 ledger. An account with an outgoing ACATS in
    progress still counts the positions it's delivering (they're frozen, not gone) and is marked as leaving."""
    from . import ledger
    from .orm import Case
    out = {}
    leaving = {c.account_id: c for c in db.scalars(select(Case).where(
        Case.kind == "transfer", Case.status.in_(("AWAITING_SETTLEMENT", "OVERDUE")))).all()
        if (c.application or {}).get("type") == "acats_out"}
    for a in db.scalars(select(Account).where(Account.status == "OPEN")).all():
        cash, holdings = ledger.settled(db, a.id)
        view = {"holdings": dict(holdings), "cash": cash}
        if a.id in leaving:
            c = leaving[a.id]
            for asset, u in ledger.case_legs(db, c.id, a.id, "settled").items():
                if asset == "USD":
                    view["cash"] -= ledger.rules.amount(u)
                else:
                    view["holdings"][asset] = view["holdings"].get(asset, 0) - ledger.rules.amount(u)
            view["leaving_on"] = (c.result or {}).get("summary", {}).get("Expected to settle")
        out[a.id] = view
    return out


def current(db: Session) -> dict:
    """The release 3 previews' book: the ledger's positions and cash, with synthetic trading and billing on top."""
    from sqlalchemy import func
    from .orm import LedgerEntry
    accounts, firms = open_accounts(db)
    day = settings.today()
    key = (day.isoformat(), db.scalar(select(func.max(LedgerEntry.id))),
           tuple((a["id"], tuple(a["codes"])) for a in accounts))
    if key not in _cache:
        _cache.clear()
        _cache[key] = synthetic_book.build(accounts, firms, day, ledger=ledger_view(db))
    return _cache[key]
