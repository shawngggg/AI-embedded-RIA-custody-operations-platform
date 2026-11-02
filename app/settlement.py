"""
The daily cash settlement cycle (map 12), run by the settlement team or the operations supervisor:

1. Receive settlement activity: the rail simulator (principle 8) delivers every answer due by today for transfers in
   transit (a validation, a confirmation, a reject, a return), and each case re-runs and posts what it settled.
2. Release returnable ACH funds whose hold ended (MM-007), by re-running the deposits.
3. Unapplied incoming cash: match each receipt in suspense to an account from its memo and sender (STL-001); a sure
   match applies itself, the rest wait for an analyst; money nobody can match goes back after 10 business days
   (STL-002), with an analyst confirming the return.
4. Check the three-layer invariant over every balance (LED-001).
5. Net the cash movement at the settlement bank by channel since the last instruction, and record the instruction as
   sent (simulated: there is no bank connection). A re-run sends only what posted since; the reserve deposit, which
   moves between the firm's own accounts, is shown apart from settlement funding (walkthrough note 14).

The cycle runs on business days (a day the Fed or NSCC is open). Run on a closed day, it runs as of the last business
day: the rails don't answer on a weekend, and the reserve is computed as of that day (walkthrough note 13).

Incoming wires and ACH credits arrive here as bank receipts. Each posts at once to the settlement bank and the suspense
account (2190), so the books always match the bank; applying it moves the money from suspense to the client.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from custody import money, settlement as rules
from custody.calendar import is_bday
from custody.ledger import CASH, Leg, client_move, gl_move, units

from . import audit, ledger
from .config import settings
from .orm import Account, BankReceipt, Case, LedgerEntry, LedgerLeg, SettlementRun, User, utcnow

IN_TRANSIT = ("AWAITING_SETTLEMENT", "AWAITING_REREGISTRATION", "OVERDUE")
RESERVE_KINDS = ("reserve_deposit", "reserve_withdrawal")   # between the firm's own accounts, not customer settlement


def is_settlement_day(d: date) -> bool:
    return is_bday(d, "fed") or is_bday(d, "nscc")


def business_date(today: date | None = None) -> date:
    """The cycle's business date: today on a business day, otherwise the last one (walkthrough note 13)."""
    d = today or settings.today()
    while not is_settlement_day(d):
        d -= timedelta(days=1)
    return d


def previous_business_date(d: date) -> date:
    return business_date(d - timedelta(days=1))
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
def _bank_legs(db: Session, through: date, since: date | None = None):
    """Settlement-bank cash legs (GL 1010) effective after `since` (or on `through` alone) through `through`."""
    q = (select(LedgerEntry.id, LedgerEntry.kind, LedgerEntry.case_id, LedgerEntry.ref, LedgerEntry.description,
                LedgerLeg.units)
         .join(LedgerLeg, LedgerLeg.entry_id == LedgerEntry.id)
         .where(LedgerLeg.layer == "gl", LedgerLeg.account == "1010", LedgerLeg.asset == CASH,
                LedgerEntry.effective_date <= through))
    q = q.where(LedgerEntry.effective_date > since) if since else q.where(LedgerEntry.effective_date == through)
    return db.execute(q.order_by(LedgerEntry.id)).all()


def net_flows(db: Session, on: date, *, since: date | None = None, exclude: set[int] | None = None,
              entries: list[int] | None = None) -> list[dict]:
    """Settlement funding: cash in and out of the settlement bank (GL 1010) by channel, for entries effective on a
    day (or after `since` through it), leaving out entries already instructed and the reserve bank moves. The ids of
    the entries counted go into `entries` when a list is given."""
    kinds = {}
    items = []
    for entry_id, kind, case_id, ref, _memo, u in _bank_legs(db, on, since):
        if kind in RESERVE_KINDS or (exclude and entry_id in exclude):
            continue
        if entries is not None and entry_id not in entries:
            entries.append(entry_id)
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


def reserve_moves(db: Session, on: date, since: date | None = None) -> list[dict]:
    """Moves between the settlement bank and the special reserve bank account: the firm's own money, apart from
    customer settlement funding."""
    return [{"ref": ref, "amount": round(-u / 1_000_000, 2), "kind": kind, "memo": memo}
            for _id, kind, _case, ref, memo, u in _bank_legs(db, on, since) if kind in RESERVE_KINDS]


def instructions(db: Session, on: date) -> list[dict]:
    """The funding instructions already sent for a business date, oldest first."""
    runs = db.scalars(select(SettlementRun).where(SettlementRun.business_date == on).order_by(SettlementRun.id)).all()
    return [r.result["instruction"] for r in runs if (r.result or {}).get("instruction")]


def _instructed_entries(db: Session, since: date) -> set[int]:
    out: set[int] = set()
    for r in db.scalars(select(SettlementRun).where(SettlementRun.business_date >= since)).all():
        out.update(((r.result or {}).get("instruction") or {}).get("entries") or [])
    return out


def _reported_reserve_moves(db: Session, since: date) -> set[str]:
    return {m["ref"] for r in db.scalars(select(SettlementRun).where(SettlementRun.business_date >= since)).all()
            for m in (r.result or {}).get("reserve_moves") or []}


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


def _fmt(d: date) -> str:
    return f"{d:%A, %b} {d.day}"


