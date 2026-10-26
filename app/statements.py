"""
Quarterly account statements. A broker-dealer sends each customer a statement at least every quarter (FINRA Rule 2231),
and an adviser with custody relies on the qualified custodian's quarterly statements going straight to the client
(Advisers Act Rule 206(4)-2(a)(3)). Each statement covers one calendar quarter, from the client layer of the ledger:

- the account value at the start and the end (securities at the latest synthetic price: the demo keeps no price
  history, and the statement says so);
- the holdings at the end of the quarter;
- every posting in the quarter, with its date and what it did to cash and securities;
- the free credit balance notice (Rule 15c3-2): the cash is payable on demand, and the custodian may use it in its
  business as Rule 15c3-3 allows;
- a request to report errors promptly, and to compare the statement with any report from the adviser.

The nightly batch generates the last quarter's statements once, on its first run after the quarter ends; the
operations supervisor can run it by hand. Each is a PDF with its SHA-256, and the account holders get a notice.
"""
from __future__ import annotations

import hashlib
import textwrap
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from custody.calendar import quarter_bounds
from custody.ledger import CASH, amount

from . import audit
from .config import settings
from .documents import make_pdf_pages
from .orm import Account, Firm, LedgerEntry, LedgerLeg, Security, Statement, User


def previous_quarter(today: date | None = None) -> tuple[date, date]:
    return quarter_bounds(today or settings.today(), -1)


def _legs(db: Session, account_id: str, through: date) -> list[tuple]:
    q = (select(LedgerEntry.id, LedgerEntry.effective_date, LedgerEntry.description, LedgerLeg.asset, LedgerLeg.units)
         .join(LedgerLeg, LedgerLeg.entry_id == LedgerEntry.id)
         .where(LedgerLeg.layer == "client", LedgerLeg.account == account_id, LedgerEntry.effective_date <= through)
         .order_by(LedgerEntry.effective_date, LedgerEntry.id))
    return list(db.execute(q).all())


def build(db: Session, a: Account, start: date, end: date) -> tuple[list[str], dict]:
    secs = {s.id: s for s in db.scalars(select(Security)).all()}
    px = {k: s.price for k, s in secs.items()}
    rows = _legs(db, a.id, end)
    opening: dict[str, int] = {}
    closing: dict[str, int] = {}
    activity: dict[int, dict] = {}
    for eid, on, desc, asset, u in rows:
        closing[asset] = closing.get(asset, 0) + u
        if on < start:
            opening[asset] = opening.get(asset, 0) + u
        else:
            item = activity.setdefault(eid, {"on": on, "desc": desc, "cash": 0, "secs": {}})
            if asset == CASH:
                item["cash"] += u
            else:
                item["secs"][asset] = item["secs"].get(asset, 0) + u

    def value(bal: dict[str, int]) -> float:
        return round(sum(amount(u) * (1.0 if k == CASH else px.get(k, 0.0)) for k, u in bal.items()), 2)

    firm = db.get(Firm, a.firm_id)
    cash_open, cash_close = amount(opening.get(CASH, 0)), amount(closing.get(CASH, 0))
    period = f"{start:%B} {start.day} to {end:%B} {end.day}, {end.year}"
    lines = ["SYNTHETIC SAMPLE: NOT A REAL STATEMENT", "",
             f"Account {a.id}: {a.display_name} ({a.registration_type.replace('_', ' ')})",
             f"Adviser: {firm.legal_name if firm else 'none'}   Statement period: {period}", "",
             f"Account value: ${value(opening):,.2f} at the start, ${value(closing):,.2f} at the end",
             f"Cash: ${cash_open:,.2f} at the start, ${cash_close:,.2f} at the end",
             "Securities are valued at the latest synthetic price (the demo keeps no price history).", "",
             f"Holdings at {end:%B} {end.day}, {end.year} (including positions still in transit to the account)"]
    held = [(k, u) for k, u in sorted(closing.items()) if k != CASH and u]
    for k, u in held:
        s = secs.get(k)
        name = (s.name if s else k)[:44]
        lines.append(f"  {name:<44} {amount(u):>14,.4f}  @ ${px.get(k, 0):>10,.2f}  = ${amount(u) * px.get(k, 0):>13,.2f}")
    if not held:
        lines.append("  No securities")
    lines += ["", f"Activity in the period ({len(activity)} postings)"]
    for item in activity.values():
        parts = []
        if item["cash"]:
            parts.append(f"cash {'+' if item['cash'] > 0 else '-'}${abs(amount(item['cash'])):,.2f}")
        for k, u in item["secs"].items():
            parts.append(f"{(secs[k].ticker or k) if k in secs else k} {'+' if u > 0 else ''}{amount(u):,.4f}")
        text = f"  {item['on'].isoformat()}  {item['desc'][:70]}" + (f": {'; '.join(parts)}" if parts else "")
        lines += textwrap.wrap(text, 112, subsequent_indent=" " * 14) or [text]
    if not activity:
        lines.append("  No activity")
    lines += ["",
              f"Free credit balance: ${max(cash_close, 0):,.2f}. It is payable to you on demand. As Rule 15c3-3 allows, "
              "we may use it in our business",
              "while we keep the customer reserve the rule requires (Rule 15c3-2 notice).",
              "Please review this statement and tell us in writing promptly about anything that looks wrong.",
              "If your adviser sends you reports about this account, compare them with this statement."]
    summary = {"opening_value": value(opening), "closing_value": value(closing), "cash_open": cash_open,
               "cash_close": cash_close, "positions": len(held), "postings": len(activity)}
    return lines, summary


