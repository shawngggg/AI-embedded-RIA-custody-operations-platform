"""
The synthetic book (synthetic/book.py) built from the demo's own open accounts, so the release 2 and 3 previews tell the
same story as the cases: the accounts the principal accepted hold positions, and their restriction codes shape what
they can do. Built once per state of the accounts and cached.
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


def as_of(accounts: list[dict]):
    """The demo's date, or the latest opening if the seed's clock ran ahead of a fixed test date."""
    return max([settings.today()] + [a["opened_on"] for a in accounts])


def current(db: Session) -> dict:
    accounts, firms = open_accounts(db)
    day = as_of(accounts)
    key = (day.isoformat(), tuple((a["id"], a["opened_on"].isoformat(), tuple(a["codes"])) for a in accounts))
    if key not in _cache:
        _cache.clear()
        _cache[key] = synthetic_book.build(accounts, firms, day)
    return _cache[key]
