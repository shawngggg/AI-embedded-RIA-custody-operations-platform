"""
Customer protection (Rule 15c3-3) on the live books: the reserve computation and the possession-or-control check
(custody/protection.py), run by the daily cycle. A deficit becomes a financial risk event for the settlement area the
same day; the settlement team records the deposit to the reserve bank account.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from custody import protection
from custody.ledger import CASH, gl_move, units

from . import audit, ledger, recon
from .config import settings
from .orm import LedgerEntry, Security, User
from .risk_register import log_event


def compute(db: Session) -> dict:
    client_cash: dict[str, int] = {}
    for _layer, acct, asset, _bucket, u in ledger.balances(db, layer="client"):
        if asset == CASH:
            client_cash[acct] = client_cash.get(acct, 0) + u
    # balances() splits by bucket; the formula wants each GL account's total
    gl_sum: dict[tuple[str, str], int] = {}
    for _layer, acct, asset, _bucket, u in ledger.balances(db, layer="gl"):
        gl_sum[(acct, asset)] = gl_sum.get((acct, asset), 0) + u
    reserve = protection.reserve_computation(client_cash, gl_sum, gl_sum.get((protection.RESERVE_GL, CASH), 0),
                                             settings.today())
    # The stock record (what the books hold for customers at each control location) against what the street confirms
    books, street = recon.street(db)
    customer: dict[str, int] = {}
    control: dict[str, int] = {}
    for side, out in ((books, customer), (street, control)):
        for (loc, asset), qty in side.items():
            if loc in protection.CONTROL_LOCATIONS:
                out[asset] = out.get(asset, 0) + units(qty)
    names = {s.id: s.name for s in db.scalars(select(Security)).all()}
    return {"reserve": reserve, "possession": protection.possession_or_control(customer, control, names)}


def check(db: Session, actor: User | None) -> dict:
    """The daily customer protection step: compute, and log a financial risk event for each deficit (once a day)."""
    out = compute(db)
    today = settings.today().isoformat()
    r = out["reserve"]
    if r["deficit"]:
        log_event(db, actor, category="Reserve formula deficit (Rule 15c3-3)", risk_type="financial",
                  source="protection", area="settlement", detected_by="Daily cycle (CP-001)",
                  summary=f"The reserve computation as of {r['as_of']} needs ${r['required']:,.2f} in the special "
                          f"reserve bank account; ${r['on_deposit']:,.2f} is on deposit. Deposit ${r['deficit']:,.2f} "
                          f"by {r['deposit_due']}.", dedupe_key=f"reserve:{today}")
    for d in out["possession"]["deficits"]:
        log_event(db, actor, category="Possession or control deficit (Rule 15c3-3)", risk_type="financial",
                  source="protection", area="settlement", detected_by="Daily cycle (CP-002)",
                  summary=f"{d['name']}: customers hold {d['customers']:,.4f} fully paid; the street confirms "
                          f"{d['in_control']:,.4f} at control locations. Bring {d['deficit']:,.4f} back into control.",
                  dedupe_key=f"control:{d['security']}:{today}")
    return out


def deposit(db: Session, actor: User, amount: float, note: str) -> LedgerEntry:
    """Move cash from the settlement bank into the special reserve bank account."""
    n = len(db.scalars(select(LedgerEntry.id).where(LedgerEntry.kind == "reserve_deposit")).all()) + 1
    e = ledger.post(db, f"RESERVE-{settings.today().isoformat()}-{n}", "reserve_deposit",
                    f"Deposit to the special reserve bank account (Rule 15c3-3): {note}",
                    gl_move(protection.RESERVE_GL, "1010", CASH, units(amount)), settings.today(), actor=actor)
    audit.record(db, actor, "reserve_deposit", "ledger_entry", e.ref, amount=round(amount, 2), note=note)
    return e
