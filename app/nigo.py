"""
NIGO aging: a request waiting on the RIA or the client for items gets reminders, and closes if they never arrive.

Platform policy (the industry-practice review, Oct. 25, 2026): a reminder 7 and 14 days after the items were requested,
and the request withdrawn at 30 days, with a notice. An application or change that was never in good order shouldn't
sit open forever: it holds an account number, it skews the queue, and its documents age (an ID can expire, a
beneficial ownership certification goes stale). The RIA submits a new request when it has the items.

Runs in the nightly batch (app/nightly.py) and when a supervisor runs it by hand.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit
from .config import settings
from .orm import Account, Case, CaseTransition, Firm, Restriction, User, utcnow

REMIND_DAYS = (7, 14)
WITHDRAW_DAYS = 30
SOURCE = "Platform policy: reminders at 7 and 14 days; withdrawn at 30 days without the items"
# What "waiting on the requester" means for each kind
WAITING = {"account": ("PENDING_ITEMS",), "firm": ("PENDING_ITEMS",), "change": ("RETURNED",)}


def waiting_since(case: Case) -> date | None:
    return case.queue_entered_at.date() if case.queue_entered_at else None


def _items(case: Case) -> list[str]:
    return (case.result or {}).get("requested_items") or []


def _notify(db: Session, case: Case, template: str, subject: str, text: str) -> int:
    from .mail import PORTAL_URL, _recipients, queue
    name = case.title.split(": ", 1)[-1]
    sent = 0
    for u in _recipients(db, case):
        body = (f"Hello {u.display_name.split()[0]},\n\n{text}\n\nOpen it in the portal: {PORTAL_URL}/cases/{case.id}"
                "\n\nYour RIA service team")
        queue(db, to=u.email, to_name=u.display_name, subject=f"{subject}: {name}", body=body, template=template,
              case=case)
        sent += 1
    return sent


def withdraw(db: Session, case: Case, actor: User | None, days: int) -> None:
    """Close a request whose items never came: the case, the pending account or firm, and the case's codes."""
    previous, now = case.status, utcnow()
    case.status, case.queue, case.queue_entered_at, case.sla_due = "WITHDRAWN", None, None, None
    case.assigned_to = case.assigned_at = None
    case.closed_at = case.updated_at = now
    result = dict(case.result or {})
    result["steps"] = list(result.get("steps") or []) + [
        {"lane": "Custody operations", "step": "NIGO aging: the items didn't arrive", "outcome": "Withdrawn",
         "detail": [f"Requested {days} days ago: " + ("; ".join(_items(case)) or "items"), SOURCE,
                    "The requester was told; a new request starts over"], "rule_ids": []}]
    case.result = result
    db.add(CaseTransition(case_id=case.id, from_status=previous, to_status="WITHDRAWN", at=now))
    if case.kind == "account" and case.account_id:
        account = db.get(Account, case.account_id)
        if account is not None and account.status in ("PENDING", "RETURNED"):
            account.status = "WITHDRAWN"
    if case.kind == "firm" and case.firm_id:
        firm = db.get(Firm, case.firm_id)
        if firm is not None and firm.status == "ONBOARDING":
            firm.status = "WITHDRAWN"
    for r in db.scalars(select(Restriction).where(Restriction.case_id == case.id, Restriction.active.is_(True))):
        r.active, r.removed_at, r.removed_by = False, now, actor.display_name if actor else "system"
        r.removal_reason = "The request was withdrawn: the items didn't arrive"
    audit.record(db, actor, "case_withdrawn", "case", case.id, case_id=case.id, days_waiting=days,
                 items=_items(case), basis=SOURCE)
    _notify(db, case, "withdrawn", "Request closed",
            f"We closed this request because the items we asked for {days} days ago didn't arrive:\n"
            + "\n".join(f"- {i}" for i in _items(case)) + "\n\nWhen you have them, submit a new request.")


def run(db: Session, actor: User | None, today: date | None = None) -> dict:
    """Send the reminders that are due and withdraw what has waited 30 days."""
    today = today or settings.today()
    reminded, withdrawn = [], []
    kinds = list(WAITING)
    for case in db.scalars(select(Case).where(Case.kind.in_(kinds)).order_by(Case.id)).all():
        if case.status not in WAITING[case.kind]:
            continue
        since = waiting_since(case)
        if since is None:
            continue
        days = (today - since).days
        if days >= WITHDRAW_DAYS:
            withdraw(db, case, actor, days)
            withdrawn.append({"case": case.id, "title": case.title, "days": days})
            continue
        sent = list((case.extra or {}).get("reminders") or [])
        due = [d for d in REMIND_DAYS if days >= d and d not in [r["day"] for r in sent]]
        if not due:
            continue
        day = max(due)
        close_on = since + timedelta(days=WITHDRAW_DAYS)
        _notify(db, case, "items_reminder", "Reminder: items still needed",
                f"We're still waiting for the items we asked for {days} days ago:\n"
                + "\n".join(f"- {i}" for i in _items(case))
                + f"\n\nIf they haven't arrived by {close_on:%B} {close_on.day}, we'll close the request.")
        sent += [{"day": d, "on": today.isoformat()} for d in due]
        case.extra = {**(case.extra or {}), "reminders": sent}
        from .runner import run_case
        run_case(db, case, actor)                    # the case's steps show the reminders
        audit.record(db, actor, "items_reminder_sent", "case", case.id, case_id=case.id, days_waiting=days,
                     closes_on=close_on.isoformat())
        reminded.append({"case": case.id, "title": case.title, "days": days, "closes_on": close_on.isoformat()})
    db.flush()                          # the session doesn't autoflush; later queries must see the changes
    return {"on": today.isoformat(), "reminded": reminded, "withdrawn": withdrawn}
