"""
The three-layer ledger in the database (custody/ledger.py has the rules). Every entry is validated before it's written,
and an entry whose reference is already posted isn't posted again, so a workflow can replay safely.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from fastapi import HTTPException
from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from custody import ledger as rules
from custody.ledger import CASH, CHART, LOCATION_GL, Leg

from . import audit
from .orm import Account, Firm, LedgerEntry, LedgerLeg, Security, User


class LedgerError(ValueError):
    pass


def firm_of(db: Session, accounts: set[str] | None = None) -> dict[str, str]:
    """Client account -> its RIA's CRD (the master it rolls up to), for every account or just `accounts`."""
    q = select(Account.id, Firm.crd).join(Firm, Firm.id == Account.firm_id)
    if accounts is not None:
        q = q.where(Account.id.in_(accounts or {"-"}))
    return {a: crd for a, crd in db.execute(q).all()}


def post(db: Session, ref: str, kind: str, description: str, legs: list[Leg], on: date, *, actor: User | None = None,
         case_id: int | None = None, account_id: str | None = None, relink: tuple | None = None,
         firms: dict | None = None) -> LedgerEntry | None:
    """Write a balanced entry once. Returns the new entry, or None when `ref` is already posted."""
    if db.scalar(select(LedgerEntry.id).where(LedgerEntry.ref == ref)):
        return None
    if firms is None:
        firms = firm_of(db, {leg.account for leg in legs if leg.layer == "client"})
    errors = rules.validate(legs, firms, relink_of=relink)
    if errors:
        raise LedgerError(f"{ref}: " + "; ".join(errors))
    e = LedgerEntry(ref=ref, kind=kind, description=description, case_id=case_id, account_id=account_id,
                    effective_date=on, created_by=actor.display_name if actor else "system")
    db.add(e)
    db.flush()
    # One batched insert, not one per leg: an opening balance has dozens of legs, and the database may be a network
    # hop away (Neon), where each statement is a round trip
    db.execute(insert(LedgerLeg), [{"entry_id": e.id, "layer": leg.layer, "account": leg.account, "asset": leg.asset,
                                    "bucket": leg.bucket, "units": leg.units} for leg in legs])
    if actor is not None or case_id is not None:
        audit.record(db, actor, "ledger_posted", "ledger_entry", ref, case_id=case_id, kind=kind,
                     legs=len(legs), account=account_id)
    return e


def balances(db: Session, account: str | None = None, layer: str | None = None) -> list[tuple]:
    """(layer, account, asset, bucket, units) summed over every leg."""
    q = select(LedgerLeg.layer, LedgerLeg.account, LedgerLeg.asset, LedgerLeg.bucket, func.sum(LedgerLeg.units)) \
        .group_by(LedgerLeg.layer, LedgerLeg.account, LedgerLeg.asset, LedgerLeg.bucket)
    if account:
        q = q.where(LedgerLeg.account == account)
    if layer:
        q = q.where(LedgerLeg.layer == layer)
    return [(r[0], r[1], r[2], r[3], int(r[4] or 0)) for r in db.execute(q).all()]


def case_legs(db: Session, case_id: int, account: str, bucket: str) -> dict[str, int]:
    """What one case has posted to an account's bucket at the client layer, by asset (units)."""
    rows = db.execute(select(LedgerLeg.asset, func.sum(LedgerLeg.units))
                      .join(LedgerEntry, LedgerEntry.id == LedgerLeg.entry_id)
                      .where(LedgerEntry.case_id == case_id, LedgerLeg.layer == "client", LedgerLeg.account == account,
                             LedgerLeg.bucket == bucket).group_by(LedgerLeg.asset)).all()
    return {asset: int(u or 0) for asset, u in rows if u}


def account_balances(db: Session, account_id: str) -> dict[str, dict[str, float]]:
    """A client account's balances: asset -> bucket -> quantity (dollars for USD)."""
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for _layer, _acct, asset, bucket, u in balances(db, account_id, "client"):
        if u:
            out[asset][bucket] = rules.amount(u)
    return dict(out)


def settled(db: Session, account_id: str) -> tuple[float, dict[str, float]]:
    """(settled cash, settled holdings) for an account."""
    b = account_balances(db, account_id)
    return b.get(CASH, {}).get("settled", 0.0), {a: v["settled"] for a, v in b.items()
                                                  if a != CASH and v.get("settled")}


def prices(db: Session) -> dict[str, float]:
    return {s.id: s.price for s in db.scalars(select(Security)).all()}


def positions(db: Session, account_id: str) -> list[dict]:
    secs = {s.id: s for s in db.scalars(select(Security)).all()}
    out = []
    for asset, buckets in sorted(account_balances(db, account_id).items()):
        s = secs.get(asset)
        qty = sum(buckets.values())
        price = 1.0 if asset == CASH else (s.price if s else 0.0)
        out.append({"asset": asset, "name": "Cash" if asset == CASH else (s.name if s else asset),
                    "ticker": s.ticker if s else None, "quantity": round(qty, 6), "buckets": buckets,
                    "price": price, "value": round(qty * price, 2),
                    "price_as_of": s.price_as_of.isoformat() if s and s.price_as_of else None})
    return out