def run_cycle(db: Session, actor: User | None) -> SettlementRun:
    calendar_day = settings.today()
    today = business_date(calendar_day)              # a closed day runs as of the last business day (note 13)
    delivered = deliver_rail_answers(db, actor, through=today)
    released = release_ach_holds(db, actor)
    receipts = match_receipts(db, actor)
    reconfirmed = sloa_annual_notices(db)
    from .protection import check as protection_check
    protect = protection_check(db, actor, today)
    reserve, control = protect["reserve"], protect["possession"]
    proof = ledger.proof(db)
    since = previous_business_date(today)
    sent_before = instructions(db, today)
    counted: list[int] = []
    flows = net_flows(db, today, since=since, exclude=_instructed_entries(db, since), entries=counted)
    net = round(sum(f["net"] for f in flows), 2)
    instruction = None
    if counted:
        instruction = {"ref": f"SI-{today.isoformat()}-{len(sent_before) + 1}", "status": "sent",
                       "sent_at": utcnow().isoformat(), "simulated": True, "net": net, "flows": flows,
                       "entries": counted, "by": actor.display_name if actor else "system"}
    reported = _reported_reserve_moves(db, since)
    moves = [m for m in reserve_moves(db, today, since) if m["ref"] not in reported]     # new since the last cycle
    if instruction:
        funding_outcome = (f"{'Receive' if net >= 0 else 'Pay'} ${abs(net):,.2f} net at the settlement bank: "
                           f"instruction {instruction['ref']} sent")
    elif sent_before:
        funding_outcome = f"Nothing new since instruction {sent_before[-1]['ref']}: nothing sent"
    else:
        funding_outcome = "No cash moved through the settlement bank: nothing sent"
    funding_detail = [f"{f['channel']}: {f['direction'].lower()} ${abs(f['net']):,.2f}" for f in flows]
    funding_detail += [f"Already sent for {_fmt(today)}: {i['ref']}, {'receive' if i['net'] >= 0 else 'pay'} "
                       f"${abs(i['net']):,.2f}" for i in sent_before]
    if instruction:
        funding_detail.append("Recorded as sent; this demo has no settlement bank connection")
    closed = [] if calendar_day == today else [
        f"{_fmt(calendar_day)} isn't a business day: the cycle runs as of {_fmt(today)}, and the rails don't answer "
        "until the next business day"]
    steps = [
        {"step": "Receive settlement activity: transfers, receipts", "outcome":
         f"{len(delivered)} transfer{'s' if len(delivered) != 1 else ''} got an answer from the rail",
         "detail": closed + [f"#{d['case']} {d['title']}: {', '.join(d['answers'])} ({_say(d['from'])} to "
                             f"{_say(d['to'])})" for d in delivered], "rule_ids": ["MM-008"]},
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
        {"step": "Customer protection (Rule 15c3-3): the reserve formula and possession or control", "outcome":
         (f"Reserve deficit ${reserve['deficit']:,.2f}: deposit by {reserve['deposit_due']}" if reserve["deficit"]
          else f"Reserve covered (${reserve['excess']:,.2f} excess)")
         + (f"; {len(control['deficits'])} possession-or-control deficit"
            + ("s" if len(control["deficits"]) != 1 else "") if control["deficits"] else "; every security in control"),
         "detail": [f"Credits ${reserve['total_credits']:,.2f}; debits ${reserve['total_debits']:,.2f} after the 2% "
                    f"reduction; required ${reserve['required']:,.2f}; on deposit ${reserve['on_deposit']:,.2f}",
                    f"{control['in_control']} of {control['securities']} securities fully in control"]
                   + [f"{d['name']}: short {d['deficit']:,.4f}" for d in control["deficits"]],
         "rule_ids": ["CP-001", "CP-002"]},
        {"step": "Special reserve bank account: the firm's own money, apart from settlement funding", "outcome":
         (f"${sum(m['amount'] for m in moves):,.2f} moved from the settlement bank to the reserve bank account"
          if moves else "No reserve deposit or withdrawal"),
         "detail": [f"{m['ref']}: ${m['amount']:,.2f}" for m in moves], "rule_ids": ["CP-001"]},
        {"step": "Send net funding and settlement instructions (only what posted since the last one)",
         "outcome": funding_outcome, "detail": funding_detail, "rule_ids": []},
        {"step": "Reconfirm standing letters of authorization with clients (annual notice)", "outcome":
         f"{len(reconfirmed)} due" if reconfirmed else "None due",
         "detail": [f"{r['account']} {r['sloa']}: {r['notices']} notice{'s' if r['notices'] != 1 else ''} sent"
                    for r in reconfirmed], "rule_ids": ["MM-011"]},
    ]
    run = SettlementRun(business_date=today, run_by=actor.display_name if actor else "system",
                        result={"steps": steps, "flows": flows, "net": net, "checks": proof["checks"],
                                "protection": protect, "delivered": delivered, "released": released,
                                "receipts": receipts, "instruction": instruction, "reserve_moves": moves,
                                "run_on": calendar_day.isoformat()})
    db.add(run)
    db.flush()
    audit.record(db, actor, "settlement_cycle_run", "settlement_run", run.id, business_date=today.isoformat(),
                 run_on=calendar_day.isoformat(), balanced=all(c["ok"] for c in proof["checks"]), net=net,
                 instruction=instruction["ref"] if instruction else None)
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
