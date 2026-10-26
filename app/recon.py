"""
Daily position and cash reconciliation (map 13), after the settlement cycle:

1. Receive the street's files. A simulator stands in for DTC, the transfer agents, the sponsors, and the settlement
   bank (principle 8): the street is the books plus each difference it reports (a StreetItem), seeded or added.
2. Match the books (the GL location accounts, which the three-layer invariant ties to every client) to the street:
   positions exactly, cash to the cent (REC-001).
3. Classify each break by pattern and route it (REC-002): a known pattern up to $25,000 is fixed automatically; a
   classified one gets a drafted resolution an analyst approves; an unexplained one, or one over $100,000, is worked
   by hand. Every fix posts a balanced correcting entry.
4. Escalate a break open more than 5 business days, or worth more than $10,000, to the supervisor, and log it in the
   risk register as a financial event (REC-003).
5. Certify the books only when nothing is open but expected in-transit items (REC-004); billing bills only certified
   periods.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from custody import recon as rules
from custody.calendar import bdays_between
from custody.ledger import CASH, LOCATION_GL, amount, client_move, gl_move

from . import audit, ledger
from .config import settings
from .orm import Account, Case, ReconRun, Security, StreetItem, User, utcnow
from .risk_register import log_event

LOCATION_NAME = {**{v: k for k, v in LOCATION_GL.items()}, "1010": "Settlement bank",
                 "1030": "Special reserve bank account (15c3-3)"}
AUTO = "Reconciliation engine (REC-002 auto-fix)"


def street(db: Session) -> tuple[dict, dict]:
    """(books, street) by (GL location, asset)."""
    books = ledger.location_books(db)
    st = dict(books)
    for item in db.scalars(select(StreetItem).where(StreetItem.status != "resolved")).all():
        key = (item.location, item.asset)
        st[key] = round(st.get(key, 0) + amount(item.units), 6)
    return books, st


def value_of(db: Session, item: StreetItem) -> float:
    qty = abs(amount(item.units))
    if item.asset == CASH:
        return round(qty, 2)
    s = db.get(Security, item.asset)
    return round(qty * (s.price if s else 0), 2)


def _holders(db: Session, asset: str) -> list[tuple[str, float]]:
    out = []
    for _layer, acct, a, bucket, u in ledger.balances(db, layer="client"):
        if a == asset and bucket == "settled" and u > 0:
            out.append((acct, amount(u)))
    return sorted(out)


def fix_legs(db: Session, item: StreetItem) -> list:
    """The balanced correcting entry for a break, by pattern, or [] when the fix isn't a posting."""
    units_ = item.units
    firms = ledger.firm_of(db)
    if item.pattern == "dividend_reinvestment":            # book each holder's reinvested shares, pro rata
        holders = _holders(db, item.asset)
        total = sum(q for _a, q in holders)
        if not holders or total <= 0:
            return []
        legs, given = [], 0
        for i, (acct, q) in enumerate(holders):
            share = units_ - given if i == len(holders) - 1 else int(units_ * q / total)
            given += share
            if share:
                legs += client_move(acct, firms[acct], item.asset, share, "settled", item.location)
        return legs
    if item.pattern == "bank_fee":                         # the bank debited a fee: the custodian's expense
        return gl_move("5100", "1010", CASH, -units_)
    if item.pattern in ("late_delivery", "unexplained") and item.account_id:
        return client_move(item.account_id, firms[item.account_id], item.asset, units_, "settled", item.location)
    return []


def propose(db: Session, item: StreetItem) -> dict:
    """The drafted resolution an analyst approves (AI drafts it in production; rules stand in here)."""
    p = rules.PATTERNS.get(item.pattern, rules.PATTERNS["unexplained"])
    out = {"text": p["fix"], "evidence": [item.note]}
    if item.pattern == "dividend_reinvestment":
        holders = _holders(db, item.asset)
        out["evidence"].append(f"{len(holders)} holders share {amount(item.units):g} shares pro rata")
    if item.pattern == "ach_return_unbooked" and item.case_id:
        out["text"] += f" (case #{item.case_id})"
    if item.pattern == "late_delivery" and item.account_id:
        out["text"] += f": {item.account_id}"
    return out


def resolve(db: Session, item: StreetItem, actor: User | None, *, resolution: str, account_id: str | None = None,
            automatic: bool = False) -> None:
    """Post the correction (or record that the street corrected itself) and close the break."""
    from .runner import run_case
    if item.status == "resolved":
        raise HTTPException(409, "This break is already resolved")
    how = resolution
    if item.pattern == "ach_return_unbooked" and item.case_id:
        case = db.get(Case, item.case_id)
        events = list((case.extra or {}).get("events", []))
        n = 1 + sum(1 for e in events if e.get("type") == "correction")
        if not any(e.get("type") == "rail" and e.get("outcome") == "returned" for e in events):
            events.append({"type": "rail", "n": n, "outcome": "returned", "code": "R01",
                           "on": settings.today().isoformat(), "by": actor.display_name if actor else AUTO})
            case.extra = {**(case.extra or {}), "events": events}
            run_case(db, case, actor)
        how = f"{resolution} The return is processed on case #{case.id}."
    elif item.pattern == "unexplained" and not account_id and resolution:
        how = f"{resolution} The street corrected its record; nothing posts."
    else:
        if account_id:
            item.account_id = account_id
        legs = fix_legs(db, item)
        if not legs:
            raise HTTPException(422, "Name the account the difference belongs to, or say the street corrected it")
        ledger.post(db, f"RECON-{item.id}", "recon_fix", f"Reconciliation fix: {item.note}", legs, settings.today(),
                    actor=actor, account_id=item.account_id)
    item.status, item.resolution = "resolved", how
    item.resolved_by = AUTO if automatic else (actor.display_name if actor else "system")
    item.resolved_at = utcnow()
    audit.record(db, actor, "recon_break_resolved", "street_item", item.id, pattern=item.pattern,
                 automatic=automatic, resolution=how)


