"""
The daily cash settlement cycle (map 12), run by the settlement team or the operations supervisor:

1. Receive settlement activity: the rail simulator (principle 8) delivers every answer due by today for transfers in
   transit (a validation, a confirmation, a reject, a return), and each case re-runs and posts what it settled.
2. Release returnable ACH funds whose hold ended (MM-007), by re-running the deposits.
3. Unapplied incoming cash: match each receipt in suspense to an account from its memo and sender (STL-001); a sure
   match applies itself, the rest wait for an analyst; money nobody can match goes back after 10 business days
   (STL-002), with an analyst confirming the return.
4. Check the three-layer invariant over every balance (LED-001).
5. Net the day's cash movement at the settlement bank by channel: the funding the bank sends or receives.

Incoming wires and ACH credits arrive here as bank receipts. Each posts at once to the settlement bank and the suspense
account (2190), so the books always match the bank; applying it moves the money from suspense to the client.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from custody import money, settlement as rules
from custody.ledger import CASH, Leg, client_move, gl_move, units

from . import audit, ledger
from .config import settings
from .orm import Account, BankReceipt, Case, LedgerEntry, LedgerLeg, SettlementRun, User, utcnow

IN_TRANSIT = ("AWAITING_SETTLEMENT", "AWAITING_REREGISTRATION", "OVERDUE")
CHANNEL = {"ach_deposit": "ACH", "ach_withdrawal": "ACH", "wire_out": "Fedwire", "acats_in": "NSCC (ACATS)",
           "acats_out": "NSCC (ACATS)", "loi_in": "Sponsor", "journal": "Journal"}


# --------------------------------------------------------------------------- 1. rail answers
def deliver_rail_answers(db: Session, actor: User | None, through: date | None = None) -> list[dict]:
    """Record the answers the rail simulator owes each transfer in transit by `through` (today), and re-run it."""
    from .runner import run_case
    through = through or settings.today()
    out = []
    cases = db.scalars(select(Case).where(Case.kind == "transfer", Case.status.in_(IN_TRANSIT))
                       .order_by(Case.id)).all()
    for case in cases:
        events = list((case.extra or {}).get("events", []))
        n = 1 + sum(1 for e in events if e.get("type") == "correction")
        sub = next((e for e in reversed(events) if e.get("type") == "submitted" and e.get("n") == n), None)
        if not sub:
            continue
        script = (case.extra or {}).get("simulate") or {}
        have = {(e.get("outcome"), e.get("n")) for e in events if e.get("type") == "rail"}
        new = [a for a in money.rail_response(case.application, money.sends_on(sub), through, script)
               if (a["outcome"], n) not in have]
        if not new:
            continue
        for a in new:
            events.append({"type": "rail", "n": n, "outcome": a["outcome"], "code": a.get("code"), "on": a["on"],
                           "by": "Rail simulator", "simulated": True})
        case.extra = {**(case.extra or {}), "events": events}
        before = case.status
        run_case(db, case, actor)
        out.append({"case": case.id, "title": case.title, "answers": [a["outcome"] for a in new],
                    "from": before, "to": case.status})
    return out


# --------------------------------------------------------------------------- 2. ACH holds
def release_ach_holds(db: Session, actor: User | None) -> list[dict]:
    """Re-run completed ACH deposits whose funds are still restricted; each posts its release once the hold ends."""
    from .runner import run_case
    out = []
    for case in db.scalars(select(Case).where(Case.kind == "transfer", Case.status == "COMPLETED")).all():
        if (case.application or {}).get("type") != "ach_deposit":
            continue
        n = 1 + sum(1 for e in (case.extra or {}).get("events", []) if e.get("type") == "correction")
        if db.scalar(select(LedgerEntry.id).where(LedgerEntry.ref == f"C{case.id}:release-{n}")):
            continue
        run_case(db, case, actor)
        if db.scalar(select(LedgerEntry.id).where(LedgerEntry.ref == f"C{case.id}:release-{n}")):
            out.append({"case": case.id, "account": case.account_id,
                        "amount": float(case.application.get("amount") or 0)})
    return out


# --------------------------------------------------------------------------- 3. receipts
def _candidates_pool(db: Session) -> list[dict]:
    rows = []
    for a in db.scalars(select(Account).where(Account.status == "OPEN")).all():
        owners = [h.get("name") for h in (a.application or {}).get("holders", []) if h.get("name")]
        rows.append({"id": a.id, "name": a.display_name, "owners": owners})
    return rows


def receive(db: Session, *, channel: str, amount: float, sender: str, memo: str, on: date | None = None,
            actor: User | None = None) -> BankReceipt:
    """Money arrives at the settlement bank: book it to suspense at once, and find the accounts it might be for."""
    on = on or settings.today()
    r = BankReceipt(received_on=on, channel=channel, amount=round(amount, 2), sender=sender, memo=memo,
                    candidates=rules.match_receipt(memo, sender, _candidates_pool(db)))
    db.add(r)
    db.flush()
    ledger.post(db, f"RCPT-{r.id}", "receipt", f"{channel} received from {sender}: held in suspense until applied",
                gl_move("1010", "2190", CASH, units(r.amount)), on, actor=actor)
    audit.record(db, actor, "receipt_received", "bank_receipt", r.id, amount=r.amount, channel=channel)
    return r


def apply_receipt(db: Session, r: BankReceipt, account_id: str, actor: User | None, note: str = "",
                  automatic: bool = False) -> None:
    if r.status != "unapplied":
        raise HTTPException(409, "This receipt has already been applied or returned")
    a = db.get(Account, account_id)
    if a is None or a.status != "OPEN":
        raise HTTPException(422, "Apply it to an open account")
    firms = ledger.firm_of(db)
    legs = client_move(a.id, firms[a.id], CASH, units(r.amount), "settled", None) + \
        [Leg("gl", "2190", CASH, "settled", units(r.amount))]
    ledger.post(db, f"RCPT-{r.id}-APPLY", "receipt_applied", f"{r.channel} from {r.sender} applied to {a.id}",
                legs, settings.today(), actor=actor, account_id=a.id, firms=firms)
    r.status, r.account_id = "applied", a.id
    r.decided_by = "Settlement engine (STL-001: a sure match)" if automatic else (actor.display_name if actor else None)
    r.decided_at, r.note = utcnow(), note or None
    audit.record(db, actor, "receipt_applied", "bank_receipt", r.id, account=a.id, amount=r.amount,
                 automatic=automatic, note=note or None)


def return_receipt(db: Session, r: BankReceipt, actor: User, note: str) -> None:
    if r.status != "unapplied":
        raise HTTPException(409, "This receipt has already been applied or returned")
    if len((note or "").strip()) < 10:
        raise HTTPException(422, "Say why it's going back (at least 10 characters)")
    ledger.post(db, f"RCPT-{r.id}-RETURN", "receipt_returned", f"{r.channel} returned to {r.sender}",
                gl_move("2190", "1010", CASH, units(r.amount)), settings.today(), actor=actor)
    r.status, r.decided_by, r.decided_at, r.note = "returned", actor.display_name, utcnow(), note.strip()
    audit.record(db, actor, "receipt_returned", "bank_receipt", r.id, amount=r.amount, note=r.note)


def match_receipts(db: Session, actor: User | None) -> dict:
    applied, waiting, due_back = [], [], []
    pool = _candidates_pool(db)
    today = settings.today()
    for r in db.scalars(select(BankReceipt).where(BankReceipt.status == "unapplied").order_by(BankReceipt.id)).all():
        r.candidates = rules.match_receipt(r.memo, r.sender, pool)
        best = rules.auto_apply(r.candidates)
        if best:
            apply_receipt(db, r, best["account"], actor, f"Matched at {best['score']:.2f}: {'; '.join(best['why'])}",
                          automatic=True)
            applied.append({"receipt": r.id, "account": best["account"], "amount": r.amount})
        elif rules.return_due(r.received_on, today):
            due_back.append({"receipt": r.id, "amount": r.amount, "received_on": r.received_on.isoformat()})
        else:
            waiting.append({"receipt": r.id, "amount": r.amount,
                            "best": r.candidates[0] if r.candidates else None})
    return {"applied": applied, "waiting": waiting, "due_back": due_back}


def receipt_dict(r: BankReceipt, today: date) -> dict:
    from custody.calendar import bdays_between
    return {"id": r.id, "received_on": r.received_on.isoformat(), "channel": r.channel, "amount": r.amount,
            "sender": r.sender, "memo": r.memo, "status": r.status, "account_id": r.account_id,
            "candidates": r.candidates or [], "decided_by": r.decided_by,
            "decided_at": r.decided_at.isoformat() if r.decided_at else None, "note": r.note,
            "age_days": bdays_between(r.received_on, today),
            "return_due": r.status == "unapplied" and rules.return_due(r.received_on, today)}


# --------------------------------------------------------------------------- 5. net funding
def net_flows(db: Session, on: date) -> list[dict]:
    """Cash in and out of the settlement bank (GL 1010) for entries effective on a day, by channel."""
    rows = db.execute(select(LedgerEntry.kind, LedgerEntry.case_id, LedgerEntry.ref, LedgerLeg.units)
                      .join(LedgerLeg, LedgerLeg.entry_id == LedgerEntry.id)
                      .where(LedgerEntry.effective_date == on, LedgerLeg.layer == "gl", LedgerLeg.account == "1010",
                             LedgerLeg.asset == CASH)).all()
    kinds = {}
    items = []
    for kind, case_id, ref, u in rows:
        if kind.startswith("receipt"):
            r = db.get(BankReceipt, int(ref.split("-")[1]))
            channel = r.channel if r else "Bank"
        elif kind in ("opening_balance", "bank_fee", "recon_fix"):
            channel = "Other" if kind != "bank_fee" else "Bank fees"
        else:
            if case_id not in kinds:
                c = db.get(Case, case_id) if case_id else None
                kinds[case_id] = (c.application or {}).get("type") if c else None
            channel = CHANNEL.get(kinds[case_id], "Other")
        items.append({"channel": channel, "amount": round(u / 1_000_000, 2)})
    return rules.net_by_channel(items)


# --------------------------------------------------------------------------- 6. SLOA reconfirmations
def sloa_annual_notices(db: Session) -> list[dict]:
    """Each standing letter of authorization is reconfirmed with the client every year (custody MM-011): send the
    notice for each one due, and set the next due date."""
    from datetime import timedelta
    from .mail import sloa_annual_notice
    today = settings.today()
    out = []
    for a in db.scalars(select(Account).where(Account.status.in_(("OPEN", "OPEN_RESTRICTED")))).all():
        sloas = (a.application or {}).get("sloas") or []
        due = [x for x in sloas if x.get("status") == "active" and x.get("annual_due")
               and date.fromisoformat(x["annual_due"]) <= today]
        if not due:
            continue
        updated = []
        for x in sloas:
            if x in due:
                sent = sloa_annual_notice(db, a, x)
                out.append({"account": a.id, "sloa": x["id"], "notices": sent})
                x = {**x, "annual_due": (today + timedelta(days=365)).isoformat(),
                     "last_annual_notice": today.isoformat()}
            updated.append(x)
        a.application = {**a.application, "sloas": updated}
    return out


# --------------------------------------------------------------------------- the cycle
def _say(status: str) -> str:
    """A status as words: AWAITING_SETTLEMENT reads "awaiting settlement"."""
    return status.replace("_", " ").lower()


def run_cycle(db: Session, actor: User | None) -> SettlementRun:
    today = settings.today()
    delivered = deliver_rail_answers(db, actor)
    released = release_ach_holds(db, actor)
    receipts = match_receipts(db, actor)
    reconfirmed = sloa_annual_notices(db)
    proof = ledger.proof(db)
    flows = net_flows(db, today)
    net = round(sum(f["net"] for f in flows), 2)
    steps = [
        {"step": "Receive settlement activity: transfers, receipts", "outcome":
         f"{len(delivered)} transfer{'s' if len(delivered) != 1 else ''} got an answer from the rail",
         "detail": [f"#{d['case']} {d['title']}: {', '.join(d['answers'])} ({_say(d['from'])} to {_say(d['to'])})"
                    for d in delivered], "rule_ids": ["MM-008"]},
        {"step": "Release settled cash; hold returnable ACH funds as restricted", "outcome":
         f"{len(released)} ACH deposit{'s' if len(released) != 1 else ''} released",
         "detail": [f"#{r['case']} {r['account']}: ${r['amount']:,.2f}" for r in released], "rule_ids": ["MM-007"]},
        {"step": "Unapplied incoming cash: match wire memos and ACH addenda to accounts", "outcome":
         f"{len(receipts['applied'])} applied, {len(receipts['waiting'])} waiting for an analyst, "
         f"{len(receipts['due_back'])} due back to the sender", "detail":
         [f"Receipt {a['receipt']} applied to {a['account']}: ${a['amount']:,.2f}" for a in receipts["applied"]],
         "rule_ids": ["STL-001", "STL-002"]},
        {"step": "Check the invariant: clients = RIA master; RIA masters = GL", "outcome":
         "Balanced" if all(c["ok"] for c in proof["checks"]) else "Out of balance",
         "detail": [c["check"] for c in proof["checks"] if not c["ok"]], "rule_ids": ["LED-001"]},
        {"step": "Send net funding and settlement instructions", "outcome":
         f"{'Receive' if net >= 0 else 'Pay'} ${abs(net):,.2f} net at the settlement bank",
         "detail": [f"{f['channel']}: {f['direction'].lower()} ${abs(f['net']):,.2f}" for f in flows], "rule_ids": []},
        {"step": "Reconfirm standing letters of authorization with clients (annual notice)", "outcome":
         f"{len(reconfirmed)} due" if reconfirmed else "None due",
         "detail": [f"{r['account']} {r['sloa']}: {r['notices']} notice{'s' if r['notices'] != 1 else ''} sent"
                    for r in reconfirmed], "rule_ids": ["MM-011"]},
    ]
    run = SettlementRun(business_date=today, run_by=actor.display_name if actor else "system",
                        result={"steps": steps, "flows": flows, "net": net, "checks": proof["checks"],
                                "delivered": delivered, "released": released, "receipts": receipts})
    db.add(run)
    db.flush()
    audit.record(db, actor, "settlement_cycle_run", "settlement_run", run.id, business_date=today.isoformat(),
                 balanced=all(c["ok"] for c in proof["checks"]), net=net)
    return run


def run_dict(r: SettlementRun | None) -> dict | None:
    if r is None:
        return None
    return {"id": r.id, "business_date": r.business_date.isoformat(), "run_at": r.run_at.isoformat(),
            "run_by": r.run_by, **(r.result or {})}


def in_transit(db: Session) -> list[dict]:
    """Transfers in transit with what sits in the in-transit suspense accounts for each, oldest first."""
    today = settings.today()
    out = []
    for c in db.scalars(select(Case).where(Case.kind == "transfer", Case.status.in_(IN_TRANSIT))).all():
        legs = db.execute(select(LedgerLeg.account, LedgerLeg.asset, LedgerLeg.units)
                          .join(LedgerEntry, LedgerEntry.id == LedgerLeg.entry_id)
                          .where(LedgerEntry.case_id == c.id, LedgerLeg.layer == "gl",
                                 LedgerLeg.account.in_(("1590", "1020", "2200", "2210")))).all()
        held = defaultdict(int)
        for acct, asset, u in legs:
            held[(acct, asset)] += u
        summary = (c.result or {}).get("summary", {})
        expected = summary.get("Expected to settle")
        out.append({"case": c.id, "title": c.title, "status": c.status, "account": c.account_id,
                    "type": money.TYPES[c.application["type"]]["label"], "expected": expected,
                    "overdue": bool(expected) and date.fromisoformat(expected) < today,
                    "items": len([k for k, v in held.items() if v])})
    return sorted(out, key=lambda x: (x["expected"] or "9999", x["case"]))
