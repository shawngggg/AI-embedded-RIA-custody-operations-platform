"""
The reviewer walkthrough, timed (MVP gate: a first-time reviewer takes a case from application to open account in
under 5 minutes).

Start gives the reviewer a fresh synthetic application from an RIA (Bonneville Planning LLC) that arrived without
its LPOA; the RIA's LPOA has just come in through the portal. The reviewer, as operations (Jamie Ortiz), checks it and
records the item received, then, as a registered principal (Avery Collins), accepts the account. The clock runs from
Start to the account opening, measured by the server. Each finished run is kept, and survives a demo reset; the
gate's measure is the median of first-time runs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from statistics import median

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from applications import AccountApplication, Person
from models import AccountGroup

from . import audit
from .config import settings
from .orm import Account, AuditEvent, Case, Firm, User, utcnow

TARGET_SECONDS = 300
DAILY_CAP = 50
NAMES = ["Avery Lund", "Jordan Pace", "Riley Moss", "Casey Ward", "Morgan Hale", "Quinn Ashby", "Rowan Tate",
         "Harper Vale", "Emerson Cole", "Sage Whitley"]
STEPS = ["As Jamie Ortiz (operations): open the case, check the LPOA the RIA sent, and record it as received.",
         "Switch user to Avery Collins (RIA service, registered principal): confirm the review and accept the account."]


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def start(db: Session, user: User, first_time: bool) -> Case:
    from .documents import sample_document, store
    from .runner import run_case
    from .seed import create_account_case, seed_documents
    if not settings.demo_mode:
        raise HTTPException(403, "The timed walkthrough runs on the demo only")
    today = datetime.combine(settings.today(), datetime.min.time(), tzinfo=timezone.utc)
    started = db.scalar(select(func.count()).select_from(AuditEvent).where(
        AuditEvent.action == "walkthrough_started", AuditEvent.at >= today))
    if started >= DAILY_CAP:
        raise HTTPException(429, "Today's walkthroughs are used up; try again tomorrow")
    n = 1 + db.scalar(select(func.count()).select_from(Account).where(Account.id.like("ACC-W%")))
    while db.get(Account, f"ACC-W{n:03d}"):
        n += 1
    firm = db.scalar(select(Firm).where(Firm.crd == "310003"))
    advisor = db.scalar(select(User).where(User.username == "bonneville.advisor"))
    if firm is None or advisor is None:
        raise HTTPException(409, "The demo data isn't seeded; reset the demo first")
    app = AccountApplication(
        f"APP-W{n:03d}", f"ACC-W{n:03d}", AccountGroup.PURE_RIA, "individual",
        [Person(NAMES[n % len(NAMES)], "account_holder", date_of_birth="1984-05-20", identity_verified=True,
                address="210 S Main St, Logan, UT 84321", tax_id=f"900-00-{4000 + n}", occupation="Engineer")],
        lpoa_signed=False, fee_authorization=True, client_signature=True, tax_form="W-9",
        account_purpose="Retirement investing", source_of_funds="Salary", source_of_wealth="Salary and savings",
        expected_initial_funding=90_000)
    case = create_account_case(db, app, firm, advisor)
    seed_documents(db, case, advisor.display_name, "portal", user)   # the documents behind what arrived complete
    run_case(db, case, advisor)                                   # waits for the LPOA
    name, data = sample_document(db, case, "lpoa", settings.today())
    store(db, case, advisor, "lpoa", name, data, "portal", synthetic=True)   # the RIA's LPOA, waiting to be checked
    case.extra = {**(case.extra or {}), "walkthrough": {"started_at": utcnow().isoformat(), "first_time": first_time,
                                                         "by": user.display_name}}
    audit.record(db, user, "walkthrough_started", "case", case.id, case_id=case.id, first_time=first_time)
    return case


def finish_if_opened(db: Session, case: Case, status: str) -> None:
    """Called after every run of a case: the clock stops when a walkthrough's account opens."""
    w = (case.extra or {}).get("walkthrough")
    if not w or w.get("finished_at") or status not in ("OPEN", "OPEN_RESTRICTED"):
        return
    now = utcnow()
    seconds = int((now - _aware(datetime.fromisoformat(w["started_at"]))).total_seconds())
    case.extra = {**case.extra, "walkthrough": {**w, "finished_at": now.isoformat(), "seconds": seconds}}
    audit.record(db, None, "walkthrough_completed", "walkthrough", case.id, case_id=case.id, seconds=seconds,
                 first_time=w.get("first_time"), started_at=w["started_at"])


def state(case: Case) -> dict:
    w = (case.extra or {}).get("walkthrough")
    if not w:
        raise HTTPException(404, "Not a walkthrough case")
    return {"case_id": case.id, "status": case.status, "steps": STEPS, "target_seconds": TARGET_SECONDS, **w}


def summary(db: Session) -> dict:
    """The gate's measure: first-time runs, median time, and how many came in under 5 minutes."""
    runs = [e.detail for e in db.scalars(select(AuditEvent).where(AuditEvent.action == "walkthrough_completed"))]
    first = sorted(r["seconds"] for r in runs if r.get("first_time"))
    return {"runs": len(runs), "first_time_runs": len(first), "target_seconds": TARGET_SECONDS,
            "median_seconds": int(median(first)) if first else None,
            "under_target": sum(s <= TARGET_SECONDS for s in first),
            "met": bool(first) and median(first) <= TARGET_SECONDS}