def generate(db: Session, actor: User | None, period: tuple[date, date] | None = None) -> dict:
    """Statements for every account open at any time in the quarter, once each."""
    from .mail import client_notice
    start, end = period or previous_quarter()
    done = {s.account_id for s in db.scalars(select(Statement).where(Statement.period_end == end)).all()}
    made = []
    for a in db.scalars(select(Account).where(Account.status.in_(("OPEN", "OPEN_RESTRICTED", "CLOSED")))
                        .order_by(Account.id)).all():
        if a.id in done or (a.opened_at and a.opened_at.date() > end) or not _legs(db, a.id, end):
            continue
        lines, summary = build(db, a, start, end)
        quarter = (end.month - 1) // 3 + 1
        pdf = make_pdf_pages(f"Quarterly statement, Q{quarter} {end.year}", lines)
        st = Statement(account_id=a.id, period_start=start, period_end=end,
                       filename=f"statement-{a.id.lower()}-{end.year}q{quarter}.pdf",
                       sha256=hashlib.sha256(pdf).hexdigest(), content=pdf, summary=summary,
                       generated_by=actor.display_name if actor else "Nightly batch")
        db.add(st)
        db.flush()
        st.notices = client_notice(db, a, template="statement_ready",
                                   subject=f"Your Q{quarter} {end.year} statement: {a.display_name}",
                                   text=f"Your quarterly statement for {start:%B} {start.day} to {end:%B} {end.day}, "
                                        f"{end.year} is ready. Your adviser has a copy, and we'll send one to you on "
                                        "request. Please review it and tell us promptly about anything that looks "
                                        "wrong.")
        made.append(st.id)
    audit.record(db, actor, "statements_generated", "statements", end.isoformat(), period_start=start.isoformat(),
                 statements=len(made))
    db.flush()
    return {"period_start": start.isoformat(), "period_end": end.isoformat(), "generated": len(made),
            "already": len(done)}


def to_dict(s: Statement) -> dict:
    return {"id": s.id, "account_id": s.account_id, "period_start": s.period_start.isoformat(),
            "period_end": s.period_end.isoformat(), "filename": s.filename, "sha256": s.sha256, "summary": s.summary,
            "generated_by": s.generated_by, "generated_at": s.generated_at.isoformat() if s.generated_at else None}