def account_value(db: Session, account_id: str) -> float:
    return round(sum(p["value"] for p in positions(db, account_id)), 2)


def proof(db: Session) -> dict:
    p = rules.proof(balances(db), firm_of(db))
    p["checks"] = [
        {"check": "Each RIA master equals the sum of its clients", "ok": p["clients_equal_masters"],
         "differences": p["master_differences"]},
        {"check": "The RIA masters equal the general ledger's client control accounts (2100, 2500)",
         "ok": p["masters_equal_gl"], "differences": p["gl_differences"]},
        {"check": "The general ledger balances: debits equal credits, asset by asset", "ok": p["gl_balances"],
         "differences": p["gl_unbalanced"]},
    ]
    return p


def trial_balance(db: Session) -> list[dict]:
    """GL accounts with their cash balance and the market value of securities held there."""
    px = prices(db)
    rows: dict[str, dict] = {}
    for _layer, acct, asset, _bucket, u in balances(db, layer="gl"):
        r = rows.setdefault(acct, {"account": acct, "name": CHART.get(acct, (acct, ""))[0],
                                   "type": CHART.get(acct, ("", ""))[1], "cash": 0.0, "securities_value": 0.0,
                                   "positions": 0})
        if asset == CASH:
            r["cash"] = round(r["cash"] + rules.amount(u), 2)
        elif u:
            r["securities_value"] = round(r["securities_value"] + rules.amount(u) * px.get(asset, 0), 2)
            r["positions"] += 1
    return [rows[k] for k in sorted(rows)]


def masters(db: Session) -> list[dict]:
    """Each RIA master account: cash and securities value, and how many client accounts roll up to it."""
    px = prices(db)
    firms = {f"MASTER-{f.crd}": f for f in db.scalars(select(Firm)).all()}
    counts: dict[str, int] = defaultdict(int)
    for a, crd in firm_of(db).items():
        counts[f"MASTER-{crd}"] += 1
    out: dict[str, dict] = {}
    for _layer, acct, asset, bucket, u in balances(db, layer="master"):
        r = out.setdefault(acct, {"master": acct, "firm": firms[acct].legal_name if acct in firms else acct,
                                  "firm_status": firms[acct].status if acct in firms else None,
                                  "accounts": counts.get(acct, 0), "cash": 0.0, "pending_cash": 0.0,
                                  "restricted_cash": 0.0, "securities_value": 0.0})
        v = rules.amount(u)
        if asset == CASH:
            key = {"settled": "cash", "pending": "pending_cash", "restricted": "restricted_cash"}[bucket]
            r[key] = round(r[key] + v, 2)
        else:
            r["securities_value"] = round(r["securities_value"] + v * px.get(asset, 0), 2)
    return sorted(out.values(), key=lambda r: r["master"])


def location_books(db: Session) -> dict[tuple[str, str], float]:
    """What the books say each location holds: (GL location account, asset) -> quantity."""
    out = {}
    for _layer, acct, asset, _bucket, u in balances(db, layer="gl"):
        if acct in set(LOCATION_GL.values()) | {"1010", "1030"} and u:
            out[(acct, asset)] = out.get((acct, asset), 0) + rules.amount(u)
    return out


def entries(db: Session, *, account: str | None = None, case_id: int | None = None, limit: int = 200) -> list[dict]:
    q = select(LedgerEntry).order_by(LedgerEntry.id.desc()).limit(limit)
    if case_id is not None:
        q = q.where(LedgerEntry.case_id == case_id)
    if account:
        ids = select(LedgerLeg.entry_id).where(LedgerLeg.account == account)
        q = q.where(LedgerEntry.id.in_(ids))
    rows = db.scalars(q).all()
    legs: dict[int, list] = defaultdict(list)
    if rows:
        for leg in db.scalars(select(LedgerLeg).where(LedgerLeg.entry_id.in_([e.id for e in rows]))).all():
            legs[leg.entry_id].append({"layer": leg.layer, "account": leg.account, "asset": leg.asset,
                                       "bucket": leg.bucket, "amount": rules.amount(leg.units)})
    return [{"id": e.id, "ref": e.ref, "kind": e.kind, "description": e.description, "case_id": e.case_id,
             "account_id": e.account_id, "effective_date": e.effective_date.isoformat(),
             "created_by": e.created_by, "legs": legs[e.id]} for e in rows]


def require_zero_or_raise(db: Session, account_id: str) -> None:
    left = {a: b for a, b in account_balances(db, account_id).items() if any(abs(v) > 1e-9 for v in b.values())}
    if left:
        raise HTTPException(409, f"{account_id} still holds {', '.join(left)}")