def run(db: Session, actor: User | None) -> ReconRun:
    today = settings.today()
    breaks = []
    for item in db.scalars(select(StreetItem).where(StreetItem.status != "resolved").order_by(StreetItem.id)).all():
        value = value_of(db, item)
        item.route = rules.route(item.pattern, value)
        if item.route == "auto_fix" and fix_legs(db, item):
            resolve(db, item, actor, resolution=rules.PATTERNS[item.pattern]["fix"] + ".", automatic=True)
            breaks.append(item_dict(db, item, today))
            continue
        if item.route == "ai_suggest" and item.proposal is None:
            item.proposal = propose(db, item)
            item.status = "proposed"
        why = rules.escalate(item.opened_on, today, value)
        if why and not item.escalated:
            item.escalated = why
            log_event(db, actor, category="Reconciliation break escalated", risk_type="financial", source="recon",
                      summary=f"{LOCATION_NAME.get(item.location, item.location)}: {item.note} ({why})",
                      area="settlement", detected_by="Reconciliation (REC-003)", target_id=item.account_id,
                      dedupe_key=f"recon-{item.id}")
        breaks.append(item_dict(db, item, today))
    books, st = street(db)
    diffs = rules.compare(books, st)
    open_ = [b for b in breaks if b["status"] != "resolved"]
    ok, why = rules.certifiable([{"status": b["status"], "pattern": b["pattern"]} for b in breaks])
    from .settlement import in_transit
    transit = in_transit(db)
    proof = ledger.proof(db)
    balanced = all(c["ok"] for c in proof["checks"])
    result = {"locations": len({k[0] for k in books}), "positions_matched": len(books) - len(diffs),
              "positions": len(books), "differences": len(diffs), "breaks": len(open_),
              "auto_fixed": [b["id"] for b in breaks if b["resolved_by"] == AUTO],
              "escalated": [b["id"] for b in open_ if b["escalated"]],
              "in_transit": transit, "stale_in_transit": [t["case"] for t in transit if t["overdue"]],
              "certifiable": ok and balanced, "certification": why if balanced else "The three layers don't balance",
              "checks": proof["checks"]}
    r = ReconRun(business_date=today, run_by=actor.display_name if actor else "system", result=result)
    if result["certifiable"]:
        r.certified_by, r.certified_at = actor.display_name if actor else "system", utcnow()
    db.add(r)
    db.flush()
    audit.record(db, actor, "recon_run", "recon_run", r.id, business_date=today.isoformat(), breaks=len(open_),
                 certified=result["certifiable"])
    return r


def item_dict(db: Session, item: StreetItem, today: date) -> dict:
    s = db.get(Security, item.asset) if item.asset != CASH else None
    qty = amount(item.units)
    return {"id": item.id, "location": LOCATION_NAME.get(item.location, item.location), "gl": item.location,
            "asset": item.asset, "security": "Cash" if item.asset == CASH else (s.name if s else item.asset),
            "ticker": s.ticker if s else None, "difference": qty, "value": value_of(db, item),
            "pattern": item.pattern, "pattern_label": rules.PATTERNS.get(item.pattern, {}).get("label", item.pattern),
            "note": item.note, "account_id": item.account_id, "case_id": item.case_id,
            "opened_on": item.opened_on.isoformat(), "age_days": bdays_between(item.opened_on, today),
            "status": item.status, "route": item.route, "proposal": item.proposal, "escalated": item.escalated,
            "resolved_by": item.resolved_by, "resolution": item.resolution,
            "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None}


def run_dict(r: ReconRun | None) -> dict | None:
    if r is None:
        return None
    return {"id": r.id, "business_date": r.business_date.isoformat(), "run_at": r.run_at.isoformat(),
            "run_by": r.run_by, "certified_by": r.certified_by,
            "certified_at": r.certified_at.isoformat() if r.certified_at else None, **(r.result or {})}


def report(db: Session, *, location: str, asset: str, difference: float, pattern: str, note: str,
           account_id: str | None = None, case_id: int | None = None, opened_on: date | None = None) -> StreetItem:
    """The simulator: the street reports a difference against the books."""
    if pattern not in rules.PATTERNS:
        raise HTTPException(422, f"Unknown break pattern {pattern}")
    item = StreetItem(location=location, asset=asset, units=int(round(difference * 1_000_000)), pattern=pattern,
                      note=note, account_id=account_id, case_id=case_id, opened_on=opened_on or settings.today())
    db.add(item)
    db.flush()
    return item


def by_location(db: Session) -> list[dict]:
    """Books against the street for every location, as positions and value."""
    books, st = street(db)
    px = ledger.prices(db)
    rows: dict[str, dict] = defaultdict(lambda: {"positions": 0, "books_value": 0.0, "street_value": 0.0,
                                                 "differences": 0})
    for (loc, asset), qty in books.items():
        r = rows[loc]
        r["positions"] += 1
        price = 1.0 if asset == CASH else px.get(asset, 0)
        r["books_value"] = round(r["books_value"] + qty * price, 2)
        r["street_value"] = round(r["street_value"] + st.get((loc, asset), 0) * price, 2)
        r["differences"] += abs(st.get((loc, asset), 0) - qty) > (0.005 if asset == CASH else 1e-9)
    return [{"gl": k, "location": LOCATION_NAME.get(k, k), **v} for k, v in sorted(rows.items())]
